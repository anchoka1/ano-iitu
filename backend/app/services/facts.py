"""«Слухи и факты»: шкала вердиктов «Правды», счётчик «проверяли N раз», проверка модератором.

Шкала: Подтверждено / Опровергнуто / Частично верно / Не подтверждено / На проверке.
  green → Подтверждено, red → Опровергнуто, yellow → Частично верно (или «Это мнение»),
  unknown → Не подтверждено; если слух отправлен модератору и ещё не решён → На проверке.

Один и тот же слух узнаём по «отпечатку» (claim_key): набор основ значимых слов без порядка.
«МУИТ сносят» и «сносят МУИТ!!!» — один отпечаток.

Сценарий: «Правда» не нашла подтверждений → автор жмёт «Отправить модератору» → заявка в очереди
(services/moderation.py) → модератор выносит вердикт → карточка автора обновляется, автору приходит
сообщение, слух появляется в ленте «Слухи и факты» и в базе знаний (rag/community.py) — следующий,
кто спросит то же самое, сразу получит готовый вердикт.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select

from backend.app.cards.schema import Reason, SourceRef, VerdictCard
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import Check
from backend.app.db.models_campus import Post
from backend.app.i18n import t
from backend.app.rag.store import tokenize
from backend.app.security import crypto
from backend.app.security.masking import mask_sensitive
from backend.app.services import anon


class FactsError(Exception):
    pass


# Слова-«обёртки» слуха: от них смысл утверждения не меняется («говорят, что…», «правда ли…», «срочно!»).
FILLER = set(tokenize(
    "говорят говорит говорили слышал слышала слышали пишут написали сказали сказал сказала якобы будто вроде кажется "
    "точно реально правда ли слух слухи срочно внимание разошлите перешлите всем вообще оказывается кстати народ ребята"
))


def claim_key(text: str) -> str:
    stems = sorted(set(tokenize(text or "")) - FILLER)
    if len(stems) < 2:
        return ""
    return hashlib.sha1(" ".join(stems[:40]).encode()).hexdigest()


def times_checked(session, key: str) -> int:
    if not key:
        return 0
    return session.scalar(select(func.count(func.distinct(Check.user_id))).where(Check.claim_key == key, Check.mode == "pravda")) or 0


def rumor_for_key(session, key: str) -> Post | None:
    """Слух на проверке или уже с вердиктом (одобренный — приоритетнее)."""
    if not key:
        return None
    rows = list(session.scalars(select(Post).where(Post.kind == "rumor", Post.status.in_(("approved", "pending")))
                                .order_by(Post.created_at.desc())))
    matches = [p for p in rows if json.loads(p.data_json or "{}").get("claim_key") == key]
    approved = [p for p in matches if p.status == "approved"]
    return (approved or matches or [None])[0]


def label_code(status: str, kind: str = "", review: str = "") -> str:
    if review == "pending":
        return "pending"
    if status == "green":
        return "confirmed"
    if status == "red":
        return "refuted"
    if status == "yellow":
        return "opinion" if kind == "opinion" else "partly"
    return "unconfirmed"


def truth_info(session, check: Check, viewer_id: int | None = None) -> dict:
    """Шкала вердикта, счётчик и состояние проверки модератором — для карточки «Правды»."""
    from backend.app.core.engine import card_from_json

    card = card_from_json(check.card_json)
    key = check.claim_key or claim_key(check.input_text)
    rumor = rumor_for_key(session, key)
    review: dict | None = None
    if rumor is not None:
        data = json.loads(rumor.data_json or "{}")
        review = {"post_id": rumor.id, "status": rumor.status, "verdict": data.get("verdict", ""), "comment": data.get("comment", ""),
                  "source_url": data.get("source_url", ""), "decided_on": data.get("decided_on", "")}
    if review and review["status"] == "approved" and review["verdict"]:
        code = review["verdict"]
    else:
        code = label_code(card.status, card.kind, review["status"] if review else "")
    can_escalate = (viewer_id == check.user_id and review is None and card.kind != "support"
                    and code in ("unconfirmed", "partly"))
    return {"code": code, "label": t(f"truth.{code}"), "times_checked": times_checked(session, key), "review": review,
            "can_escalate": can_escalate}


def ready_verdict(key: str) -> VerdictCard | None:
    """Модератор уже вынес вердикт по этому слуху — показываем его сразу, без новой проверки."""
    with get_sessionmaker()() as session:
        rumor = rumor_for_key(session, key)
        if rumor is None or rumor.status != "approved":
            return None
        data = json.loads(rumor.data_json or "{}")
    verdict = data.get("verdict")
    if not verdict:
        return None
    status = {"confirmed": "green", "refuted": "red", "partly": "yellow"}.get(verdict, "unknown")
    card = VerdictCard(
        status=status, kind="fact" if status != "unknown" else "insufficient", confidence=90 if status != "unknown" else 50,
        title=t("truth.mod_title", verdict=t(f"truth.{verdict}")),
        reasons=[Reason(text=data["comment"])] if data.get("comment") else [Reason(text=t("truth.mod_reason"))],
        sources=[SourceRef(title=t("truth.mod_source"), url=data.get("source_url", ""), date=data.get("decided_on", ""))],
        do=[t("truth.do_feed")], dont=[t("truth.dont_forward")] if status in ("red", "unknown") else [],
    )
    return card


async def escalate(check_id: int, user_id: int, user_name: str = "") -> dict:
    """«Отправить на проверку модератору». Тот же слух уже ждёт — присоединяемся к заявке (тоже получим ответ)."""
    from backend.app.core.engine import card_from_json
    from backend.app.core.features import enabled
    from backend.app.services.moderation import announce

    if not enabled("fact_feed"):
        raise FactsError(t("cm.feature_off"))
    with get_sessionmaker()() as session:
        check = session.get(Check, check_id)
        if check is None or check.user_id != user_id:
            raise FactsError(t("truth.err.not_yours"))
        if check.mode != "pravda":
            raise FactsError(t("truth.err.mode"))
        card = card_from_json(check.card_json)
        key = check.claim_key or claim_key(check.input_text)
        if not key:
            raise FactsError(t("truth.err.short"))
        existing = rumor_for_key(session, key)
        if existing is not None:
            data = json.loads(existing.data_json or "{}")
            watchers = data.get("_watchers", [])
            mine = anon.author_hash(user_id)
            if existing.author_hash != mine and mine not in data.get("_watcher_hashes", []):
                watchers.append(crypto.encrypt_text(str(user_id)))
                data["_watchers"] = watchers[-200:]
                data["_watcher_hashes"] = (data.get("_watcher_hashes", []) + [mine])[-200:]
                existing.data_json = json.dumps(data, ensure_ascii=False)
                session.commit()
            return {"post_id": existing.id, "status": existing.status, "joined": True}
        post = Post(
            kind="rumor", author_id=None, author_hash=anon.author_hash(user_id), title=mask_sensitive(check.input_text)[:256],
            body="", status="pending", notify_enc=crypto.encrypt_text(str(user_id)),
            data_json=json.dumps({"check_id": check.id, "claim_key": key, "bot_status": card.status}, ensure_ascii=False),
        )
        session.add(post)
        if not check.claim_key:
            check.claim_key = key
        session.commit()
        post_id = post.id
    await announce(f"post:{post_id}")
    return {"post_id": post_id, "status": "pending", "joined": False}


def watchers(post_data: dict) -> list[int]:
    out = []
    for enc in post_data.get("_watchers", []):
        value = crypto.decrypt_text(enc)
        if value.isdigit():
            out.append(int(value))
    return out


def feed(user_id: int | None = None, limit: int = 50) -> list[dict]:
    """Лента «Слухи и факты»: слухи с вердиктом модератора, новые сверху, с числом проверок."""
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.kind == "rumor", Post.status == "approved")
                               .order_by(Post.moderated_at.desc(), Post.created_at.desc()).limit(limit))
        out = []
        for p in rows:
            data = json.loads(p.data_json or "{}")
            out.append({"id": p.id, "claim": p.title, "verdict": data.get("verdict", "unconfirmed"),
                        "label": t(f"truth.{data.get('verdict', 'unconfirmed')}"), "comment": data.get("comment", ""),
                        "source_url": data.get("source_url", ""), "decided_on": data.get("decided_on", ""),
                        "times_checked": times_checked(session, data.get("claim_key", ""))})
        return out

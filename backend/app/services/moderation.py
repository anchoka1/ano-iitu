"""Модерация: одна очередь для всего, что ждёт проверки, решения, журнал, уведомления автору.

Что попадает в очередь:
  post:ID   — новая публикация с модерацией (Радар, вопрос старшекурснику, слух на проверку, потеряшка, ...);
  report:ID — публикация с жалобами (скрыть или оставить);
  app:ID    — заявка: ментор, подтверждение преподавателя, новый хаб.

Где модерируют:
  - Mini App → Профиль → «Модерация» (видно только модераторам; права проверяет сервер);
  - служебный чат модераторов (MOD_CHAT_ID) или личка модераторов — кнопками под сообщением;
  - команды бота: /mod, /reject, /edit, /modlog.

После решения:
  - автор получает сообщение в боте (для анонимных заявок — по зашифрованному «обратному адресу»);
  - запись в журнале (кто, что, когда, причина);
  - одобренный Радар уходит подписчикам и сразу попадает в базу знаний «Правды» и «Развода»;
  - вердикт модератора по слуху обновляет карточку автора и появляется в ленте «Слухи и факты».
"""

from __future__ import annotations

import html
import json
import logging

from sqlalchemy import func, select

from backend.app.core.config import get_settings
from backend.app.core.notify import get_notifier
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import iso_utc, utcnow
from backend.app.db.models_campus import Application, Hub, Post, Report
from backend.app.db.models_mod import ModLog
from backend.app.i18n import t
from backend.app.security import crypto
from backend.app.security.masking import mask_sensitive
from backend.app.services import roles

log = logging.getLogger(__name__)

REJECT_REASONS = ("spam", "details", "personal", "offtopic", "duplicate", "rules")
VERDICTS = ("confirmed", "refuted", "partly", "unconfirmed")
# Вердикт модератора → цвет карточки (как у «Правды»).
VERDICT_STATUS = {"confirmed": "green", "refuted": "red", "partly": "yellow", "unconfirmed": "unknown"}


class ModerationError(Exception):
    pass


def _data(post: Post) -> dict:
    try:
        return json.loads(post.data_json or "{}")
    except ValueError:
        return {}


HIDDEN_FIELDS = {"check_id", "claim_key", "bot_status", "hub_ids", "verdict", "comment", "source_url", "decided_on"}


def details(data: dict) -> list[dict]:
    """Поля заявки для модератора — с человеческими подписями, без служебных ключей."""
    out = []
    for key, value in data.items():
        if key.startswith("_") or key in HIDDEN_FIELDS or value in ("", None):
            continue
        label = t(f"mod.field.{key}")
        out.append({"label": label if label != f"mod.field.{key}" else key, "value": str(value)[:500]})
    return out


def reason_text(code_or_text: str) -> str:
    """Готовая причина по коду (spam, details...) или свой текст модератора."""
    if code_or_text in REJECT_REASONS:
        return t(f"mod.reason.{code_or_text}")
    return " ".join((code_or_text or "").split())[:500]


# ------------------------------------------------------------------ очередь


def _post_item(session, post: Post, reports: int = 0, reasons: list[str] | None = None) -> dict:
    from backend.app.services.community import KINDS

    cfg = KINDS.get(post.kind, {})
    data = {k: v for k, v in _data(post).items() if not k.startswith("_")}
    hub = session.get(Hub, post.hub_id) if post.hub_id else None
    item = {
        "key": f"{'report' if reports else 'post'}:{post.id}", "type": "report" if reports else "post", "id": post.id,
        "kind": post.kind, "kind_label": t(f"cm.kind.{post.kind}"), "title": post.title, "body": post.body, "data": data,
        "anon": bool(cfg.get("anon")), "author_name": "" if cfg.get("anon") else post.author_name, "hub": hub.title if hub else "",
        "status": post.status, "created_at": iso_utc(post.created_at), "reports": reports, "report_reasons": reasons or [],
        "needs_verdict": post.kind == "rumor" and not reports, "details": details(data),
    }
    if post.kind == "rumor":
        item["claim"] = _rumor_context(session, data)
    if post.kind == "senior_a" and post.parent_id and (q := session.get(Post, post.parent_id)):
        item["parent_title"] = q.title
    return item


def _rumor_context(session, data: dict) -> dict:
    """Что бот нашёл сам: его вердикт и источники — модератору проще решить."""
    from backend.app.core.engine import card_from_json
    from backend.app.db.models import Check

    check = session.get(Check, int(data.get("check_id") or 0)) if data.get("check_id") else None
    out = {"times_checked": 0, "bot_title": "", "bot_status": "", "sources": []}
    if check is not None:
        card = card_from_json(check.card_json)
        out.update(bot_title=card.title, bot_status=card.status, sources=[s.model_dump() for s in card.sources][:5])
    if data.get("claim_key"):
        out["times_checked"] = session.scalar(select(func.count(func.distinct(Check.user_id))).where(Check.claim_key == data["claim_key"])) or 0
    return out


def _app_item(session, app: Application) -> dict:
    data = json.loads(app.data_json or "{}")
    hub_titles = [h.title for h in session.scalars(select(Hub).where(Hub.id.in_(data.get("hub_ids", []) or [0])))]
    fields = {k: v for k, v in data.items() if k != "hub_ids" and v}
    return {"key": f"app:{app.id}", "type": "app", "id": app.id, "kind": app.kind, "kind_label": t(f"mod.app.{app.kind}"),
            "title": app.name, "body": "", "data": fields, "details": details(fields), "hubs": hub_titles, "author_name": "",
            "anon": False, "status": app.status, "created_at": iso_utc(app.created_at), "reports": 0, "needs_verdict": False}


def queue(moderator_id: int, limit: int = 100) -> list[dict]:
    roles.require_moderator(moderator_id)
    with get_sessionmaker()() as session:
        items = [_post_item(session, p) for p in session.scalars(select(Post).where(Post.status == "pending").order_by(Post.created_at).limit(limit))]
        reported = session.execute(select(Report.post_id, func.count()).where(Report.resolved.is_(False)).group_by(Report.post_id).limit(limit)).all()
        for post_id, count in reported:
            post = session.get(Post, post_id)
            if post is not None and post.status != "pending":
                reasons = [r.reason for r in session.scalars(select(Report).where(Report.post_id == post_id, Report.resolved.is_(False))) if r.reason]
                items.append(_post_item(session, post, count, reasons))
        items += [_app_item(session, a) for a in session.scalars(select(Application).where(Application.status == "pending").order_by(Application.created_at))]
    items.sort(key=lambda x: x["created_at"] or "")
    return items


def queue_count() -> int:
    with get_sessionmaker()() as session:
        posts = session.scalar(select(func.count()).select_from(Post).where(Post.status == "pending")) or 0
        reports = session.scalar(select(func.count(func.distinct(Report.post_id))).where(Report.resolved.is_(False))) or 0
        apps = session.scalar(select(func.count()).select_from(Application).where(Application.status == "pending")) or 0
    return posts + reports + apps


def get_item(moderator_id: int, key: str) -> dict:
    roles.require_moderator(moderator_id)
    kind, _, raw = key.partition(":")
    if not raw.isdigit():
        raise ModerationError(t("mod.err.not_found"))
    with get_sessionmaker()() as session:
        if kind == "app":
            app = session.get(Application, int(raw))
            if app is None:
                raise ModerationError(t("mod.err.not_found"))
            return _app_item(session, app)
        post = session.get(Post, int(raw))
        if post is None:
            raise ModerationError(t("mod.err.not_found"))
        reports = session.scalar(select(func.count()).select_from(Report).where(Report.post_id == post.id, Report.resolved.is_(False))) or 0
        return _post_item(session, post, reports if kind == "report" else 0)


def journal(moderator_id: int, limit: int = 100) -> list[dict]:
    roles.require_moderator(moderator_id)
    with get_sessionmaker()() as session:
        rows = session.scalars(select(ModLog).order_by(ModLog.created_at.desc()).limit(limit))
        return [{"id": r.id, "moderator": r.moderator_name or str(r.moderator_id), "target": r.target, "kind": r.kind,
                 "kind_label": t(f"cm.kind.{r.kind}") if r.target.startswith(("post", "report")) else t(f"mod.app.{r.kind}") if r.target.startswith("app") else r.kind,
                 "action": r.action, "action_label": t(f"mod.action.{r.action}"), "reason": r.reason, "created_at": iso_utc(r.created_at)} for r in rows]


# ------------------------------------------------------------------ решения


async def decide(moderator_id: int, moderator_name: str, key: str, action: str, reason: str = "", title: str | None = None,
                 body: str | None = None, verdict: str = "", comment: str = "", source_url: str = "") -> dict:
    """Решение модератора. action: approve | reject | edit | hide | keep. Для слухов approve требует verdict."""
    roles.require_moderator(moderator_id)
    kind, _, raw = key.partition(":")
    if not raw.isdigit():
        raise ModerationError(t("mod.err.not_found"))
    if kind == "app":
        return await _decide_app(moderator_id, moderator_name, int(raw), action, reason)
    if kind not in ("post", "report"):
        raise ModerationError(t("mod.err.not_found"))
    return await _decide_post(moderator_id, moderator_name, int(raw), kind, action, reason, title, body, verdict, comment, source_url)


def _log(session, moderator_id: int, name: str, target: str, kind: str, action: str, reason: str = "") -> None:
    session.add(ModLog(moderator_id=moderator_id, moderator_name=(name or "")[:128], target=target, kind=kind, action=action, reason=(reason or "")[:512]))


async def _decide_post(moderator_id, moderator_name, post_id, kind, action, reason, title, body, verdict, comment, source_url) -> dict:
    after: dict = {}
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None:
            raise ModerationError(t("mod.err.not_found"))
        if kind == "report":
            if action not in ("hide", "keep"):
                raise ModerationError(t("mod.err.action"))
            unresolved = list(session.scalars(select(Report).where(Report.post_id == post_id, Report.resolved.is_(False))))
            if not unresolved:
                raise ModerationError(t("mod.err.done"))
            for r in unresolved:
                r.resolved = True
            post.status = "hidden" if action == "hide" else "approved"
            post.moderated_at, post.moderated_by = utcnow(), moderator_id
            if action == "hide":
                post.reject_reason = reason_text(reason) or t("mod.reason.reports")
            _log(session, moderator_id, moderator_name, f"report:{post_id}", post.kind, action, post.reject_reason if action == "hide" else "")
            session.commit()
            if action == "hide":
                after = _author_message(post, "hidden")
            status = post.status
        else:
            if post.status != "pending":
                raise ModerationError(t("mod.err.done"))
            if action not in ("approve", "reject", "edit"):
                raise ModerationError(t("mod.err.action"))
            data = _data(post)
            if action == "reject":
                text = reason_text(reason)
                if len(text) < 3:
                    raise ModerationError(t("mod.err.reason"))
                post.status, post.reject_reason = "rejected", text
                _log(session, moderator_id, moderator_name, f"post:{post_id}", post.kind, "reject", text)
            else:
                if action == "edit":
                    if title is not None:
                        post.title = mask_sensitive(" ".join(title.split())[:256])
                    if body is not None:
                        post.body = mask_sensitive(body.strip()[:4000])
                    if len(post.title) < 3 and len(post.body) < 3:
                        raise ModerationError(t("cm.post.err.empty"))
                if post.kind == "rumor":
                    if verdict not in VERDICTS:
                        raise ModerationError(t("mod.err.verdict"))
                    if source_url and not source_url.startswith(("https://", "http://")):
                        raise ModerationError(t("cm.post.err.url"))
                    data.update(verdict=verdict, comment=" ".join((comment or "").split())[:1000], source_url=source_url.strip()[:500],
                                decided_on=_local_today())
                    post.data_json = json.dumps(data, ensure_ascii=False)
                post.status = "approved"
                _log(session, moderator_id, moderator_name, f"post:{post_id}", post.kind, "edit" if action == "edit" else ("verdict" if post.kind == "rumor" else "approve"),
                     t(f"truth.{verdict}") if post.kind == "rumor" else "")
            post.moderated_at, post.moderated_by = utcnow(), moderator_id
            session.commit()
            status = post.status
            after = _author_message(post, status)
            after["post"] = {"id": post.id, "kind": post.kind, "title": post.title, "body": post.body, "hub_id": post.hub_id, "data": data,
                             "author_hash": post.author_hash}
        if post.kind != "senior_q" and post.status in ("approved", "rejected", "hidden"):
            post.notify_enc = ""  # адрес больше не нужен (для вопросов — нужен, чтобы сообщать о новых ответах)
            if post.kind == "rumor":
                clean = {k: v for k, v in _data(post).items() if k not in ("_watchers", "_watcher_hashes")}
                post.data_json = json.dumps(clean, ensure_ascii=False)
            session.commit()
    await _after_post(after, status)
    return {"key": f"{kind}:{post_id}", "status": status}


def _author_message(post: Post, status: str) -> dict:
    """Кому и что написать автору (адрес берём из author_id или расшифровываем notify_enc)."""
    to = post.author_id or (int(v) if (v := crypto.decrypt_text(post.notify_enc)).isdigit() else None)
    if not to:
        return {}
    e = html.escape
    label = t(f"cm.kind.{post.kind}")
    what = e(post.title[:200] or post.body[:200])
    if status == "approved" and post.kind == "rumor":
        data = _data(post)
        text = t("mod.notify.rumor", what=what, verdict=e(t(f"truth.{data.get('verdict', 'unconfirmed')}")),
                 comment=e(data.get("comment") or "—"))
    elif status == "approved":
        text = t("mod.notify.approved", kind=e(label), what=what)
    elif status == "hidden":
        text = t("mod.notify.hidden", kind=e(label), what=what, reason=e(post.reject_reason))
    else:
        text = t("mod.notify.rejected", kind=e(label), what=what, reason=e(post.reject_reason))
    return {"to": to, "text": text, "post_id": post.id}


async def _after_post(after: dict, status: str) -> None:
    notifier = get_notifier()
    post = after.get("post") or {}
    if notifier and after.get("to"):
        await notifier.send(after["to"], after["text"])
    if notifier and post.get("kind") == "rumor" and after.get("text"):
        from backend.app.services.facts import watchers

        for uid in watchers(post.get("data") or {}):
            if uid != after.get("to"):
                await notifier.send(uid, after["text"])
    if status != "approved" or not post:
        return
    if post["kind"] in ("radar", "rumor"):
        from backend.app.rag.community import invalidate

        invalidate()
    if post["kind"] == "radar":
        await _broadcast_radar(post)
    elif post["kind"] == "rumor":
        _apply_rumor_verdict(post)
    elif post["kind"] == "senior_q" and post.get("hub_id"):
        await _notify_hub_mentors(post)


async def _broadcast_radar(post: dict) -> int:
    from backend.app.services import prefs

    notifier = get_notifier()
    if notifier is None:
        return 0
    text = t("cm.radar.alert_html", title=html.escape(post["title"]), body=html.escape(post["body"][:800]))
    sent = 0
    from backend.app.services import anon

    for uid in prefs.subscribers("sub.radar"):
        if anon.author_hash(uid) == post.get("author_hash"):
            continue  # автору — отдельное сообщение о публикации
        sent += await notifier.send(uid, text)
    log.info("Радар: предупреждение разослано %d подписчикам", sent)
    return sent


def _apply_rumor_verdict(post: dict) -> None:
    """Вердикт модератора — в карточку проверки, с которой слух отправили (автор видит обновление)."""
    from backend.app.cards.schema import Reason, SourceRef
    from backend.app.core.engine import card_from_json
    from backend.app.db.models import Check

    data = post.get("data") or {}
    verdict = data.get("verdict", "unconfirmed")
    with get_sessionmaker()() as session:
        check = session.get(Check, int(data.get("check_id") or 0)) if data.get("check_id") else None
        if check is None:
            return
        card = card_from_json(check.card_json)
        card.status = VERDICT_STATUS[verdict]
        card.kind = "fact" if verdict in ("confirmed", "refuted", "partly") else "insufficient"
        card.title = t("truth.mod_title", verdict=t(f"truth.{verdict}"))
        card.reasons = [Reason(text=data.get("comment") or t("truth.mod_reason"))]
        card.confidence = 90 if verdict in ("confirmed", "refuted") else 70 if verdict == "partly" else 50
        card.sources = ([SourceRef(title=t("truth.mod_source"), url=data["source_url"], date=data.get("decided_on", ""))]
                        if data.get("source_url") else []) + [s for s in card.sources if s.url][:2]
        card.do = [t("truth.do_feed")]
        card.dont = [t("truth.dont_forward")] if verdict in ("refuted", "unconfirmed") else []
        card.notes = []
        check.status = card.status
        check.card_json = card.model_dump_json()
        session.commit()


async def _notify_hub_mentors(post: dict) -> None:
    from backend.app.db.models_campus import HubMember

    notifier = get_notifier()
    if notifier is None:
        return
    with get_sessionmaker()() as session:
        hub = session.get(Hub, post["hub_id"])
        mentors = [m.user_id for m in session.scalars(select(HubMember).where(HubMember.hub_id == post["hub_id"], HubMember.role.in_(("mentor", "teacher"))))]
    for uid in mentors:
        await notifier.send(uid, t("mod.notify.new_question", hub=html.escape(hub.title if hub else ""), what=html.escape(post["title"][:300])))


async def _decide_app(moderator_id, moderator_name, app_id, action, reason) -> dict:
    from backend.app.services import hubs

    if action not in ("approve", "reject"):
        raise ModerationError(t("mod.err.action"))
    text = reason_text(reason) if action == "reject" else ""
    try:
        result = await hubs.decide(app_id, moderator_id, action == "approve", reason=text)
    except hubs.HubError as exc:
        raise ModerationError(str(exc)) from exc
    with get_sessionmaker()() as session:
        app = session.get(Application, app_id)
        _log(session, moderator_id, moderator_name, f"app:{app_id}", app.kind if app else "", action, text)
        session.commit()
    return {"key": f"app:{app_id}", "status": result["status"]}


# ------------------------------------------------------------------ сообщения модераторам


def item_html(item: dict) -> str:
    """Текст заявки для чата модераторов (весь пользовательский текст экранирован)."""
    e = html.escape
    head = t("mod.title.report") if item["type"] == "report" else t("mod.title.new")
    lines = [f"<b>{e(head)}</b> · {e(item['kind_label'])} · <code>{e(item['key'])}</code>"]
    if item.get("hub"):
        lines.append(e(t("mod.field.hub", hub=item["hub"])))
    if item.get("parent_title"):
        lines.append(e(t("mod.field.parent", title=item["parent_title"][:200])))
    if item.get("title"):
        lines.append(f"<b>{e(item['title'][:300])}</b>")
    if item.get("body"):
        lines.append(e(item["body"][:1500]))
    if item.get("details"):
        lines.append("<i>" + e("; ".join(f"{d['label']}: {d['value']}" for d in item["details"]))[:600] + "</i>")
    if item.get("hubs"):
        lines.append(e(t("mod.field.hubs") + ": " + ", ".join(item["hubs"])))
    if claim := item.get("claim"):
        lines.append(e(t("mod.claim_info", n=claim["times_checked"], bot=claim["bot_title"] or "—")))
        for s in claim["sources"][:3]:
            lines.append(f"• {e(s.get('title', ''))} {e(s.get('url', ''))}")
    if item["reports"]:
        lines.append(e(t("mod.reports", n=item["reports"], reason="; ".join(item.get("report_reasons") or []) or "—")))
    if not item.get("anon") and item.get("author_name"):
        lines.append(e(t("mod.field.author", name=item["author_name"])))
    if item["type"] == "post":
        lines.append(f"<i>{e(t('mod.chat_hint', target=item['key']))}</i>")
    return "\n".join(lines)


def item_buttons(item: dict, step: str = "main") -> list[list[tuple[str, str]]]:
    """Кнопки под заявкой. callback_data: mq:<действие>:<key>[:<параметр>] (до 64 байт)."""
    key = item["key"]
    if item["type"] == "report":
        return [[(t("mod.btn.hide"), f"mq:h:{key}"), (t("mod.btn.keep"), f"mq:k:{key}")]]
    if step == "reasons":
        rows = [[(t(f"mod.reason.{code}.short"), f"mq:r:{key}:{code}")] for code in REJECT_REASONS]
        rows.append([(t("mod.btn.back"), f"mq:b:{key}")])
        return rows
    if item.get("needs_verdict"):
        return [[(t("truth.btn.confirmed"), f"mq:v:{key}:confirmed"), (t("truth.btn.refuted"), f"mq:v:{key}:refuted")],
                [(t("truth.btn.partly"), f"mq:v:{key}:partly"), (t("truth.btn.unconfirmed"), f"mq:v:{key}:unconfirmed")],
                [(t("mod.btn.no"), f"mq:n:{key}")]]
    return [[(t("mod.btn.ok"), f"mq:a:{key}"), (t("mod.btn.no"), f"mq:n:{key}")]]


def _mod_chat_id() -> int | None:
    raw = get_settings().mod_chat_id.strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


async def announce(key: str) -> int:
    """Новая заявка — в чат модераторов (или каждому модератору в личку) с кнопками."""
    notifier = get_notifier()
    if notifier is None:
        return 0
    with get_sessionmaker()() as session:
        kind, _, raw = key.partition(":")
        if kind == "app":
            app = session.get(Application, int(raw))
            if app is None or app.status != "pending":
                return 0
            item = _app_item(session, app)
        else:
            post = session.get(Post, int(raw))
            if post is None:
                return 0
            reports = session.scalar(select(func.count()).select_from(Report).where(Report.post_id == post.id, Report.resolved.is_(False))) or 0
            if kind == "post" and post.status != "pending":
                return 0
            reasons = [r.reason for r in session.scalars(select(Report).where(Report.post_id == post.id, Report.resolved.is_(False))) if r.reason]
            item = _post_item(session, post, reports if kind == "report" else 0, reasons)
    text, buttons = item_html(item), item_buttons(item)
    chat = _mod_chat_id()
    if chat:
        return int(await notifier.send(chat, text, buttons))
    sent = 0
    for uid in roles.moderator_ids():
        sent += await notifier.send(uid, text, buttons)
    if not sent:
        log.warning("Заявка %s ждёт модерации, но модераторы не настроены (ADMIN_IDS / MODERATOR_IDS / MOD_CHAT_ID в .env).", key)
    return sent


def set_notify_ref(post_id: int, user_id: int) -> None:
    """Запомнить зашифрованный «обратный адрес» автора анонимной заявки — чтобы сообщить решение."""
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is not None and not post.author_id:
            post.notify_enc = crypto.encrypt_text(str(user_id))
            session.commit()


def _local_today() -> str:
    from backend.app.services.planner import local_today

    return local_today().isoformat()

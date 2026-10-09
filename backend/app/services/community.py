"""Контент сообщества одним механизмом (таблица posts). Модерация — services/moderation.py.

Правила (из задания):
  - у любого пользовательского контента есть «Пожаловаться»; жалоба сразу уходит владельцу;
  - вопросы, объявления, потеряшки, отзывы и сообщения о разводах публикуются ПОСЛЕ модерации;
  - модерация — очередь в Mini App, чат модераторов с кнопками и команды бота (services/moderation.py);
  - автор видит статус в «Моих обращениях» и получает сообщение о решении (с причиной отказа);
  - оцениваем предметы, процессы и сервисы, но не людей: отзывы о преподавателях не принимаем;
  - анонимные виды хранят только хеш автора (author_hash), без id;
  - никаких готовых ответов на экзамены, вариантов текущих контрольных, чужих работ и пиратских копий.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.app.core.features import enabled, is_owner
from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import User, iso_utc
from backend.app.db.models_campus import EventGoing, Post, PostAck, PostVote, Report, Slot
from backend.app.i18n import t
from backend.app.security import crypto
from backend.app.security.masking import mask_sensitive
from backend.app.services import anon

# kind → настройки: флаг функции, анонимность, нужна ли модерация, голосование, «прочитал», родитель.
KINDS: dict[str, dict] = {
    "senior_q": {"flag": "ask_senior", "anon": True, "moderated": True, "votes": False},
    "senior_a": {"flag": "ask_senior", "anon": True, "moderated": False, "votes": True, "parent": "senior_q"},
    "radar": {"flag": "scam_radar", "anon": True, "moderated": True, "votes": False},
    "team": {"flag": "team_search", "anon": False, "moderated": True, "contact": True},
    "lost": {"flag": "lost_found", "anon": False, "moderated": True, "contact": True},
    "event": {"flag": "events", "anon": False, "moderated": True, "going": True},
    "review": {"flag": "subject_reviews", "anon": True, "moderated": True, "votes": False},
    "resource": {"flag": "resources", "anon": True, "moderated": True, "votes": True},
    "vacancy": {"flag": "career", "anon": False, "moderated": True},
    "ai_rules": {"flag": "ai_rules", "anon": False, "moderated": False, "acks": True, "teacher_role": True},
    "announce": {"flag": "teacher_cabinet", "anon": False, "moderated": False, "acks": True, "verified": True},
    "hub_deadline": {"flag": "course_plan", "anon": False, "moderated": False, "verified": True},
    "lecture": {"flag": "lecture_questions", "anon": False, "moderated": False, "teacher_role": True},
    "lecture_q": {"flag": "lecture_questions", "anon": True, "moderated": False, "votes": True, "parent": "lecture"},
    "faq": {"flag": "hub_faq", "anon": True, "moderated": False, "votes": True, "internal": True},
    # Слух, который «Правда» не смогла подтвердить, — автор отправил его модератору (создаётся через /api/checks/{id}/escalate).
    "rumor": {"flag": "fact_feed", "anon": True, "moderated": True, "internal": True},
}
AUTO_HIDE_REPORTS = 3
PERSON_RE = re.compile(r"препод\w*|преподавател\w*|учител\w*|лектор\w*|куратор\w*|эдвайзер\w*|профессор\w*|доцент\w*|\bагай\b|\bапай\b", re.IGNORECASE)
HONESTY_RE = re.compile(r"(готов\w*|все|сливы?|слив\w*)\s+ответ\w*|ответы\s+на\s+(экзамен|рубежк|рк|тест|финал|контрольн)\w*|"
                        r"слив\w*\s+(экзамен|рубежк|тест|вариант)\w*|варианты?\s+(контрольн|рубежк|экзамен)\w*|"
                        r"сдам\s+за\s+тебя|напишу\s+(курсов|диплом|лаб)\w*\s+за|купить\s+(курсов|диплом|лаб)\w*", re.IGNORECASE)


class CommunityError(Exception):
    pass


def _data(post: Post) -> dict:
    try:
        return json.loads(post.data_json or "{}")
    except ValueError:
        return {}


def post_dict(post: Post, user_id: int | None = None, session=None) -> dict:
    cfg = KINDS.get(post.kind, {})
    data = _data(post)
    mine = bool(user_id and (post.author_id == user_id or (post.author_hash and post.author_hash == anon.author_hash(user_id))))
    out = {
        "id": post.id, "kind": post.kind, "hub_id": post.hub_id, "parent_id": post.parent_id, "title": post.title, "body": post.body,
        "data": {k: v for k, v in data.items() if not k.startswith("_")}, "status": post.status, "score": post.score,
        "is_teacher": post.is_teacher, "is_mentor": post.is_mentor, "promoted": post.promoted,
        "author_name": "" if cfg.get("anon") else post.author_name, "mine": mine, "created_at": iso_utc(post.created_at),
        "reject_reason": post.reject_reason if mine else "", "moderated_at": iso_utc(post.moderated_at) if mine else None,
    }
    if session is not None and user_id:
        if cfg.get("votes"):
            out["voted"] = bool(session.scalar(select(PostVote.id).where(PostVote.post_id == post.id,
                                                                         PostVote.voter_hash == anon.voter_hash(f"post:{post.id}", user_id))))
        if cfg.get("acks"):
            out["acked"] = bool(session.scalar(select(PostAck.id).where(PostAck.post_id == post.id, PostAck.user_id == user_id)))
            out["acks"] = session.scalar(select(func.count()).select_from(PostAck).where(PostAck.post_id == post.id)) or 0
        if cfg.get("going"):
            out["going"] = bool(session.scalar(select(EventGoing.id).where(EventGoing.post_id == post.id, EventGoing.user_id == user_id)))
            out["going_count"] = session.scalar(select(func.count()).select_from(EventGoing).where(EventGoing.post_id == post.id)) or 0
        if post.kind in ("senior_q", "lecture"):
            out["answers"] = session.scalar(select(func.count()).select_from(Post).where(Post.parent_id == post.id, Post.status == "approved")) or 0
    return out


# Где фильтр честности нужен: виды, которые РАСПРОСТРАНЯЮТ материалы и ответы. В «Радаре разводов» и в вопросах,
# наоборот, о продаже «ответов» как раз и сообщают — там текст проверяет модератор.
HONESTY_KINDS = {"resource", "senior_a", "review", "team", "lecture_q"}


def _check_text(kind: str, title: str, body: str) -> None:
    text = f"{title}\n{body}"
    if kind == "review" and PERSON_RE.search(text):
        raise CommunityError(t("cm.post.err.person"))
    if kind in HONESTY_KINDS and HONESTY_RE.search(text):
        raise CommunityError(t("cm.post.err.honesty"))


def _user_profile(session, user_id: int) -> User | None:
    return repo.get_user(session, user_id)


def create_post(kind: str, user_id: int, user_name: str, title: str = "", body: str = "", data: dict | None = None,
                hub_id: int | None = None, parent_id: int | None = None) -> dict:
    """Создать публикацию. Модерируемые виды ждут владельца; остальные видны сразу (с кнопкой «Пожаловаться»)."""
    from backend.app.services import hubs as hubs_service
    from backend.app.university import crisis

    cfg = KINDS.get(kind)
    if cfg is None or cfg.get("internal"):
        raise CommunityError(t("cm.post.err.kind"))
    if not enabled(cfg["flag"]):
        raise CommunityError(t("cm.feature_off"))
    title = " ".join((title or "").split())[:256]
    body = (body or "").strip()[:4000]
    if len(title) < 3 and len(body) < 3:
        raise CommunityError(t("cm.post.err.empty"))
    _check_text(kind, title, body)
    data = dict(data or {})
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, user_name)
        if cfg.get("verified") and not hubs_service.is_hub_teacher(session, hub_id, user_id):
            raise CommunityError(t("hub.err.verified_only"))
        if cfg.get("teacher_role") and user.role != "teacher" and not user.teacher_verified:
            raise CommunityError(t("cm.post.err.teacher_role"))
        if parent_id:
            parent = session.get(Post, parent_id)
            if parent is None or parent.kind != cfg.get("parent") or parent.status != "approved":
                raise CommunityError(t("cm.post.err.parent"))
            hub_id = parent.hub_id
        elif cfg.get("parent"):
            raise CommunityError(t("cm.post.err.parent"))
        if kind == "review" and not hub_id and not data.get("subject"):
            raise CommunityError(t("cm.post.err.subject"))
        if kind == "event":
            try:
                date.fromisoformat(str(data.get("date", "")))
            except ValueError as exc:
                raise CommunityError(t("cm.post.err.event_date")) from exc
        if kind == "hub_deadline":
            try:
                date.fromisoformat(str(data.get("due_date", "")))
            except ValueError as exc:
                raise CommunityError(t("pl.err.date")) from exc
        if kind in ("review",):
            for key in ("load", "difficulty"):
                value = int(data.get(key) or 0)
                if not 1 <= value <= 5:
                    raise CommunityError(t("cm.post.err.rating"))
                data[key] = value
        if kind == "resource" and not re.match(r"^https?://\S+\.\S+", str(data.get("url", ""))):
            raise CommunityError(t("cm.post.err.url"))
        hub_role = hubs_service.member_role(session, hub_id, user_id) if hub_id else ""
        post = Post(
            kind=kind, hub_id=hub_id, parent_id=parent_id,
            author_id=None if cfg["anon"] else user_id, author_hash=anon.author_hash(user_id),
            author_name="" if cfg["anon"] else (user_name or "")[:128],
            title=mask_sensitive(title), body=mask_sensitive(body) if cfg["anon"] else body, data_json=json.dumps(data, ensure_ascii=False),
            status="pending" if cfg["moderated"] and not is_owner(user_id) else "approved",
            is_mentor=hub_role == "mentor" or (kind == "senior_a" and hubs_service.is_mentor_anywhere(session, user_id)),
            is_teacher=hub_role == "teacher" and bool(user.teacher_verified),
        )
        if cfg["anon"] and (post.status == "pending" or kind == "senior_q"):
            # Зашифрованный «обратный адрес»: сообщить решение модератора и новые ответы на вопрос.
            post.notify_enc = crypto.encrypt_text(str(user_id))
        session.add(post)
        session.commit()
        result = post_dict(post, user_id, session)
    result["crisis"] = crisis.detect(f"{title}\n{body}") if kind in ("senior_q", "senior_a", "lecture_q", "radar") else ""
    result["pending"] = result["status"] == "pending"
    return result


def list_posts(kind: str, user_id: int | None = None, hub_id: int | None = None, parent_id: int | None = None,
               limit: int = 50, include_pending_mine: bool = True, order: str = "new") -> list[dict]:
    cfg = KINDS.get(kind)
    if cfg is None:
        raise CommunityError(t("cm.post.err.kind"))
    with get_sessionmaker()() as session:
        query = select(Post).where(Post.kind == kind)
        if hub_id is not None:
            query = query.where(Post.hub_id == hub_id)
        if parent_id is not None:
            query = query.where(Post.parent_id == parent_id)
        mine_hash = anon.author_hash(user_id) if user_id else ""
        if include_pending_mine and user_id:
            query = query.where((Post.status == "approved") | ((Post.status == "pending") & (Post.author_hash == mine_hash)))
        else:
            query = query.where(Post.status == "approved")
        if order == "top":
            query = query.order_by(Post.is_teacher.desc(), Post.score.desc(), Post.created_at.desc())
        else:
            query = query.order_by(Post.created_at.desc())
        if kind == "event":
            today = date.today().isoformat()
            rows = [p for p in session.scalars(query.limit(500)) if str(_data(p).get("date", "")) >= today]
            rows.sort(key=lambda p: str(_data(p).get("date", "")))
            return [post_dict(p, user_id, session) for p in rows[:limit]]
        return [post_dict(p, user_id, session) for p in session.scalars(query.limit(limit))]


def get_post(post_id: int, user_id: int | None = None) -> dict:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or (post.status != "approved" and not (user_id and (post.author_hash == anon.author_hash(user_id) or is_owner(user_id)))):
            raise CommunityError(t("cm.post.err.not_found"))
        return post_dict(post, user_id, session)


def my_posts(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.author_hash == anon.author_hash(user_id), Post.kind != "faq").order_by(Post.created_at.desc()).limit(100))
        return [post_dict(p, user_id, session) for p in rows]


def delete_own(post_id: int, user_id: int) -> None:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.author_hash != anon.author_hash(user_id):
            raise CommunityError(t("cm.post.err.not_found"))
        session.execute(delete(Post).where(Post.parent_id == post.id))
        session.delete(post)
        session.commit()


def hide_by_container_owner(post_id: int, user_id: int) -> None:
    """Автор «доски вопросов к лекции» может скрыть вопрос на своей доске."""
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        parent = session.get(Post, post.parent_id) if post and post.parent_id else None
        if post is None or parent is None or parent.author_id != user_id:
            raise CommunityError(t("cm.post.err.not_found"))
        post.status = "hidden"
        session.commit()


def vote(post_id: int, user_id: int, value: int = 1) -> dict:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.status != "approved" or not KINDS.get(post.kind, {}).get("votes"):
            raise CommunityError(t("cm.post.err.not_found"))
        h = anon.voter_hash(f"post:{post_id}", user_id)
        existing = session.scalar(select(PostVote).where(PostVote.post_id == post_id, PostVote.voter_hash == h))
        value = 1 if value > 0 else -1 if value < 0 else 0
        if existing is not None and value == 0:
            session.delete(existing)
        elif existing is not None:
            existing.value = value
        elif value:
            session.add(PostVote(post_id=post_id, voter_hash=h, value=value))
        session.flush()
        post.score = session.scalar(select(func.coalesce(func.sum(PostVote.value), 0)).where(PostVote.post_id == post_id)) or 0
        session.commit()
        return post_dict(post, user_id, session)


def ack(post_id: int, user_id: int, user_name: str) -> dict:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.status != "approved" or not KINDS.get(post.kind, {}).get("acks"):
            raise CommunityError(t("cm.post.err.not_found"))
        repo.upsert_user(session, user_id, user_name)
        if not session.scalar(select(PostAck.id).where(PostAck.post_id == post_id, PostAck.user_id == user_id)):
            session.add(PostAck(post_id=post_id, user_id=user_id, name=(user_name or "")[:128]))
            session.commit()
        return post_dict(post, user_id, session)


def ack_report(post_id: int, user_id: int) -> dict:
    """Отчёт для автора правил/объявления: кто подтвердил прочтение, кто из участников хаба — ещё нет."""
    from backend.app.db.models_campus import HubMember

    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.author_id != user_id:
            raise CommunityError(t("cm.report.only_creator"))
        acks = list(session.scalars(select(PostAck).where(PostAck.post_id == post_id).order_by(PostAck.created_at)))
        acked = {a.user_id for a in acks}
        waiting = []
        if post.hub_id:
            ids = [m.user_id for m in session.scalars(select(HubMember).where(HubMember.hub_id == post.hub_id, HubMember.role == "member"))]
            users = {u.telegram_id: u.first_name for u in session.scalars(select(User).where(User.telegram_id.in_(ids)))}
            waiting = [users.get(uid) or "Участник" for uid in ids if uid not in acked and uid != user_id]
        return {"post_id": post_id, "title": post.title, "confirmed": [a.name or "Участник" for a in acks], "waiting": waiting,
                "known_members": bool(post.hub_id)}


def going(post_id: int, user_id: int, user_name: str, on: bool = True) -> dict:
    """«Иду» на событие: запоминаем для напоминания и ПРЕДЛАГАЕМ задачу в план (без молчаливого добавления)."""
    from backend.app.services import planner

    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.kind != "event" or post.status != "approved":
            raise CommunityError(t("cm.post.err.not_found"))
        repo.upsert_user(session, user_id, user_name)
        row = session.scalar(select(EventGoing).where(EventGoing.post_id == post_id, EventGoing.user_id == user_id))
        if on and row is None:
            session.add(EventGoing(post_id=post_id, user_id=user_id))
        elif not on and row is not None:
            session.delete(row)
        session.commit()
        data = _data(post)
        title = post.title
        result = post_dict(post, user_id, session)
    if on:
        planner.suggest(user_id, title, "event", f"event:{post_id}", due_date=str(data.get("date", "")), due_time=str(data.get("time", ""))[:5],
                        note=str(data.get("place", "")))
    return result


async def contact_author(post_id: int, user_id: int, user_name: str, username: str, text: str) -> bool:
    """Связаться с автором объявления через бота: контакт автора не раскрываем."""
    text = " ".join((text or "").split())[:800]
    if len(text) < 2:
        raise CommunityError(t("cm.post.err.empty"))
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.status != "approved" or not KINDS.get(post.kind, {}).get("contact") or not post.author_id:
            raise CommunityError(t("cm.post.err.not_found"))
        if post.author_id == user_id:
            raise CommunityError(t("cm.post.err.own"))
        author_id, title = post.author_id, post.title
    notifier = get_notifier()
    if notifier is None:
        raise CommunityError(t("api.error.bot_needed"))
    who = html.escape(user_name or "Пользователь") + (f" (@{html.escape(username)})" if username else "")
    return await notifier.send(author_id, t("cm.contact.msg", title=html.escape(title), who=who, text=html.escape(text)))


# ------------------------------------------------------------------ жалобы и модерация


async def report(post_id: int, user_id: int, reason: str = "") -> dict:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None:
            raise CommunityError(t("cm.post.err.not_found"))
        session.add(Report(post_id=post_id, reporter_hash=anon.voter_hash(f"report:{post_id}", user_id), reason=(reason or "")[:500]))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return {"reported": True, "again": True}
        count = session.scalar(select(func.count()).select_from(Report).where(Report.post_id == post_id, Report.resolved.is_(False))) or 0
        if count >= AUTO_HIDE_REPORTS and post.status == "approved":
            post.status = "hidden"  # несколько жалоб — прячем до решения владельца
            session.commit()
        report_kind = post.kind
    from backend.app.services.moderation import announce

    await announce(f"report:{post_id}")
    return {"reported": True, "kind": report_kind}


async def announce_pending(post_id: int) -> None:
    """Новая публикация ждёт модерации — в очередь модераторов (чат или личка) с кнопками."""
    from backend.app.services.moderation import announce

    await announce(f"post:{post_id}")


async def moderate(post_id: int, moderator_id: int, action: str, moderator_name: str = "") -> str:
    """Старые кнопки (mod:ok / mod:no / mod:hide / mod:keep) и «лучший ответ в навигатор»."""
    from backend.app.services import moderation

    if not is_owner(moderator_id):
        raise CommunityError(t("mod.err.moderator_only"))
    if action == "promote":
        with get_sessionmaker()() as session:
            post = session.get(Post, post_id)
            if post is None:
                raise CommunityError(t("cm.post.err.not_found"))
            post.promoted = not post.promoted
            session.commit()
            return post.status
    mapping = {"ok": ("post", "approve"), "no": ("post", "reject"), "hide": ("report", "hide"), "keep": ("report", "keep")}
    if action not in mapping:
        raise CommunityError(t("mod.err.action"))
    target, act = mapping[action]
    try:
        result = await moderation.decide(moderator_id, moderator_name, f"{target}:{post_id}", act, reason="rules" if act == "reject" else "",
                                         verdict="unconfirmed")
    except (moderation.ModerationError, moderation.roles.RoleError) as exc:
        raise CommunityError(str(exc)) from exc
    return result["status"]


def best_answers(limit: int = 10) -> list[dict]:
    """Лучшие ответы старшекурсников (по голосам) — владелец решает, что попадёт в навигатор."""
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.kind == "senior_a", Post.status == "approved", Post.score >= 1)
                               .order_by(Post.score.desc()).limit(limit))
        out = []
        for p in rows:
            q = session.get(Post, p.parent_id) if p.parent_id else None
            out.append({"id": p.id, "question": q.title if q else "", "answer": p.body or p.title, "score": p.score, "promoted": p.promoted})
        return out


def promoted_for_course(course: str) -> list[dict]:
    """Лучшие ответы после модерации — в навигатор по курсам."""
    with get_sessionmaker()() as session:
        out = []
        for a in session.scalars(select(Post).where(Post.kind == "senior_a", Post.promoted.is_(True), Post.status == "approved")):
            q = session.get(Post, a.parent_id) if a.parent_id else None
            q_course = str(_data(q).get("course", "")) if q else ""
            if q and q.status == "approved" and (not q_course or q_course == course):
                out.append({"question": q.title, "answer": a.body or a.title, "score": a.score, "mentor": a.is_mentor})
        return out


def review_summary(hub_id: int | None = None, subject: str = "") -> dict:
    """Средние оценки нагрузки и сложности — только когда отзывов не меньше пяти."""
    with get_sessionmaker()() as session:
        query = select(Post).where(Post.kind == "review", Post.status == "approved")
        query = query.where(Post.hub_id == hub_id) if hub_id else query
        rows = [p for p in session.scalars(query) if hub_id or str(_data(p).get("subject", "")).lower() == subject.lower()]
    n = len(rows)
    if not anon.visible(n):
        return {"count": n, "visible": False, "min": anon.min_answers()}
    load = sum(int(_data(p).get("load", 0)) for p in rows) / n
    diff = sum(int(_data(p).get("difficulty", 0)) for p in rows) / n
    return {"count": n, "visible": True, "load": round(load, 1), "difficulty": round(diff, 1), "min": anon.min_answers()}


# ------------------------------------------------------------------ запись на консультацию


def slot_dict(slot: Slot, user_id: int | None = None) -> dict:
    mine = bool(user_id and slot.taken_by == user_id)
    return {"id": slot.id, "owner_id": slot.owner_id, "owner_name": slot.owner_name, "hub_id": slot.hub_id, "start": slot.start,
            "minutes": slot.minutes, "place": slot.place, "taken": slot.taken_by is not None, "mine": mine,
            "taken_name": slot.taken_name if user_id == slot.owner_id else "", "is_owner": user_id == slot.owner_id}


def create_slots(owner_id: int, owner_name: str, starts: list[str], minutes: int = 15, place: str = "", hub_id: int | None = None) -> list[dict]:
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, owner_id, owner_name)
        if user.role != "teacher" and not user.teacher_verified:
            raise CommunityError(t("cm.post.err.teacher_role"))
        out = []
        for start in starts[:30]:
            try:
                datetime.fromisoformat(start)
            except ValueError as exc:
                raise CommunityError(t("cm.slot.err.time")) from exc
            slot = Slot(owner_id=owner_id, owner_name=(owner_name or "")[:128], hub_id=hub_id, start=start[:16], minutes=max(5, min(120, minutes)),
                        place=(place or "")[:128])
            session.add(slot)
            out.append(slot)
        session.commit()
        return [slot_dict(s, owner_id) for s in out]


def list_slots(user_id: int, owner_id: int | None = None, hub_id: int | None = None) -> list[dict]:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    with get_sessionmaker()() as session:
        query = select(Slot).where(Slot.start >= now)
        if owner_id:
            query = query.where(Slot.owner_id == owner_id)
        if hub_id:
            query = query.where(Slot.hub_id == hub_id)
        return [slot_dict(s, user_id) for s in session.scalars(query.order_by(Slot.start).limit(100))]


async def book_slot(slot_id: int, user_id: int, user_name: str, cancel: bool = False) -> dict:
    """Занять слот в один тап (или освободить). Задача консультации — предложением в план обоим."""
    from backend.app.services import planner

    with get_sessionmaker()() as session:
        slot = session.get(Slot, slot_id)
        if slot is None:
            raise CommunityError(t("cm.slot.err.not_found"))
        if cancel:
            if slot.taken_by != user_id and slot.owner_id != user_id:
                raise CommunityError(t("cm.slot.err.not_found"))
            slot.taken_by, slot.taken_name = None, ""
            session.commit()
            return slot_dict(slot, user_id)
        if slot.taken_by is not None:
            raise CommunityError(t("cm.slot.err.taken"))
        if slot.owner_id == user_id:
            raise CommunityError(t("cm.slot.err.own"))
        repo.upsert_user(session, user_id, user_name)
        slot.taken_by, slot.taken_name = user_id, (user_name or "")[:128]
        session.commit()
        result = slot_dict(slot, user_id)
        owner_id, start, place, owner_name = slot.owner_id, slot.start, slot.place, slot.owner_name
    day, time_ = start.split("T")
    planner.suggest(user_id, t("cm.slot.task", name=owner_name or t("cm.slot.teacher")), "consult", f"slot:{slot_id}:{user_id}",
                    due_date=day, due_time=time_[:5], note=place)
    planner.suggest(owner_id, t("cm.slot.task_owner", name=user_name or ""), "consult", f"slot:{slot_id}:{user_id}", due_date=day,
                    due_time=time_[:5], note=place)
    if (notifier := get_notifier()):
        await notifier.send(owner_id, html.escape(t("cm.slot.booked_notify", name=user_name or "", when=start.replace("T", " "))))
    return result


def delete_slot(slot_id: int, owner_id: int) -> None:
    with get_sessionmaker()() as session:
        slot = session.get(Slot, slot_id)
        if slot is None or slot.owner_id != owner_id:
            raise CommunityError(t("cm.slot.err.not_found"))
        session.delete(slot)
        session.commit()


# ------------------------------------------------------------------ напоминания о событиях (планировщик)


def events_tomorrow(today: date) -> list[tuple[int, str]]:
    from backend.app.services import prefs

    tomorrow = (today + timedelta(days=1)).isoformat()
    out = []
    with get_sessionmaker()() as session:
        for row in session.scalars(select(EventGoing).where(EventGoing.reminded.is_(False))):
            post = session.get(Post, row.post_id)
            if post is None or str(_data(post).get("date", "")) != tomorrow:
                continue
            row.reminded = True
            if prefs.get_bool(row.user_id, "sub.events"):
                data = _data(post)
                out.append((row.user_id, html.escape(t("cm.event.remind", title=post.title, time=data.get("time", ""), place=data.get("place", "")))))
        session.commit()
    return out


def delete_user(session, user_id: int) -> None:
    """Удаление данных: неанонимные публикации, «иду», подтверждения, слоты; анонимные — по хешу автора."""
    h = anon.author_hash(user_id)
    session.execute(delete(Post).where((Post.author_id == user_id) | (Post.author_hash == h)))
    session.execute(delete(PostAck).where(PostAck.user_id == user_id))
    session.execute(delete(EventGoing).where(EventGoing.user_id == user_id))
    session.execute(delete(Slot).where(Slot.owner_id == user_id))
    for slot in session.scalars(select(Slot).where(Slot.taken_by == user_id)):
        slot.taken_by, slot.taken_name = None, ""


def since_days(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)

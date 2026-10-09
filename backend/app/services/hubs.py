"""Учебные хабы: каталог по курсам и программам, группы с темами, частые вопросы, менторы, преподаватели.

Бот не заменяет студенческие группы, а помогает внутри них и собирает их в общий каталог.

Приватность: переписку бот не хранит. В частые вопросы попадают только сообщения, которые
участник явно отметил командой /save (и ментор подтвердил). Для дайджеста — короткая сводка
из того, что бот и так знает (сохранённые ответы, открытые вопросы, договорённости); включает
его администратор группы, и бот сообщает об этом участникам.

Статус преподавателя для кабинета дисциплины, метки «ответ преподавателя» и «Преподавательской»
подтверждает владелец бота вручную. Самозаявленной роли недостаточно.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import delete, func, select, true

from backend.app.core.features import enabled, is_owner
from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import Agreement, Check, User, iso_utc, utcnow
from backend.app.db.models_campus import Application, Hub, HubChat, HubMember, HubTopic, Post
from backend.app.i18n import t
from backend.app.rag.store import tokenize
from backend.app.services import anon
from backend.app.university import reference
from backend.app.university.reference import load_json

SIMILAR_THRESHOLD = 0.5


class HubError(Exception):
    pass


@lru_cache
def seed_data() -> dict:
    return load_json("hubs.json")


def seed_hubs() -> int:
    """Стартовые хабы из data/iitu/hubs.json (по slug, повторно не создаются)."""
    data = seed_data()
    added = 0
    with get_sessionmaker()() as session:
        existing = {h.slug for h in session.scalars(select(Hub))}
        for item in data.get("hubs", []) + data.get("discipline_hubs", []):
            if item["slug"] in existing:
                continue
            session.add(Hub(slug=item["slug"], title=item["title"], kind=item.get("kind", "discipline"), course=item.get("course", ""),
                            program=item.get("program", ""), emoji=item.get("emoji", "📚"), description=item.get("description", ""),
                            chat_url=item.get("chat_url", "")))
            added += 1
        session.commit()
    return added


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9а-яё]+", "-", title.lower()).strip("-")[:40] or "hub"
    return base


def _settings(hub: Hub) -> dict:
    try:
        return json.loads(hub.settings_json or "{}")
    except ValueError:
        return {}


def member_role(session, hub_id: int | None, user_id: int) -> str:
    if not hub_id:
        return ""
    row = session.scalar(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id))
    return row.role if row else ""


def is_hub_teacher(session, hub_id: int | None, user_id: int) -> bool:
    if not hub_id:
        return False
    user = repo.get_user(session, user_id)
    return bool(user and user.teacher_verified and member_role(session, hub_id, user_id) == "teacher")


def is_mentor_anywhere(session, user_id: int) -> bool:
    return bool(session.scalar(select(HubMember.id).where(HubMember.user_id == user_id, HubMember.role == "mentor")))


def is_verified_teacher(user_id: int) -> bool:
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        return bool(user and user.teacher_verified)


def hub_dict(session, hub: Hub, user_id: int | None = None) -> dict:
    members = session.scalar(select(func.count()).select_from(HubMember).where(HubMember.hub_id == hub.id)) or 0
    return {"id": hub.id, "slug": hub.slug, "title": hub.title, "kind": hub.kind, "course": hub.course,
            "course_title": reference.course_title(hub.course) if hub.course else t("hub.any_course"), "program": hub.program,
            "emoji": hub.emoji, "description": hub.description, "chat_url": hub.chat_url, "members": members,
            "my_role": member_role(session, hub.id, user_id) if user_id else "", "status": hub.status,
            "settings": _settings(hub)}


def _can_see(session, hub: Hub, user_id: int) -> bool:
    if hub.status != "active":
        return is_owner(user_id)
    if hub.kind == "teachers":
        user = repo.get_user(session, user_id)
        return bool(is_owner(user_id) or (user and user.teacher_verified)) and enabled("teachers_room")
    return True


def catalog(user_id: int) -> dict:
    """Каталог: сквозные хабы для любого курса + хабы дисциплин по курсам и программам + мои хабы."""
    seed_hubs()
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        hubs = [h for h in session.scalars(select(Hub).where(Hub.status == "active").order_by(Hub.course, Hub.program, Hub.title))
                if _can_see(session, h, user_id)]
        items = [hub_dict(session, h, user_id) for h in hubs]
        mine = [x for x in items if x["my_role"]]
        by_course: dict[str, list[dict]] = {}
        for x in items:
            if x["kind"] == "discipline":
                by_course.setdefault(x["course"] or "any", []).append(x)
        order = [c["id"] for c in reference.courses()] + ["any"]
        groups = [{"course": c, "title": reference.course_title(c) if c != "any" else t("hub.any_course"), "hubs": by_course[c]}
                  for c in order if c in by_course]
        return {"mine": mine, "cross": [x for x in items if x["kind"] == "cross"], "courses": groups,
                "teachers": [x for x in items if x["kind"] == "teachers"],
                "teacher_verified": bool(user and user.teacher_verified), "is_owner": is_owner(user_id),
                "mentor_recruiting": date.today().month == 9, "my_course": user.course if user and user.role == "student" else ""}


def page(hub_id: int, user_id: int) -> dict:
    """Страница хаба: описание, чат, частые вопросы, материалы, менторы, дедлайны и события, вопросы без ответа."""
    from backend.app.services import community, polls

    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        if hub is None or not _can_see(session, hub, user_id):
            raise HubError(t("hub.err.not_found"))
        data = hub_dict(session, hub, user_id)
        roles = list(session.scalars(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.role.in_(("mentor", "teacher", "curator")))))
        users = {u.telegram_id: u for u in session.scalars(select(User).where(User.telegram_id.in_([r.user_id for r in roles])))}
        data["mentors"] = [{"name": users[r.user_id].first_name if r.user_id in users else "Ментор", "user_id": r.user_id}
                           for r in roles if r.role == "mentor"]
        data["teachers"] = [{"name": users[r.user_id].first_name if r.user_id in users else "Преподаватель", "user_id": r.user_id}
                            for r in roles if r.role == "teacher" and r.user_id in users and users[r.user_id].teacher_verified]
        chats = list(session.scalars(select(HubChat).where(HubChat.hub_id == hub_id)))
        data["chats"] = [{"title": c.title, "digest_on": c.digest_on, "topic": bool(c.thread_id)} for c in chats]
        me = repo.get_user(session, user_id)
        data["i_am_verified_teacher"] = bool(me and me.teacher_verified)
        data["i_am_hub_teacher"] = is_hub_teacher(session, hub_id, user_id)
    today = date.today().isoformat()
    data["faq"] = community.list_posts("faq", user_id, hub_id=hub_id, order="top", include_pending_mine=False)[:30] if enabled("hub_faq") else []
    data["resources"] = community.list_posts("resource", user_id, hub_id=hub_id, order="top")[:20] if enabled("resources") else []
    data["deadlines"] = [p for p in community.list_posts("hub_deadline", user_id, hub_id=hub_id)
                         if str(p["data"].get("due_date", "")) >= today][:20] if enabled("course_plan") else []
    data["deadlines"].sort(key=lambda p: p["data"].get("due_date", ""))
    data["events"] = community.list_posts("event", user_id, hub_id=hub_id)[:10] if enabled("events") else []
    questions = community.list_posts("senior_q", user_id, hub_id=hub_id)[:50] if enabled("ask_senior") else []
    data["questions"] = questions[:20]
    data["unanswered"] = [q for q in questions if not q.get("answers")][:20]
    data["announcements"] = community.list_posts("announce", user_id, hub_id=hub_id)[:10] if enabled("teacher_cabinet") else []
    data["lectures"] = community.list_posts("lecture", user_id, hub_id=hub_id)[:10] if enabled("lecture_questions") else []
    data["reviews"] = community.list_posts("review", user_id, hub_id=hub_id)[:20] if enabled("subject_reviews") else []
    data["review_summary"] = community.review_summary(hub_id=hub_id) if enabled("subject_reviews") else None
    data["slots"] = community.list_slots(user_id, hub_id=hub_id) if enabled("consultations") else []
    pulse_on = enabled("group_pulse") and data["settings"].get("group_pulse")
    data["pulse"] = polls.group_pulse(hub_id, data["title"]) if pulse_on else None
    if pulse_on and data["pulse"]:
        data["pulse"]["answered"] = polls.get(data["pulse"]["id"], user_id)["answered"]
    data["pulse_history"] = polls.group_pulse_history(hub_id) if pulse_on and data["i_am_hub_teacher"] else []
    return data


def join(hub_id: int, user_id: int, user_name: str, on: bool = True) -> dict:
    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        if hub is None or not _can_see(session, hub, user_id):
            raise HubError(t("hub.err.not_found"))
        repo.upsert_user(session, user_id, user_name)
        row = session.scalar(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id))
        if on and row is None:
            session.add(HubMember(hub_id=hub_id, user_id=user_id, role="member"))
        elif not on and row is not None:
            session.delete(row)
        session.commit()
        return hub_dict(session, hub, user_id)


def attach_teacher(hub_id: int, user_id: int) -> dict:
    """Подтверждённый преподаватель закрепляется за хабом своей дисциплины."""
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        hub = session.get(Hub, hub_id)
        if not user or not user.teacher_verified:
            raise HubError(t("hub.err.verified_only"))
        if hub is None or hub.kind == "teachers":
            raise HubError(t("hub.err.not_found"))
        row = session.scalar(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id))
        if row is None:
            session.add(HubMember(hub_id=hub_id, user_id=user_id, role="teacher"))
        else:
            row.role = "teacher"
        session.commit()
        return hub_dict(session, hub, user_id)


def update_settings(hub_id: int, user_id: int, **values) -> dict:
    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        if hub is None or not (is_hub_teacher(session, hub_id, user_id) or is_owner(user_id)):
            raise HubError(t("hub.err.verified_only"))
        settings = _settings(hub)
        for key in ("group_pulse",):
            if key in values and values[key] is not None:
                settings[key] = bool(values[key])
        hub.settings_json = json.dumps(settings)
        session.commit()
        return hub_dict(session, hub, user_id)


# ------------------------------------------------------------------ заявки: хаб, ментор, преподаватель


async def _notify_owners_application(app_id: int) -> None:
    from backend.app.services.moderation import announce

    await announce(f"app:{app_id}")


async def apply(kind: str, user_id: int, user_name: str, data: dict) -> dict:
    if kind not in ("mentor", "teacher", "hub"):
        raise HubError(t("hub.err.kind"))
    clean = {k: " ".join(str(v).split())[:500] for k, v in data.items() if k != "hub_ids" and v not in (None, "")}
    hub_ids = [int(x) for x in (data.get("hub_ids") or []) if str(x).isdigit()][:10]
    if kind == "hub" and len(clean.get("title", "")) < 3:
        raise HubError(t("hub.err.title"))
    if kind == "hub" and clean.get("course") and not reference.is_valid("course", clean["course"]):
        raise HubError(t("hub.err.course"))
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, user_name)
        if kind == "teacher" and user.teacher_verified:
            raise HubError(t("hub.err.already_verified"))
        if session.scalar(select(Application.id).where(Application.user_id == user_id, Application.kind == kind, Application.status == "pending")):
            raise HubError(t("hub.err.already_applied"))
        app = Application(kind=kind, user_id=user_id, name=(user_name or "")[:128], data_json=json.dumps({**clean, "hub_ids": hub_ids}, ensure_ascii=False))
        session.add(app)
        session.commit()
        app_id = app.id
    await _notify_owners_application(app_id)
    return {"id": app_id, "status": "pending"}


def my_applications(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        return [{"id": a.id, "kind": a.kind, "status": a.status, "created_at": iso_utc(a.created_at),
                 "reason": json.loads(a.data_json or "{}").get("_reason", ""), "decided_at": iso_utc(a.decided_at)}
                for a in session.scalars(select(Application).where(Application.user_id == user_id).order_by(Application.created_at.desc()))]


def pending_applications() -> list[dict]:
    with get_sessionmaker()() as session:
        return [{"id": a.id, "kind": a.kind, "name": a.name, "user_id": a.user_id, "data": json.loads(a.data_json or "{}")}
                for a in session.scalars(select(Application).where(Application.status == "pending").order_by(Application.created_at))]


async def decide(app_id: int, owner_id: int, approve: bool, reason: str = "") -> dict:
    """Решение модератора по заявке. Ментор → роль в выбранных хабах; преподаватель → подтверждённый статус.
    Причину отказа видит автор (в «Моих обращениях» и в сообщении бота)."""
    if not is_owner(owner_id):
        raise HubError(t("mod.err.owner_only"))
    with get_sessionmaker()() as session:
        app = session.get(Application, app_id)
        if app is None or app.status != "pending":
            raise HubError(t("hub.err.app_done"))
        app.status = "approved" if approve else "rejected"
        app.decided_at = utcnow()
        data = json.loads(app.data_json or "{}")
        if not approve and reason:
            app.data_json = json.dumps({**data, "_reason": reason[:500]}, ensure_ascii=False)
        result_hub = None
        if approve and app.kind == "teacher":
            user = repo.upsert_user(session, app.user_id, app.name)
            user.teacher_verified = True
            for hid in data.get("hub_ids", []):
                _set_role(session, hid, app.user_id, "teacher")
            room = session.scalar(select(Hub).where(Hub.kind == "teachers"))
            if room:
                _set_role(session, room.id, app.user_id, "member")
        elif approve and app.kind == "mentor":
            for hid in data.get("hub_ids", []):
                _set_role(session, hid, app.user_id, "mentor")
        elif approve and app.kind == "hub":
            slug = _slug(data.get("title", "hub"))
            while session.scalar(select(Hub.id).where(Hub.slug == slug)):
                slug = f"{slug[:36]}-{app.id}"
            hub = Hub(slug=slug, title=data.get("title", "")[:128], kind="discipline", course=data.get("course", ""), program=data.get("program", "")[:16],
                      description=data.get("description", ""), chat_url=data.get("chat_url", "") if str(data.get("chat_url", "")).startswith("https://t.me/") else "",
                      created_by=app.user_id)
            session.add(hub)
            session.flush()
            result_hub = hub.id
            _set_role(session, hub.id, app.user_id, "curator")
        session.commit()
        user_id, kind = app.user_id, app.kind
    notifier = get_notifier()
    if notifier:
        text = t(f"hub.app.{'approved' if approve else 'rejected'}.{kind}")
        if not approve and reason:
            text += "\n" + t("mod.notify.reason", reason=reason)
        await notifier.send(user_id, html.escape(text))
    return {"id": app_id, "status": "approved" if approve else "rejected", "hub_id": result_hub}


def _set_role(session, hub_id: int, user_id: int, role: str) -> None:
    row = session.scalar(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id))
    if row is None:
        session.add(HubMember(hub_id=hub_id, user_id=user_id, role=role))
    elif row.role in ("member", "") or role in ("teacher", "curator"):
        row.role = role


def create_hub_direct(owner_id: int, title: str, course: str = "", program: str = "", description: str = "", kind: str = "discipline") -> dict:
    if not is_owner(owner_id):
        raise HubError(t("mod.err.owner_only"))
    if course and not reference.is_valid("course", course):
        raise HubError(t("hub.err.course"))
    with get_sessionmaker()() as session:
        slug = _slug(title)
        n = 1
        while session.scalar(select(Hub.id).where(Hub.slug == slug)):
            n += 1
            slug = f"{_slug(title)[:36]}-{n}"
        hub = Hub(slug=slug, title=title[:128], kind=kind, course=course, program=program[:16], description=description, created_by=owner_id)
        session.add(hub)
        session.commit()
        return hub_dict(session, hub, owner_id)


# ------------------------------------------------------------------ группы и темы Telegram


def remember_topic(chat_id: int, thread_id: int, name: str) -> None:
    if not thread_id or not name:
        return
    with get_sessionmaker()() as session:
        row = session.get(HubTopic, (chat_id, thread_id)) or HubTopic(chat_id=chat_id, thread_id=thread_id)
        row.name = name[:128]
        session.add(row)
        session.commit()


def topic_name(chat_id: int, thread_id: int | None) -> str:
    if not thread_id:
        return ""
    with get_sessionmaker()() as session:
        row = session.get(HubTopic, (chat_id, thread_id))
        return row.name if row else ""


def hub_for_chat(chat_id: int, thread_id: int | None) -> dict | None:
    """Хаб темы (если привязана) или всего чата."""
    with get_sessionmaker()() as session:
        link = None
        if thread_id:
            link = session.scalar(select(HubChat).where(HubChat.chat_id == chat_id, HubChat.thread_id == thread_id))
        link = link or session.scalar(select(HubChat).where(HubChat.chat_id == chat_id, HubChat.thread_id == 0))
        if link is None:
            return None
        hub = session.get(Hub, link.hub_id)
        return {"hub_id": link.hub_id, "title": hub.title if hub else "", "link_id": link.id, "digest_on": link.digest_on,
                "thread_id": link.thread_id} if hub else None


def find_hub(query: str) -> list[dict]:
    q = query.strip().lower()
    with get_sessionmaker()() as session:
        hubs = [h for h in session.scalars(select(Hub).where(Hub.status == "active", Hub.kind != "teachers"))]
        exact = [h for h in hubs if h.slug == q or h.title.lower() == q or str(h.id) == q]
        found = exact or [h for h in hubs if q and (q in h.title.lower() or q in h.slug)]
        return [{"id": h.id, "title": h.title, "slug": h.slug} for h in found[:10]]


def link_chat(chat_id: int, thread_id: int | None, hub_id: int, title: str, user_id: int) -> dict:
    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        if hub is None or hub.status != "active" or hub.kind == "teachers":
            raise HubError(t("hub.err.not_found"))
        link = session.scalar(select(HubChat).where(HubChat.chat_id == chat_id, HubChat.thread_id == (thread_id or 0)))
        if link is None:
            link = HubChat(chat_id=chat_id, thread_id=thread_id or 0)
            session.add(link)
        link.hub_id, link.title, link.linked_by = hub_id, (title or "")[:256], user_id
        if not hub.chat_url and chat_id:
            pass  # ссылку на чат бот сам не публикует: её добавляет куратор (приватные группы не должны всплывать)
        session.commit()
        return {"hub_id": hub_id, "title": hub.title}


def unlink_chat(chat_id: int, thread_id: int | None) -> bool:
    with get_sessionmaker()() as session:
        n = session.execute(delete(HubChat).where(HubChat.chat_id == chat_id, HubChat.thread_id == (thread_id or 0))).rowcount
        session.commit()
        return bool(n)


def set_digest(chat_id: int, thread_id: int | None, on: bool) -> bool:
    with get_sessionmaker()() as session:
        link = session.scalar(select(HubChat).where(HubChat.chat_id == chat_id, HubChat.thread_id == (thread_id or 0)))
        if link is None:
            raise HubError(t("hub.err.not_linked"))
        link.digest_on = on
        session.commit()
        return on


# ------------------------------------------------------------------ частые вопросы: «Сохрани ответ» и повторные вопросы


def message_link(chat_id: int, message_id: int, thread_id: int | None = None, username: str = "") -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(chat_id).removeprefix("-100") if str(chat_id).startswith("-100") else str(abs(chat_id))
    return f"https://t.me/c/{internal}/{(str(thread_id) + '/') if thread_id else ''}{message_id}"


def save_answer(chat_id: int, thread_id: int | None, message_id: int, question: str, answer: str, saver_id: int,
                author_id: int | None, link: str) -> dict:
    """Участник отметил удачный ответ. В FAQ хаба он попадёт после подтверждения ментора."""
    hub = hub_for_chat(chat_id, thread_id)
    if hub is None:
        raise HubError(t("hub.err.not_linked"))
    answer = (answer or "").strip()
    if len(answer) < 5:
        raise HubError(t("hub.err.answer_short"))
    from backend.app.services.community import HONESTY_RE

    if HONESTY_RE.search(answer):
        raise HubError(t("cm.post.err.honesty"))
    with get_sessionmaker()() as session:
        is_teacher_answer = bool(author_id and is_hub_teacher(session, hub["hub_id"], author_id))
        post = Post(kind="faq", hub_id=hub["hub_id"], author_hash=anon.author_hash(saver_id), title=" ".join((question or answer[:120]).split())[:256],
                    body=answer[:4000], status="pending", is_teacher=is_teacher_answer,
                    data_json=json.dumps({"link": link, "chat_id": chat_id, "thread_id": thread_id or 0, "message_id": message_id}, ensure_ascii=False))
        session.add(post)
        session.commit()
        return {"id": post.id, "hub": hub["title"], "is_teacher": is_teacher_answer}


def can_confirm(session, hub_id: int, user_id: int) -> bool:
    return is_owner(user_id) or member_role(session, hub_id, user_id) in ("mentor", "curator") or is_hub_teacher(session, hub_id, user_id)


def confirm_faq(post_id: int, user_id: int, chat_admin: bool = False, approve: bool = True) -> dict:
    with get_sessionmaker()() as session:
        post = session.get(Post, post_id)
        if post is None or post.kind != "faq":
            raise HubError(t("cm.post.err.not_found"))
        if not (chat_admin or can_confirm(session, post.hub_id, user_id)):
            raise HubError(t("hub.err.mentor_only"))
        post.status = "approved" if approve else "rejected"
        post.moderated_at = utcnow()
        if approve and is_hub_teacher(session, post.hub_id, user_id) and post.is_teacher is False:
            pass  # метку «ответ преподавателя» даёт только авторство ответа, а не подтверждение
        session.commit()
        return {"id": post.id, "status": post.status, "title": post.title}


def find_similar(hub_id: int, question: str) -> dict | None:
    """Повторный вопрос: если такой уже разбирали — сохранённый ответ (ответ преподавателя — в приоритете)."""
    q = set(tokenize(question))
    if len(q) < 2:
        return None
    best, best_score = None, 0.0
    with get_sessionmaker()() as session:
        for post in session.scalars(select(Post).where(Post.kind == "faq", Post.hub_id == hub_id, Post.status == "approved")):
            tokens = set(tokenize(post.title)) or set(tokenize(post.body[:300]))
            if not tokens:
                continue
            score = len(q & tokens) / min(len(q), len(tokens)) + (0.15 if post.is_teacher else 0) + min(post.score, 5) * 0.01
            if score > best_score:
                best, best_score = post, score
        if best is None or best_score < SIMILAR_THRESHOLD:
            return None
        data = json.loads(best.data_json or "{}")
        return {"id": best.id, "question": best.title, "answer": best.body, "link": data.get("link", ""), "is_teacher": best.is_teacher,
                "score": round(best_score, 2)}


# ------------------------------------------------------------------ дайджест темы


def digest_text(chat_id: int, thread_id: int, days: int = 7) -> str:
    """Что обсуждали (сохранённые ответы и проверки), что осталось без ответа, какие договорённости зафиксированы."""
    e = html.escape
    since = datetime.now(timezone.utc) - timedelta(days=days)
    hub = hub_for_chat(chat_id, thread_id)
    with get_sessionmaker()() as session:
        faq = list(session.scalars(select(Post).where(Post.kind == "faq", Post.hub_id == (hub or {}).get("hub_id", -1), Post.created_at >= since,
                                                     Post.status == "approved")))
        thread_filter = (Check.thread_id == thread_id) if thread_id else Check.thread_id.is_(None)
        checks = session.scalar(select(func.count()).select_from(Check).where(Check.chat_id == chat_id, thread_filter, Check.created_at >= since)) or 0
        agr_filter = (Agreement.thread_id == thread_id) if thread_id else Agreement.thread_id.is_(None)
        agreements = list(session.scalars(select(Agreement).where(Agreement.chat_id == chat_id, agr_filter, Agreement.created_at >= since)))
        open_q = list(session.scalars(select(Post).where(Post.kind == "senior_q", Post.hub_id == (hub or {}).get("hub_id", -1), Post.status == "approved")))
        unanswered = [q for q in open_q if not session.scalar(select(Post.id).where(Post.parent_id == q.id, Post.status == "approved"))]
    lines = [f"🗞 <b>{e(t('hub.digest.title'))}</b>" + (f" · {e(hub['title'])}" if hub else ""), ""]
    lines.append(e(t("hub.digest.checks", n=checks)))
    if faq:
        lines += ["", f"<b>{e(t('hub.digest.saved'))}</b>"] + [f"• {e(p.title[:120])}" for p in faq[:8]]
    if unanswered:
        lines += ["", f"<b>{e(t('hub.digest.unanswered'))}</b>"] + [f"• {e(q.title[:120])}" for q in unanswered[:8]]
    if agreements:
        lines += ["", f"<b>{e(t('hub.digest.agreements'))}</b>"]
        for a in agreements[:8]:
            what = json.loads(a.draft_json or "{}").get("what") or a.text
            lines.append(f"• {e(what[:120])}" + (f" — {e(a.deadline_iso)}" if a.deadline_iso else ""))
    if len(lines) <= 3:
        lines.append(e(t("hub.digest.quiet")))
    lines += ["", f"<i>{e(t('hub.digest.privacy'))}</i>"]
    return "\n".join(lines)


def digest_targets() -> list[tuple[int, int]]:
    with get_sessionmaker()() as session:
        return [(c.chat_id, c.thread_id) for c in session.scalars(select(HubChat).where(HubChat.digest_on.is_(True)))]


# ------------------------------------------------------------------ преподавателю: план курса, карта нагрузки, сводка


def _week(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def load_map(hub_id: int, due: str, user_id: int) -> dict:
    """Сколько у этой группы уже опубликованных дедлайнов на ту же неделю по другим предметам.

    Считаются только дедлайны, опубликованные преподавателями в хабах. Личные задачи студентов сюда не попадают.
    «Та же группа» — участники хаба, которые состоят и в других хабах, либо тот же курс и программа.
    """
    try:
        day = date.fromisoformat(due)
    except ValueError as exc:
        raise HubError(t("pl.err.date")) from exc
    start, end = _week(day)
    with get_sessionmaker()() as session:
        if not (is_hub_teacher(session, hub_id, user_id) or is_owner(user_id)):
            raise HubError(t("hub.err.verified_only"))
        hub = session.get(Hub, hub_id)
        members = {m.user_id for m in session.scalars(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.role == "member"))}
        related: set[int] = set()
        if members:
            for m in session.scalars(select(HubMember).where(HubMember.user_id.in_(members), HubMember.hub_id != hub_id)):
                related.add(m.hub_id)
        if hub and hub.course:
            related |= {h.id for h in session.scalars(select(Hub).where(Hub.id != hub_id, Hub.course == hub.course,
                                                                       (Hub.program == hub.program) if hub.program else true()))}
        titles = {h.id: h.title for h in session.scalars(select(Hub).where(Hub.id.in_(related or {0})))}
        items = []
        for post in session.scalars(select(Post).where(Post.kind == "hub_deadline", Post.hub_id.in_(related or {0}), Post.status == "approved")):
            d = str(json.loads(post.data_json or "{}").get("due_date", ""))
            if start.isoformat() <= d <= end.isoformat():
                items.append({"hub": titles.get(post.hub_id, ""), "title": post.title, "due_date": d})
    level = "red" if len(items) >= 3 else "yellow" if items else "green"
    return {"week_start": start.isoformat(), "week_end": end.isoformat(), "count": len(items), "items": sorted(items, key=lambda x: x["due_date"]),
            "level": level, "text": t(f"hub.load.{level}", n=len(items))}


async def publish_deadline(hub_id: int, user_id: int, user_name: str, title: str, due_date: str, due_time: str = "", note: str = "") -> dict:
    """План курса: дедлайн дисциплины → в хаб; у участников хаба он появляется ПРЕДЛОЖЕННОЙ задачей."""
    from backend.app.services import community, planner, prefs

    post = community.create_post("hub_deadline", user_id, user_name, title=title, body=note, hub_id=hub_id,
                                 data={"due_date": due_date, "due_time": due_time})
    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        members = [m.user_id for m in session.scalars(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.role == "member"))]
        hub_title = hub.title if hub else ""
    suggested = 0
    for uid in members:
        suggested += planner.suggest(uid, title, "hub", f"deadline:{post['id']}", due_date=due_date, due_time=due_time, subject=hub_title[:64], note=note)
    notifier = get_notifier()
    if notifier:
        text = html.escape(t("hub.deadline.notify", hub=hub_title, title=title, due=due_date + (f" {due_time}" if due_time else "")))
        for uid in set(members) & set(prefs.subscribers("sub.hub_deadlines")):
            await notifier.send(uid, text)
    return {**post, "suggested": suggested}


def subject_summary(hub_id: int, user_id: int) -> dict:
    """Сводка по предмету: какие темы вызывают больше всего вопросов и что осталось без ответа — без имён студентов."""
    from collections import Counter

    from backend.app.services.polls import STOPWORDS

    with get_sessionmaker()() as session:
        if not (is_hub_teacher(session, hub_id, user_id) or is_owner(user_id)):
            raise HubError(t("hub.err.verified_only"))
        questions = list(session.scalars(select(Post).where(Post.hub_id == hub_id, Post.kind.in_(("senior_q", "lecture_q", "faq")),
                                                           Post.status == "approved")))
        unanswered = [q for q in questions if q.kind == "senior_q" and not session.scalar(
            select(Post.id).where(Post.parent_id == q.id, Post.status == "approved"))]
    words = Counter(w for q in questions for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", f"{q.title} {q.body[:200]}".lower()) if w not in STOPWORDS)
    return {"questions": len(questions), "topics": [{"word": w, "count": c} for w, c in words.most_common(10) if c >= 2],
            "unanswered": [q.title for q in unanswered[:20]], "faq": sum(q.kind == "faq" for q in questions)}


async def announce(hub_id: int, user_id: int, user_name: str, title: str, text: str) -> dict:
    """Объявление преподавателя в кабинете дисциплины — с подтверждением прочтения."""
    from backend.app.services import community, prefs

    post = community.create_post("announce", user_id, user_name, title=title, body=text, hub_id=hub_id)
    with get_sessionmaker()() as session:
        hub = session.get(Hub, hub_id)
        members = [m.user_id for m in session.scalars(select(HubMember).where(HubMember.hub_id == hub_id, HubMember.role == "member"))]
    notifier = get_notifier()
    if notifier:
        msg = html.escape(t("hub.announce.notify", hub=hub.title if hub else "", title=title))
        for uid in set(members) & set(prefs.subscribers("sub.hub_deadlines")):
            await notifier.send(uid, msg, [[(t("hub.btn.ack"), f"ack:{post['id']}")]])
    return post


def delete_user(session, user_id: int) -> None:
    session.execute(delete(HubMember).where(HubMember.user_id == user_id))
    session.execute(delete(Application).where(Application.user_id == user_id))

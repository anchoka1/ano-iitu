"""«Группы и потоки» — университетская версия «Семьи».

Что умеет:
  - создать группу: учебная группа, поток, группа с преподавателем, проектная команда, клуб;
  - пригласить по ссылке t.me/бот?start=grp_ТОКЕН или кнопкой «Вступить» прямо в чате группы (/gruppa);
  - предупреждения: если участник получил 🔴 в «Развод?» или «Чек», остальным приходит
    АНОНИМНОЕ предупреждение (без имени и без текста сообщения) — «похожее может прийти и вам»;
  - объявления: владелец и админы рассылают объявление участникам (не больше POSTS_PER_DAY в сутки);
  - договорённость с группой: каждый участник получает её с кнопками и подтверждает сам;
  - ближайшие даты академкалендаря для курса группы.

Права. Роль «преподаватель» в профиле прав не даёт. Права есть только у того, кто создал
группу (owner), и у тех, кого он назначил админами. Участник в любой момент может выйти
или отключить уведомления. Вступление — только добровольно.
"""

from __future__ import annotations

import asyncio
import html
import json
import secrets
from datetime import date, timedelta

from sqlalchemy import delete, func, select

from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import Agreement, Circle, CircleMember, CirclePost, User, iso_utc, utcnow
from backend.app.i18n import t

KINDS = {
    "group": ("🎓", "Учебная группа"),
    "stream": ("👥", "Поток"),
    "teacher": ("👩‍🏫", "Группа с преподавателем"),
    "team": ("🛠", "Проектная команда"),
    "club": ("🎉", "Клуб / сообщество"),
}
MAX_MEMBERS = 300
MAX_CIRCLES_PER_USER = 20
POSTS_PER_DAY = 5
MAX_POST_CHARS = 1500


class CircleError(Exception):
    pass


def kind_label(kind: str) -> str:
    emoji, title = KINDS.get(kind, KINDS["group"])
    return f"{emoji} {title}"


def _member(session, circle_id: int, user_id: int) -> CircleMember | None:
    return session.scalar(select(CircleMember).where(CircleMember.circle_id == circle_id, CircleMember.user_id == user_id))


def _members(session, circle_id: int) -> list[CircleMember]:
    return list(session.scalars(select(CircleMember).where(CircleMember.circle_id == circle_id).order_by(CircleMember.joined_at)))


def _circle_dict(session, circle: Circle, user_id: int | None = None, with_members: bool = False) -> dict:
    members = _members(session, circle.id)
    me = next((m for m in members if m.user_id == user_id), None)
    data = {
        "id": circle.id, "kind": circle.kind, "kind_label": kind_label(circle.kind), "title": circle.title,
        "invite_token": circle.invite_token if me else "", "course": circle.course, "linked_chat": bool(circle.chat_id),
        "members_count": len(members), "created_at": iso_utc(circle.created_at),
        "my_role": me.role if me else "", "alerts": me.alerts if me else False, "posts": me.posts if me else False,
    }
    if with_members and me:
        users = {u.telegram_id: u for u in session.scalars(select(User).where(User.telegram_id.in_([m.user_id for m in members])))}
        data["members"] = [{
            "user_id": m.user_id, "name": (users[m.user_id].first_name if m.user_id in users else "") or "Участник",
            "role": m.role, "can_receive": bool(m.user_id in users and users[m.user_id].bot_started),
        } for m in members]
    return data


# ------------------------------------------------------------------ создание и вступление


def create(user_id: int, user_name: str, title: str, kind: str = "group", course: str = "", chat_id: int | None = None) -> dict:
    title = " ".join((title or "").split())[:128]
    if len(title) < 2:
        raise CircleError(t("grp.err.title"))
    if kind not in KINDS:
        kind = "group"
    with get_sessionmaker()() as session:
        repo.upsert_user(session, user_id, user_name)
        count = session.scalar(select(func.count()).select_from(CircleMember).where(CircleMember.user_id == user_id)) or 0
        if count >= MAX_CIRCLES_PER_USER:
            raise CircleError(t("grp.err.too_many", n=MAX_CIRCLES_PER_USER))
        circle = Circle(kind=kind, title=title, invite_token=secrets.token_urlsafe(9), owner_id=user_id, course=course[:8], chat_id=chat_id)
        session.add(circle)
        session.flush()
        session.add(CircleMember(circle_id=circle.id, user_id=user_id, role="owner"))
        session.commit()
        return _circle_dict(session, circle, user_id, with_members=True)


def by_token(token: str) -> dict | None:
    with get_sessionmaker()() as session:
        circle = session.scalar(select(Circle).where(Circle.invite_token == token))
        return _circle_dict(session, circle) if circle else None


def for_chat(chat_id: int) -> dict | None:
    with get_sessionmaker()() as session:
        circle = session.scalar(select(Circle).where(Circle.chat_id == chat_id))
        return _circle_dict(session, circle) if circle else None


def join(user_id: int, user_name: str, token: str) -> dict:
    with get_sessionmaker()() as session:
        circle = session.scalar(select(Circle).where(Circle.invite_token == token))
        if circle is None:
            raise CircleError(t("grp.err.not_found"))
        repo.upsert_user(session, user_id, user_name)
        if _member(session, circle.id, user_id) is None:
            if len(_members(session, circle.id)) >= MAX_MEMBERS:
                raise CircleError(t("grp.err.full", n=MAX_MEMBERS))
            count = session.scalar(select(func.count()).select_from(CircleMember).where(CircleMember.user_id == user_id)) or 0
            if count >= MAX_CIRCLES_PER_USER:
                raise CircleError(t("grp.err.too_many", n=MAX_CIRCLES_PER_USER))
            session.add(CircleMember(circle_id=circle.id, user_id=user_id))
            session.commit()
        return _circle_dict(session, circle, user_id, with_members=True)


def list_for_user(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        ids = select(CircleMember.circle_id).where(CircleMember.user_id == user_id)
        circles = list(session.scalars(select(Circle).where(Circle.id.in_(ids)).order_by(Circle.created_at)))
        return [_circle_dict(session, c, user_id) for c in circles]


def info(circle_id: int, user_id: int) -> dict:
    with get_sessionmaker()() as session:
        circle = session.get(Circle, circle_id)
        if circle is None or _member(session, circle_id, user_id) is None:
            raise CircleError(t("grp.err.not_member"))
        data = _circle_dict(session, circle, user_id, with_members=True)
        posts = session.scalars(select(CirclePost).where(CirclePost.circle_id == circle_id).order_by(CirclePost.created_at.desc()).limit(10))
        data["posts_list"] = [{"id": p.id, "author": p.author_name, "text": p.text, "created_at": iso_utc(p.created_at)} for p in posts]
        agreements = session.scalars(select(Agreement).where(Agreement.circle_id == circle_id).order_by(Agreement.created_at.desc()).limit(10))
        data["agreements"] = [{"code": a.code, "status": a.status, "what": json.loads(a.draft_json or "{}").get("what") or a.text}
                              for a in agreements]
        if circle.course:
            from backend.app.university import calendar

            data["upcoming"] = [e.public() for e in calendar.next_events(circle.course, date.today(), limit=4)
                                if e.kind not in ("study", "holidays")]
        return data


def leave(circle_id: int, user_id: int) -> None:
    """Выйти. Если уходит владелец — владельцем становится старший админ или самый «старый» участник."""
    with get_sessionmaker()() as session:
        me = _member(session, circle_id, user_id)
        if me is None:
            return
        session.delete(me)
        session.flush()
        rest = _members(session, circle_id)
        circle = session.get(Circle, circle_id)
        if not rest:
            session.execute(delete(CirclePost).where(CirclePost.circle_id == circle_id))
            session.delete(circle)
        elif me.role == "owner":
            heir = next((m for m in rest if m.role == "admin"), rest[0])
            heir.role = "owner"
            circle.owner_id = heir.user_id
        session.commit()


def _require_admin(session, circle_id: int, user_id: int) -> CircleMember:
    me = _member(session, circle_id, user_id)
    if me is None or me.role not in ("owner", "admin"):
        raise CircleError(t("grp.err.admin_only"))
    return me


def set_role(circle_id: int, owner_id: int, target_id: int, role: str) -> dict:
    if role not in ("admin", "member"):
        raise CircleError(t("grp.err.bad_role"))
    with get_sessionmaker()() as session:
        me = _member(session, circle_id, owner_id)
        if me is None or me.role != "owner":
            raise CircleError(t("grp.err.owner_only"))
        target = _member(session, circle_id, target_id)
        if target is None or target.role == "owner":
            raise CircleError(t("grp.err.not_member"))
        target.role = role
        session.commit()
        return _circle_dict(session, session.get(Circle, circle_id), owner_id, with_members=True)


def remove_member(circle_id: int, admin_id: int, target_id: int) -> dict:
    with get_sessionmaker()() as session:
        me = _require_admin(session, circle_id, admin_id)
        target = _member(session, circle_id, target_id)
        if target is None or target.role == "owner" or (target.role == "admin" and me.role != "owner"):
            raise CircleError(t("grp.err.cannot_remove"))
        session.delete(target)
        session.commit()
        return _circle_dict(session, session.get(Circle, circle_id), admin_id, with_members=True)


def update_settings(circle_id: int, user_id: int, alerts: bool | None = None, posts: bool | None = None) -> dict:
    with get_sessionmaker()() as session:
        me = _member(session, circle_id, user_id)
        if me is None:
            raise CircleError(t("grp.err.not_member"))
        if alerts is not None:
            me.alerts = alerts
        if posts is not None:
            me.posts = posts
        session.commit()
        return _circle_dict(session, session.get(Circle, circle_id), user_id)


def delete_circle(circle_id: int, owner_id: int) -> None:
    with get_sessionmaker()() as session:
        me = _member(session, circle_id, owner_id)
        if me is None or me.role != "owner":
            raise CircleError(t("grp.err.owner_only"))
        session.execute(delete(CircleMember).where(CircleMember.circle_id == circle_id))
        session.execute(delete(CirclePost).where(CirclePost.circle_id == circle_id))
        for agreement in session.scalars(select(Agreement).where(Agreement.circle_id == circle_id)):
            agreement.circle_id = None  # договорённости остаются у участников
        session.delete(session.get(Circle, circle_id))
        session.commit()


def link_chat(chat_id: int, chat_title: str, user_id: int, user_name: str, kind: str = "group") -> dict:
    """Группа для чата Telegram: есть — вернуть, нет — создать (создатель — тот, кто вызвал /gruppa)."""
    existing = for_chat(chat_id)
    if existing:
        return existing
    return create(user_id, user_name, chat_title or "Группа", kind, chat_id=chat_id)


def invite_token(circle_id: int) -> str:
    """Токен приглашения — для кнопки «Вступить» в привязанном чате Telegram."""
    with get_sessionmaker()() as session:
        circle = session.get(Circle, circle_id)
        return circle.invite_token if circle else ""


def is_member(circle_id: int, user_id: int) -> bool:
    with get_sessionmaker()() as session:
        return _member(session, circle_id, user_id) is not None


# ------------------------------------------------------------------ рассылки


def _recipients(circle_id: int, exclude: int | None, field: str) -> tuple[str, list[int]]:
    with get_sessionmaker()() as session:
        circle = session.get(Circle, circle_id)
        rows = session.execute(
            select(CircleMember.user_id).join(User, User.telegram_id == CircleMember.user_id)
            .where(CircleMember.circle_id == circle_id, getattr(CircleMember, field).is_(True), User.bot_started.is_(True))
        ).all()
        return (circle.title if circle else ""), [r[0] for r in rows if r[0] != exclude]


async def post_announcement(circle_id: int, author_id: int, author_name: str, text: str) -> dict:
    text = (text or "").strip()
    if len(text) < 3:
        raise CircleError(t("grp.err.empty"))
    if len(text) > MAX_POST_CHARS:
        raise CircleError(t("grp.err.too_long", n=MAX_POST_CHARS))
    with get_sessionmaker()() as session:
        _require_admin(session, circle_id, author_id)
        since = utcnow() - timedelta(days=1)
        today_posts = session.scalar(select(func.count()).select_from(CirclePost).where(
            CirclePost.circle_id == circle_id, CirclePost.created_at >= since)) or 0
        if today_posts >= POSTS_PER_DAY:
            raise CircleError(t("grp.err.post_limit", n=POSTS_PER_DAY))
        post = CirclePost(circle_id=circle_id, author_id=author_id, author_name=author_name[:128], text=text)
        session.add(post)
        session.commit()
        post_id = post.id
    title, users = _recipients(circle_id, author_id, "posts")
    notifier = get_notifier()
    sent = 0
    if notifier is not None:
        message = t("grp.post_msg", title=html.escape(title), author=html.escape(author_name), text=html.escape(text))
        for user_id in users:
            sent += bool(await notifier.send(user_id, message))
            await asyncio.sleep(0.05)
    with get_sessionmaker()() as session:
        session.get(CirclePost, post_id).sent = sent
        session.commit()
    return {"id": post_id, "sent": sent, "recipients": len(users)}


async def alert_members(user_id: int, card_title: str) -> int:
    """Анонимное предупреждение группам человека, получившего 🔴: без имени и без текста сообщения."""
    notifier = get_notifier()
    if notifier is None:
        return 0
    with get_sessionmaker()() as session:
        circle_ids = [c for c in session.scalars(select(CircleMember.circle_id).where(CircleMember.user_id == user_id))]
    sent = 0
    already: set[int] = set()
    for circle_id in circle_ids:
        title, users = _recipients(circle_id, user_id, "alerts")
        text = t("grp.alert", title=html.escape(title), verdict=html.escape(card_title))
        for member in users:
            if member in already:
                continue  # одно предупреждение на человека, даже если он в нескольких общих группах
            already.add(member)
            sent += bool(await notifier.send(member, text))
    return sent


async def create_agreement(circle_id: int, author_id: int, author_name: str, text: str) -> dict:
    """Договорённость с группой: создаёт групповую договорённость и рассылает участникам с кнопками."""
    from backend.app.services import agreements as agreements_service

    with get_sessionmaker()() as session:
        _require_admin(session, circle_id, author_id)
    agreement = await agreements_service.create_agreement(text, author_id, author_name, multi=True)
    with get_sessionmaker()() as session:
        row = repo.get_agreement(session, agreement.code)
        row.circle_id = circle_id
        session.commit()
        agreement = row
    title, users = _recipients(circle_id, author_id, "posts")
    notifier = get_notifier()
    sent = 0
    if notifier is not None:
        body = t("grp.agr_msg", title=html.escape(title)) + "\n\n" + agreements_service.render_html(agreement)
        buttons = [[(t("agr.btn.confirm"), f"agr:ok:{agreement.code}"), (t("agr.btn.decline"), f"agr:no:{agreement.code}")]]
        for user_id in users:
            sent += bool(await notifier.send(user_id, body, buttons))
            await asyncio.sleep(0.05)
    return {**agreements_service.to_dict(agreement), "sent": sent}


# ------------------------------------------------------------------ приватность


def delete_user(session, user_id: int) -> None:
    """Для /delete: выйти из всех групп (с передачей владения)."""
    circle_ids = list(session.scalars(select(CircleMember.circle_id).where(CircleMember.user_id == user_id)))
    session.commit()
    for circle_id in circle_ids:
        leave(circle_id, user_id)


def export_user(session, user_id: int) -> list[dict]:
    rows = session.execute(select(Circle.title, Circle.kind, CircleMember.role, CircleMember.joined_at)
                           .join(CircleMember, CircleMember.circle_id == Circle.id).where(CircleMember.user_id == user_id)).all()
    return [{"title": r[0], "kind": r[1], "role": r[2], "joined_at": iso_utc(r[3])} for r in rows]

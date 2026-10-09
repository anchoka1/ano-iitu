"""Режим «Семья».

Как работает:
  - Человек создаёт семью и получает ссылку-приглашение
    t.me/бот?start=fam_ТОКЕН (deep link — ссылка, которая открывает бота
    с параметром).
  - Близкий открывает ссылку, нажимает «Старт» и соглашается войти в семью.
  - Если кто-то из семьи получает карточку 🔴 в режимах /razvod или /chek,
    остальным приходит уведомление (если они не отключили уведомления).

Ограничение Telegram: бот не может написать первым человеку, который ни
разу не нажал /start. Поэтому уведомления получают только те, кто открыл бота.

Семейное кодовое слово: слово, которое знают только члены семьи. Его
спрашивают, если «родственник» звонит с незнакомого номера или голос
кажется странным (подделка голоса ИИ). Само слово мы НЕ храним — только
отметку «договорились», чтобы напоминать о нём.
"""

from __future__ import annotations

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker


class FamilyError(Exception):
    pass


def info(user_id: int) -> dict:
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        if user is None or not user.family_id:
            return {"has_family": False, "notify": True, "code_word_set": bool(user and user.code_word_set)}
        family = repo.get_family(session, user.family_id)
        members = repo.family_members(session, user.family_id)
        return {
            "has_family": True,
            "is_owner": family is not None and family.owner_id == user_id,
            "invite_token": family.invite_token if family else "",
            "notify": user.family_notify,
            "code_word_set": user.code_word_set,
            "members": [{"name": m.first_name or "Без имени", "is_me": m.telegram_id == user_id,
                         "can_receive": m.bot_started} for m in members],
        }


def create(user_id: int, user_name: str) -> dict:
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, user_name)
        if not user.family_id:
            repo.create_family(session, user)
    return info(user_id)


def join(user_id: int, user_name: str, token: str) -> dict:
    with get_sessionmaker()() as session:
        family = repo.family_by_token(session, token)
        if family is None:
            raise FamilyError("Приглашение не найдено или устарело. Попросите новую ссылку.")
        user = repo.upsert_user(session, user_id, user_name)
        if user.family_id and user.family_id != family.id:
            raise FamilyError("Вы уже состоите в другой семье. Сначала выйдите из неё в приложении.")
        user.family_id = family.id
        session.commit()
    return info(user_id)


def leave(user_id: int) -> dict:
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        if user and user.family_id:
            repo.leave_family(session, user)
    return info(user_id)


def update_settings(user_id: int, notify: bool | None = None, code_word_set: bool | None = None) -> dict:
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        if user is None:
            raise FamilyError("Сначала откройте приложение.")
        if notify is not None:
            user.family_notify = notify
        if code_word_set is not None:
            user.code_word_set = code_word_set
        session.commit()
    return info(user_id)

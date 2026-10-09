"""Личные настройки и подписки (таблица prefs).

Правило: все уведомления новых функций — по подписке и ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНЫ.
Исключение — напоминания о договорённостях, в которых человек участвует сам
(они работают как раньше, через agreements).
"""

from __future__ import annotations

from sqlalchemy import delete, select

from backend.app.db.base import get_sessionmaker
from backend.app.db.models_campus import Pref

# Подписки: ключ → (флаг функции, подпись для интерфейса).
SUBSCRIPTIONS: dict[str, tuple[str, str]] = {
    "sub.rumor": ("rumor_week", "Слух недели"),
    "sub.morning": ("morning_digest", "Утренняя сводка"),
    "sub.frog": ("planner_frog", "Лягушка дня (утром)"),
    "sub.traffic": ("planner_traffic", "Светофор недели (в пятницу)"),
    "sub.week_verdict": ("planner_week_verdict", "Итоги недели (в воскресенье)"),
    "sub.pulse": ("pulse", "Пульс МУИТ (вопрос недели)"),
    "sub.radar": ("scam_radar", "Радар разводов"),
    "sub.events": ("events", "Напоминания о событиях, куда я иду"),
    "sub.hub_deadlines": ("course_plan", "Новые дедлайны в моих хабах"),
}

# Настройки планера и их значения по умолчанию.
PLAN_DEFAULTS = {
    "plan.remind": "0",           # напоминания о задачах — выключены, пока человек сам не включит
    "plan.remind_offset": "60",   # за сколько минут предупреждать (для задач без времени — от 09:00)
    "plan.quiet": "23-8",         # тихие часы: с 23 до 8 бот ничего не присылает
}


def get(user_id: int, key: str, default: str = "") -> str:
    with get_sessionmaker()() as session:
        row = session.get(Pref, (user_id, key))
        if row is not None:
            return row.value
    return PLAN_DEFAULTS.get(key, default)


def is_set(user_id: int, key: str) -> bool:
    """Человек сам менял эту настройку (а не значение по умолчанию)."""
    with get_sessionmaker()() as session:
        return session.get(Pref, (user_id, key)) is not None


def get_bool(user_id: int, key: str) -> bool:
    return get(user_id, key, "0") == "1"


def set_value(user_id: int, key: str, value: str) -> None:
    with get_sessionmaker()() as session:
        row = session.get(Pref, (user_id, key)) or Pref(user_id=user_id, key=key)
        row.value = str(value)[:256]
        session.add(row)
        session.commit()


def all_for(user_id: int) -> dict[str, str]:
    with get_sessionmaker()() as session:
        values = {r.key: r.value for r in session.scalars(select(Pref).where(Pref.user_id == user_id))}
    return {**PLAN_DEFAULTS, **values}


def subscribers(key: str) -> list[int]:
    """Кто подписан (value=1) и может получать сообщения от бота."""
    from backend.app.db.models import User

    with get_sessionmaker()() as session:
        rows = session.execute(
            select(Pref.user_id).join(User, User.telegram_id == Pref.user_id)
            .where(Pref.key == key, Pref.value == "1", User.bot_started.is_(True))
        ).all()
    return [r[0] for r in rows]


def subscriptions_view(user_id: int) -> list[dict]:
    from backend.app.core.features import enabled

    values = all_for(user_id)
    return [{"key": key, "title": title, "on": values.get(key) == "1"}
            for key, (flag, title) in SUBSCRIPTIONS.items() if enabled(flag)]


def quiet_now(user_id: int, hour: int) -> bool:
    """Сейчас тихие часы пользователя? Формат «23-8»: с 23:00 до 08:00."""
    raw = get(user_id, "plan.quiet")
    try:
        start, end = (int(x) for x in raw.split("-", 1))
    except ValueError:
        return False
    if start == end:
        return False
    return start <= hour or hour < end if start > end else start <= hour < end


def delete_user(session, user_id: int) -> None:
    session.execute(delete(Pref).where(Pref.user_id == user_id))

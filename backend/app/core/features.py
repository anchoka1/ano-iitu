"""Флаги функций: каждую новую функцию можно выключить в .env, не трогая код.

Имя переменной = FEATURE_<КЛЮЧ>, например FEATURE_GPA=false — спрятать
GPA-калькулятор. По умолчанию всё включено. Выключенная функция пропадает
из Mini App (экран не показывается), её команды в боте отвечают «функция
выключена», а API возвращает 404.

Модерируют контент и подтверждают статус преподавателя модераторы и админы
(services/roles.py): в очереди Mini App, в чате модераторов и командами бота.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException
from pydantic import create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.config import PROJECT_ROOT

# Ключ → (группа, короткое описание). Порядок — как в документе с заданием.
FEATURES: dict[str, tuple[str, str]] = {
    # Планер «Мой план»
    "planner": ("plan", "Планер и трекер задач"),
    "planner_frog": ("plan", "Лягушка дня"),
    "planner_matrix": ("plan", "Матрица «срочно / важно»"),
    "planner_steps": ("plan", "Разбей на шаги"),
    "planner_traffic": ("plan", "Светофор недели"),
    "planner_promise": ("plan", "Договор с собой и свидетель"),
    "planner_week_verdict": ("plan", "Итоги недели"),
    "planner_team_board": ("plan", "Командная доска"),
    # Волна 1
    "rumor_week": ("wave1", "Слух недели"),
    "fact_feed": ("wave1", "Слухи и факты: проверка слухов модератором"),
    "gpa": ("wave1", "GPA-калькулятор"),
    "countdown": ("wave1", "Обратный отсчёт"),
    "agreement_deadlines": ("wave1", "Дедлайны из «Договорились»"),
    "syllabus": ("wave1", "Разбор силлабуса"),
    "class_poll": ("wave1", "Опрос после пары"),
    # Волна 2
    "pulse": ("wave2", "Пульс МУИТ"),
    "ask_senior": ("wave2", "Спроси старшекурсника"),
    "checklist": ("wave2", "Мой курс: чек-лист"),
    "morning_digest": ("wave2", "Утренняя сводка"),
    "appeal_helper": ("wave2", "Помощник обращений"),
    "scam_radar": ("wave2", "Радар разводов"),
    "ai_rules": ("wave2", "Правила ИИ на курсе"),
    "lecture_questions": ("wave2", "Вопросы перед лекцией"),
    "consultations": ("wave2", "Запись на консультацию"),
    "agreement_report": ("wave2", "Отчёт по договорённости"),
    "group_pulse": ("wave2", "Пульс группы"),
    # Волна 3
    "group_rating": ("wave3", "Рейтинг групп"),
    "team_search": ("wave3", "Поиск команды"),
    "lost_found": ("wave3", "Потеряшки"),
    "events": ("wave3", "Афиша и клубы"),
    "focus_room": ("wave3", "Фокус-комната"),
    "quiz": ("wave3", "Квиз первокурсника"),
    "subject_reviews": ("wave3", "Отзывы о предметах"),
    "career": ("wave3", "Карьерный трек"),
    "resources": ("wave3", "Полка ресурсов"),
    "time_capsule": ("wave3", "Капсула времени"),
    "badges": ("wave3", "Значки"),
    # Учебные хабы
    "hubs": ("hubs", "Каталог учебных хабов"),
    "hub_topics": ("hubs", "Бот в группах с темами"),
    "hub_faq": ("hubs", "«Сохрани ответ» и повторные вопросы"),
    "hub_digest": ("hubs", "Дайджест темы"),
    "mentors": ("hubs", "Менторы"),
    "teacher_cabinet": ("hubs", "Кабинет дисциплины и «Ответ преподавателя»"),
    "teachers_room": ("hubs", "Преподавательская"),
    "course_plan": ("hubs", "План курса и карта нагрузки"),
}


class _FlagsBase(BaseSettings):
    """FEATURE_<КЛЮЧ>=true|false в .env."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", env_prefix="FEATURE_", extra="ignore")


# Поле на каждый ключ (по умолчанию True): pydantic сам прочитает FEATURE_GPA и приведёт "false" к False.
FeatureFlags = create_model("FeatureFlags", __base__=_FlagsBase, **{key: (bool, True) for key in FEATURES})


@lru_cache
def get_flags() -> _FlagsBase:
    return FeatureFlags()


def enabled(key: str) -> bool:
    if key not in FEATURES:
        return False
    return bool(getattr(get_flags(), key, True))


def all_flags() -> dict[str, bool]:
    return {key: enabled(key) for key in FEATURES}


def require(key: str) -> None:
    """Для API: выключенная функция — 404, как будто её нет."""
    if not enabled(key):
        raise HTTPException(status_code=404, detail="Эта функция сейчас выключена.")


def owner_ids() -> set[int]:
    """Кто модерирует: админы и модераторы (services/roles.py). Старое имя оставлено для совместимости."""
    from backend.app.services.roles import moderator_ids

    return moderator_ids()


def is_owner(user_id: int) -> bool:
    """Право модерировать и решать заявки — у модераторов и админов (проверяется на сервере)."""
    from backend.app.services.roles import is_moderator

    return is_moderator(user_id)

"""Обработчики бота, разложенные по темам.

Порядок подключения важен: aiogram проверяет роутеры по очереди, и первый
подходящий обработчик «забирает» сообщение. Поэтому общий обработчик
«любое сообщение в личке» (fallback) подключаем последним.
"""

from __future__ import annotations

from aiogram import Router

from backend.app.telegram.handlers import (
    agreements, campus, cards, checks, circles, common, family, flows, groups, hubs, moderation, onboarding, planner, university,
)


def build_router() -> Router:
    root = Router(name="root")
    # Новые разделы (модерация, хабы, функции волн, планер) — перед checks: там общий обработчик лички.
    for module in (common, onboarding, university, circles, family, groups, agreements, cards,
                   moderation, flows, hubs, campus, planner, checks):
        root.include_router(module.router)
    return root

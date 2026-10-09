"""Профиль бота в Telegram: описание, короткое описание и меню команд.

Суть: то, что обычно настраивают руками в @BotFather (/setdescription,
/setabouttext, /setcommands), мы задаём кодом через Bot API. Тексты
лежат в ru.json, и при каждом запуске бот сверяет их с тем, что сейчас
в Telegram, и обновляет только то, что изменилось.

Где что видно:
  short_description — в профиле бота и в ссылке на бота (до 120 символов);
  description       — на пустом экране чата до нажатия «Старт» (до 512);
  commands          — меню слева от поля ввода и подсказки при вводе «/».
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.i18n import t
from backend.app.modes.registry import MODES

log = logging.getLogger("verdikt.bot")

# Меню повторяет разделы Mini App: Проверка информации (режимы + Радар, проверенные слухи) → Учёба → IITU Hub → Профиль.
# Остальные команды (/navigator, /kalendar, /gpa, /hubs, /gruppa, /opros...) работают, но в меню не мешают.
MENU_MODES = ("pravda", "razvod", "spor", "dogovorilis", "chek", "prava", "sprosi")
EXTRA_COMMANDS = ("radar", "fakty", "plan", "syllabus", "put", "news", "moi", "settings", "help", "delete")


def build_commands(lang: str = "ru") -> list[tuple[str, str]]:
    """Список команд меню (команда, описание): режимы проверки, затем разделы и /help, /delete."""
    commands = [(m.command, t(f"bot.cmd.{m.command}", lang)) for m in MODES if m.command in MENU_MODES]
    commands += [(c, t(f"bot.cmd.{c}", lang)) for c in EXTRA_COMMANDS]
    return commands


async def apply_menu_button(bot: Bot, lang: str = "ru") -> None:
    """Кнопка слева от поля ввода: открывает Mini App (если есть HTTPS-адрес) или меню команд.

    Это то же самое, что @BotFather → Bot Settings → Menu Button, только кодом.
    Без HTTPS-адреса возвращаем обычное меню команд, чтобы не осталась ссылка
    на старый (уже не работающий) адрес туннеля.
    """
    settings = get_settings()
    if settings.webapp_is_https:
        button = MenuButtonWebApp(text=t("bot.menu_button", lang), web_app=WebAppInfo(url=settings.webapp_url))
    else:
        button = MenuButtonCommands()
    await bot.set_chat_menu_button(menu_button=button)
    log.info("Кнопка меню: %s", settings.webapp_url if settings.webapp_is_https else "список команд")


async def apply_profile(bot: Bot, lang: str = "ru") -> None:
    """Сверяет профиль бота с ru.json и обновляет изменившееся.

    Сначала читаем текущие значения (get_*), чтобы не отправлять лишние
    запросы: у Telegram есть ограничения на частоту изменений профиля.
    """
    short = t("bot.short_description", lang)
    if (await bot.get_my_short_description()).short_description != short:
        await bot.set_my_short_description(short_description=short)
        log.info("Обновлено короткое описание бота.")

    description = t("bot.description", lang)
    if (await bot.get_my_description()).description != description:
        await bot.set_my_description(description=description)
        log.info("Обновлено описание бота.")

    await apply_menu_button(bot, lang)

    wanted = build_commands(lang)
    current = [(c.command, c.description) for c in await bot.get_my_commands()]
    if current != wanted:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in wanted])
        log.info("Обновлено меню команд бота (%d команд).", len(wanted))

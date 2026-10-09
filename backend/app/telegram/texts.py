"""Сборка ответов бота без привязки к aiogram.

Зачем отдельно: эти функции легко тестировать — на входе простые данные,
на выходе текст и описание кнопок. Хэндлеры только вызывают их и
отправляют результат в Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.i18n import t


@dataclass(frozen=True)
class StartReply:
    text: str
    webapp_url: str | None  # если задан — показать кнопку «Открыть приложение»


def build_start_reply(first_name: str, is_private: bool, webapp_url: str, lang: str = "ru") -> StartReply:
    """Ответ на /start.

    В группах кнопка web_app не работает (Telegram разрешает её только в
    личных чатах), поэтому там — короткое приветствие без кнопки.
    """
    if not is_private:
        return StartReply(text=t("bot.start.group", lang), webapp_url=None)

    text = t("bot.start.greeting", lang, name=first_name or "друг")
    if webapp_url.lower().startswith("https://"):
        return StartReply(text=text, webapp_url=webapp_url)
    return StartReply(text=text + "\n\n" + t("bot.start.no_webapp", lang), webapp_url=None)

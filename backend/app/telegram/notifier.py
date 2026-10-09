"""Реализация Notifier через Telegram-бота."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from backend.app.core.notify import Buttons

log = logging.getLogger("verdikt.bot")


def _markup(buttons: Buttons | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in buttons])


class BotNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, chat_id: int, html_text: str, buttons: Buttons | None = None, thread_id: int | None = None) -> bool:
        try:
            await self.bot.send_message(chat_id, html_text, reply_markup=_markup(buttons), message_thread_id=thread_id or None)
            return True
        except TelegramAPIError as exc:
            # Чаще всего: человек не нажимал /start или заблокировал бота.
            log.info("Не удалось отправить сообщение в %s: %s", chat_id, exc)
            return False

    async def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> bool:
        try:
            await self.bot.send_document(chat_id, BufferedInputFile(data, filename), caption=caption or None)
            return True
        except TelegramAPIError as exc:
            log.info("Не удалось отправить файл в %s: %s", chat_id, exc)
            return False

    async def send_photo(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> bool:
        try:
            await self.bot.send_photo(chat_id, BufferedInputFile(data, filename), caption=caption or None)
            return True
        except TelegramAPIError as exc:
            log.info("Не удалось отправить картинку в %s: %s", chat_id, exc)
            return False

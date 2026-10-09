"""Вложения из Telegram: фото, PDF, DOCX, голосовые.

Файл скачиваем в память, тип определяем по содержимому (core/docparse.py), а не по имени и MIME:
  - фото → картинка для модели;
  - PDF и DOCX → извлекаем текст; PDF-скан без текста → картинки страниц;
  - всё остальное (архивы, программы, HTML) — отказ с понятным сообщением.
Оригинал нигде не сохраняется. Перед первым файлом бот спрашивает согласие на обработку (ensure_consent).
"""

from __future__ import annotations

import base64

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.core import docparse
from backend.app.i18n import t
from backend.app.llm.base import Attachment

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 МБ


class MediaError(Exception):
    """Код ошибки для текста bot.file.<код>: too_big | unsupported | unreadable | scan | encrypted | empty."""


def has_file(message: Message) -> bool:
    return bool(message.photo or message.document)


async def read_file(bot: Bot, message: Message) -> tuple[list[Attachment], str]:
    """(картинки для модели, извлечённый текст). Пусто — в сообщении нет файла."""
    if message.photo:
        photo = message.photo[-1]  # Telegram присылает несколько размеров; последний — самый большой
        if photo.file_size and photo.file_size > docparse.max_bytes():
            raise MediaError("too_big")
        raw = (await bot.download(photo.file_id)).read()
    elif message.document:
        if message.document.file_size and message.document.file_size > docparse.max_bytes():
            raise MediaError("too_big")
        raw = (await bot.download(message.document.file_id)).read()
    else:
        return [], ""
    try:
        kind = docparse.check_upload(raw, ("pdf", "docx", "jpeg", "png", "webp"))
        if kind in ("jpeg", "png", "webp"):
            return [Attachment("image", docparse.IMAGE_TYPES[kind], base64.b64encode(raw).decode())], ""
        text = docparse.extract(raw, kind)
        if len(text) >= 20:
            return [], text[:20000]
        if kind == "pdf":
            images = docparse.pdf_images(raw)
            if images:
                return [Attachment("image", mime, base64.b64encode(data).decode()) for mime, data in images], ""
            raise MediaError("scan")
        raise MediaError("empty")
    except docparse.DocError as exc:
        raise MediaError({"bad_type": "unsupported"}.get(exc.code, exc.code)) from exc


async def extract_attachments(bot: Bot, message: Message) -> list[Attachment]:
    """Совместимость со старыми вызовами: только картинки (текст из документа — read_file)."""
    attachments, _ = await read_file(bot, message)
    return attachments


def consent_given(user_id: int) -> bool:
    from backend.app.services import prefs

    return bool(prefs.get(user_id, "consent.docs"))


def consent_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("consent.btn.ok"), callback_data=f"cons:ok:{token}"),
        InlineKeyboardButton(text=t("consent.btn.no"), callback_data="cons:no"),
    ]])


def origin_of(message: Message) -> str:
    """Откуда переслано сообщение: @канал, название чата или пусто.

    forward_origin — новое поле Telegram (Bot API 7+): описывает источник
    пересылки. Для каналов есть username, для скрытых пользователей — только имя.
    """
    origin = message.forward_origin
    if origin is None:
        return ""
    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    if chat is not None:
        return f"@{chat.username}" if chat.username else (chat.title or "")
    return ""  # пересылка от обычного человека — его имя не храним

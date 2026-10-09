"""Временное хранилище сообщений, ожидающих выбора режима.

Человек пересылает боту сообщение без команды → бот спрашивает «Что
проверить?» с кнопками режимов. Пока человек выбирает, текст лежит здесь
(в памяти, до 30 минут). В callback_data кнопки — короткий токен, а не
весь текст: у Telegram лимит 64 байта.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from backend.app.llm.base import Attachment

TTL_SECONDS = 30 * 60


@dataclass
class Pending:
    user_id: int
    text: str
    origin: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    reply_to: int | None = None  # id исходного сообщения, чтобы ответить на него
    created: float = field(default_factory=time.time)


_store: dict[str, Pending] = {}


def put(item: Pending) -> str:
    # Заодно чистим устаревшие записи
    now = time.time()
    for key in [k for k, v in _store.items() if now - v.created > TTL_SECONDS]:
        del _store[key]
    token = secrets.token_urlsafe(6)
    _store[token] = item
    return token


def pop(token: str, user_id: int) -> Pending | None:
    item = _store.get(token)
    if item is None or item.user_id != user_id or time.time() - item.created > TTL_SECONDS:
        return None
    return _store.pop(token)


def get(token: str, user_id: int) -> Pending | None:
    """Посмотреть, не забирая (например, пока человек отвечает на вопрос о согласии)."""
    item = _store.get(token)
    if item is None or item.user_id != user_id or time.time() - item.created > TTL_SECONDS:
        return None
    return item

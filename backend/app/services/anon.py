"""Анонимность: хеш вместо id и порог «меньше пяти ответов — результат скрыт».

voter_hash = HMAC-SHA256(соль, "<область>:<telegram id>"). Область — например "poll:15":
у одного человека в разных опросах разные хеши, поэтому ответы нельзя связать между собой.
Хеш нужен только чтобы не проголосовать дважды и чтобы человек видел «свои» анонимные
вопросы. По хешу нельзя быстро узнать id без соли; соль — ANON_SALT в .env (или
сгенерированная при первом запуске и сохранённая в базе).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker


@lru_cache
def _salt() -> bytes:
    configured = get_settings().anon_salt.strip()
    if configured:
        return configured.encode()
    with get_sessionmaker()() as session:
        value = repo.get_meta(session, "anon_salt")
        if not value:
            value = secrets.token_hex(32)
            repo.set_meta(session, "anon_salt", value)
    return value.encode()


def reset() -> None:
    _salt.cache_clear()


def ensure_salt() -> None:
    _salt.cache_clear()
    _salt()


def voter_hash(scope: str, user_id: int) -> str:
    return hmac.new(_salt(), f"{scope}:{user_id}".encode(), hashlib.sha256).hexdigest()


def author_hash(user_id: int) -> str:
    """Один хеш автора на все анонимные публикации — чтобы показать человеку «мои вопросы»."""
    return voter_hash("author", user_id)


def min_answers() -> int:
    return max(1, get_settings().anon_min_answers)


def visible(count: int) -> bool:
    return count >= min_answers()

"""Шифрование файлов и чувствительных полей (Fernet: AES-128-CBC + HMAC-SHA256).

Ключ — DATA_ENCRYPTION_KEY в .env. Если ключа нет, при запуске сервера создаём новый
и дописываем его в .env (ensure_key). В репозиторий ключ не попадает: .env в .gitignore.

Что шифруем:
  - загруженные файлы (фото к объявлениям) — на диске лежит только шифротекст;
  - «обратный адрес» автора анонимной заявки (posts.notify_enc): по нему бот сообщает
    автору решение модератора. Модераторы и остальные пользователи его не видят.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.config import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)
ENV_FILE = PROJECT_ROOT / ".env"


def ensure_key() -> None:
    """Нет ключа — создаём и сохраняем в .env (только если .env существует: в тестах ключ задают переменной)."""
    if get_settings().data_encryption_key.strip() or os.environ.get("DATA_ENCRYPTION_KEY"):
        return
    key = Fernet.generate_key().decode()
    if ENV_FILE.is_file():
        with ENV_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\n# Ключ шифрования файлов и личных данных (создан автоматически). Не теряйте и не публикуйте.\nDATA_ENCRYPTION_KEY={key}\n")
        log.warning("Создан ключ шифрования DATA_ENCRYPTION_KEY и записан в .env. Сохраните копию .env в надёжном месте.")
    os.environ["DATA_ENCRYPTION_KEY"] = key
    get_settings().data_encryption_key = key
    _fernet.cache_clear()


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().data_encryption_key.strip() or os.environ.get("DATA_ENCRYPTION_KEY", "")
    if not key:
        ensure_key()
        key = get_settings().data_encryption_key
    return Fernet(key.encode())


def reset() -> None:
    _fernet.cache_clear()


def encrypt(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt(token: bytes) -> bytes | None:
    try:
        return _fernet().decrypt(token)
    except (InvalidToken, ValueError):
        return None


def encrypt_text(value: str) -> str:
    return encrypt(value.encode()).decode() if value else ""


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    raw = decrypt(value.encode())
    return raw.decode() if raw is not None else ""

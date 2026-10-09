"""Общие настройки тестов.

Главное: тесты НЕ должны ходить в интернет, тратить токены и трогать
настоящую базу. Поэтому до импорта приложения задаём переменные
окружения: демо-режим ИИ, пустой токен бота, без онлайн-проверки ссылок.
А перед каждым тестом создаём новую пустую базу во временной папке.

fixture (фикстура) — функция-«подготовка», которую pytest вызывает сам,
если тест упоминает её имя в параметрах. autouse=True — для всех тестов.
"""

from __future__ import annotations

import json
import os
import time

import pytest

# Переменные окружения имеют приоритет над .env — так ключи из .env не попадут в тесты.
os.environ.update({
    "MOCK_LLM": "true",
    "DEV_MODE": "true",
    "TELEGRAM_BOT_TOKEN": "",
    "ANTHROPIC_API_KEY": "",
    "LLM_API_KEY": "",
    "LLM_PROVIDER": "gemini",
    "BACKUP_LLM_PROVIDER": "",
    "BACKUP_LLM_API_KEY": "",
    "WEBAPP_URL": "",
    "TUNNEL": "",
    "LINK_CHECK_ONLINE": "false",
    "SCHEDULER_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
    # Постоянный тестовый ключ шифрования (в .env он свой; без ключа сервер создаёт его сам).
    "DATA_ENCRYPTION_KEY": "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMTIzNDU=",
    "ADMIN_IDS": "",
    "MODERATOR_IDS": "",
    "MOD_CHAT_ID": "",
    "SEARCH_PROVIDER": "off",
    "OWNER_IDS": "",
})

TEST_TOKEN = "123456789:" + "A" * 35


def reset_caches() -> None:
    from backend.app.core.config import get_settings
    from backend.app.core.engine import reset_verdict_engine
    from backend.app.core.notify import set_notifier
    from backend.app.db.base import reset_engine
    from backend.app.llm.factory import get_llm_client

    from backend.app.core.features import get_flags
    from backend.app.security import crypto
    from backend.app.services import anon

    get_settings.cache_clear()
    crypto.reset()
    from backend.app.core.guard import api_throttle
    from backend.app.rag import community as community_kb
    from backend.app.search import web

    api_throttle.reset()
    community_kb.invalidate()
    web.reset_cache()
    get_flags.cache_clear()
    anon.reset()
    get_llm_client.cache_clear()
    reset_engine()
    reset_verdict_engine()
    set_notifier(None)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Новая пустая база для каждого теста."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    reset_caches()
    from backend.app.db.init_db import init_db

    init_db()
    yield
    reset_caches()


@pytest.fixture
def client():
    """HTTP-клиент для API. Без `with` — значит, без запуска бота (lifespan не выполняется)."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    return TestClient(app)


@pytest.fixture
def with_token(monkeypatch):
    """Включает «настоящую» проверку подписи Telegram с тестовым токеном."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DEV_MODE", "false")
    reset_caches()
    return TEST_TOKEN


def tma_header(user_id: int, first_name: str = "Тест", username: str = "", token: str = TEST_TOKEN) -> dict[str, str]:
    """Заголовок Authorization с правильно подписанной initData — как от Telegram."""
    from backend.app.security.initdata import build_init_data

    user = {"id": user_id, "first_name": first_name, "username": username, "language_code": "ru"}
    init_data = build_init_data({"auth_date": str(int(time.time())), "query_id": "AAE", "user": json.dumps(user, ensure_ascii=False)}, token)
    return {"Authorization": f"tma {init_data}"}


class FakeNotifier:
    """«Фальшивый» отправщик: запоминает, кому что отправили."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.buttons: list = []
        self.files: list[tuple[int, str]] = []

    async def send(self, chat_id, html_text, buttons=None, **kwargs):
        self.sent.append((chat_id, html_text))
        self.buttons.append(buttons)
        return True

    async def send_document(self, chat_id, filename, data, caption=""):
        self.files.append((chat_id, filename))
        return True

    async def send_photo(self, chat_id, filename, data, caption=""):
        self.files.append((chat_id, filename))
        return True


@pytest.fixture
def notifier():
    from backend.app.core.notify import set_notifier

    fake = FakeNotifier()
    set_notifier(fake)
    return fake

"""Распознавание голосовых сообщений (необязательно).

Языковые модели в проекте не принимают аудио, поэтому голос сначала нужно превратить
в текст. Если подключён Groq (LLM_PROVIDER или BACKUP_LLM_PROVIDER = groq с ключом),
используется его бесплатный Whisper (whisper-large-v3-turbo) — ничего ставить не нужно.
Иначе — локальная модель Whisper (библиотека faster-whisper). Она тяжёлая
(≈ 500 МБ с моделью), поэтому НЕ ставится по умолчанию. Чтобы включить:

    .venv\\Scripts\\pip install faster-whisper

и перезапустите сервер. Без неё бот честно ответит, что голосовые не
распознаёт, и попросит прислать текст.
"""

from __future__ import annotations

import asyncio
import io
import logging
from functools import lru_cache

import httpx

from backend.app.core.config import get_settings

log = logging.getLogger(__name__)

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3-turbo"


def _groq_key() -> str:
    settings = get_settings()
    for provider, key in ((settings.llm_provider, settings.llm_api_key), (settings.backup_llm_provider, settings.backup_llm_api_key)):
        if provider.strip().lower() == "groq" and key.strip():
            return key.strip()
    return ""


def is_available() -> bool:
    if _groq_key():
        return True
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache
def _model():
    from faster_whisper import WhisperModel

    # "small" — компромисс скорости и качества для русского; int8 — экономит память.
    return WhisperModel("small", device="cpu", compute_type="int8")


def _transcribe_sync(data: bytes) -> str:
    segments, _ = _model().transcribe(io.BytesIO(data), language="ru")
    return " ".join(s.text.strip() for s in segments).strip()


async def _transcribe_groq(data: bytes, filename: str, key: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            GROQ_STT_URL, headers={"Authorization": f"Bearer {key}"},
            data={"model": GROQ_STT_MODEL, "language": "ru", "response_format": "json"},
            files={"file": (filename, data)},
        )
    response.raise_for_status()
    return str(response.json().get("text", "")).strip()


async def transcribe(data: bytes, filename: str = "voice.ogg") -> str:
    key = _groq_key()
    if key:
        try:
            return await _transcribe_groq(data, filename, key)
        except Exception:
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                raise
            log.warning("Groq Whisper не ответил — распознаю локально", exc_info=True)
    # Распознавание долгое и «тяжёлое» — выполняем в отдельном потоке, чтобы бот не завис.
    return await asyncio.to_thread(_transcribe_sync, data)

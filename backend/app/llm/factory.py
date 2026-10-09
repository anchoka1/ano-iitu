"""Выбор клиента модели по настройкам (.env).

LLM_PROVIDER:
  gemini, groq, openrouter — бесплатные облачные (ключ LLM_API_KEY);
  openai                   — любой OpenAI-совместимый сервис (LLM_BASE_URL + LLM_API_KEY);
  ollama                   — локальная модель на вашем компьютере;
  anthropic                — Claude (платно, ключ ANTHROPIC_API_KEY).
Без ключа — демо-клиент (правила, без ИИ).
"""

from __future__ import annotations

from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.llm.base import LLMClient
from backend.app.llm.mock import MockLLMClient


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()
    if settings.use_mock_llm:
        return MockLLMClient()
    if provider == "ollama":
        from backend.app.llm.ollama_client import OllamaLLMClient

        return OllamaLLMClient(settings.ollama_url, settings.ollama_model)
    if provider == "anthropic":
        from backend.app.llm.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(settings.anthropic_api_key, settings.anthropic_model, settings.llm_effort)

    backup_provider = settings.backup_llm_provider.strip().lower()
    backup = None
    if backup_provider and settings.backup_llm_api_key.strip():
        backup = _openai_compat(backup_provider, settings.backup_llm_api_key, settings.backup_llm_model, "")
    if not settings.llm_api_key.strip() and backup:
        return backup  # ключа основного сервиса пока нет — работаем на запасном
    primary = _openai_compat(provider, settings.llm_api_key, settings.llm_model, settings.llm_base_url)
    if backup:
        from backend.app.llm.backup import BackupLLMClient

        return BackupLLMClient(primary, backup)
    return primary


def _openai_compat(provider: str, api_key: str, model: str, base_url: str) -> LLMClient:
    from backend.app.llm.openai_compat import PRESETS, OpenAICompatClient

    preset = PRESETS.get(provider, {})
    base_url = base_url.strip() or preset.get("base_url", "")
    if not base_url:
        raise ValueError(f"Неизвестный LLM_PROVIDER «{provider}». Для своего сервиса укажите LLM_BASE_URL.")
    # Запасную модель используем, только если основная — по умолчанию (свою модель пользователь выбрал сам).
    fallback = preset.get("fallback", "") if not model.strip() else ""
    return OpenAICompatClient(provider, base_url, api_key, model.strip() or preset.get("model", ""), fallback_model=fallback,
                              vision_model=preset.get("vision", ""))

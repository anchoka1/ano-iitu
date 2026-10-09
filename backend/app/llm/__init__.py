"""Клиент языковой модели (LLM), независимый от провайдера.

Реализации:
  MockLLMClient      — заготовленные ответы (демо, ключ не нужен);
  OpenAICompatClient — бесплатные облачные: Gemini, Groq, OpenRouter (llm/openai_compat.py);
  AnthropicLLMClient — Anthropic API, платно (модель из ANTHROPIC_MODEL);
  OllamaLLMClient    — локальная модель (на русском заметно слабее).

Все возвращают ответ строго по Pydantic-схеме; при ошибке формата —
повторный запрос с объяснением ошибки.
"""

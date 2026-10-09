"""Основная модель + запасная от другого сервиса.

Зачем: у бесплатных тарифов есть лимиты, и сервис иногда недоступен.
Если основной (например, Groq) ответил ошибкой — лимит, нет связи,
перегрузка — тот же запрос уходит запасному (например, Gemini).
Фото основная модель может не понимать (у Groq — только если задана vision-модель),
тогда запросы с картинками сразу отправляем запасному.
"""

from __future__ import annotations

import logging

from backend.app.llm.base import LLMClient, LLMError, LLMRequest, LLMResult

log = logging.getLogger(__name__)


class BackupLLMClient(LLMClient):
    def __init__(self, primary: LLMClient, backup: LLMClient) -> None:
        self.primary = primary
        self.backup = backup
        self.name = primary.name
        self.model = primary.model

    async def check_model(self) -> str:
        parts = []
        for role, llm in (("основная", self.primary), ("запасная", self.backup)):
            status = await llm.check_model() if hasattr(llm, "check_model") else "готова"
            parts.append(f"{role} {llm.name}: {status}")
        return " | ".join(parts)

    async def generate(self, request: LLMRequest) -> LLMResult:
        if request.attachments and not getattr(self.primary, "supports_images", True):
            return await self.backup.generate(request)
        try:
            return await self.primary.generate(request)
        except LLMError as exc:
            log.info("%s: %s — отправляю запрос запасному сервису %s", self.primary.name, exc, self.backup.name)
            return await self.backup.generate(request)

"""Клиент для локальной модели через Ollama.

Ollama (ollama.com) запускает открытые модели на вашем компьютере, без
интернета и без оплаты. Честная оговорка: небольшие локальные модели
заметно хуже понимают русский язык и чаще ошибаются в формате ответа,
поэтому для реальной проверки лучше облачная модель (Gemini, Groq).

Как запустить: установите Ollama, выполните `ollama pull qwen2.5:7b`,
в .env поставьте LLM_PROVIDER=ollama и MOCK_LLM=false.
PDF Ollama не читает; фото — только мультимодальные модели (например, llava).
"""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from backend.app.llm.base import LLMClient, LLMError, LLMRequest, LLMResult

MAX_ATTEMPTS = 3


class OllamaLLMClient(LLMClient):
    name = "ollama"

    def __init__(self, url: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.model = model

    async def generate(self, request: LLMRequest) -> LLMResult:
        if any(a.kind == "pdf" for a in request.attachments):
            raise LLMError("Локальная модель (Ollama) не умеет читать PDF. Пришлите фото или текст.")
        images = [a.data_b64 for a in request.attachments if a.kind == "image"]
        note = ""
        total_in = total_out = 0
        for _ in range(MAX_ATTEMPTS):
            payload = {
                "model": self.model,
                "stream": False,
                # format = JSON-схема: Ollama ограничит ответ этой схемой.
                "format": request.schema.model_json_schema(),
                "options": {"temperature": 0.2},
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user + note, **({"images": images} if images else {})},
                ],
            }
            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    response = await client.post(f"{self.url}/api/chat", json=payload)
            except httpx.HTTPError as exc:
                raise LLMError(f"Ollama не отвечает по адресу {self.url}. Запущена ли программа Ollama?") from exc
            if response.status_code == 404:
                raise LLMError(f"В Ollama нет модели «{self.model}». Выполните: ollama pull {self.model}")
            if response.status_code != 200:
                raise LLMError(f"Ollama вернула ошибку {response.status_code}.")
            body = response.json()
            total_in += body.get("prompt_eval_count", 0)
            total_out += body.get("eval_count", 0)
            try:
                data = request.schema.model_validate_json(body["message"]["content"])
            except (ValidationError, KeyError) as exc:
                note = f"\n\nПредыдущий ответ не прошёл проверку: {str(exc)[:300]}. Ответь строго JSON по схеме."
                continue
            return LLMResult(data, self.name, self.model, total_in, total_out)
        raise LLMError("Локальная модель несколько раз ответила в неверном формате. Попробуйте ещё раз или используйте облачную модель (LLM_PROVIDER=gemini).")

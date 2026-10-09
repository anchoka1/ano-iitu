"""Клиент Anthropic API (Claude).

Как устроен запрос:
  - output_config.format = JSON-схема нашей Pydantic-модели — «структурированный
    вывод»: API заставляет модель ответить JSON строго по схеме.
    transform_schema (из SDK) приводит схему Pydantic к виду, который принимает API.
  - Ответ сначала проверяем на причину остановки (отказ, обрезка), и только
    потом разбираем JSON через schema.model_validate_json. Если делать
    наоборот, отказ модели (пустой текст) выглядел бы как «неверный JSON»
    и вызывал бы лишние платные повторы.
  - output_config["effort"] — насколько глубоко модель «думает»
    (low / medium / high). Больше — точнее, но дольше и дороже.
  - fallbacks="default" — если модель откажется отвечать по соображениям
    безопасности (бывает на текстах про мошенничество), сервер Anthropic
    сам повторит запрос на запасной модели. Требует бета-заголовка.
  - Фото передаются блоком "image", PDF — блоком "document" (base64).

Ошибки переводим в понятные русские сообщения (LLMError).
"""

from __future__ import annotations

import logging

import anthropic
from anthropic import transform_schema
from pydantic import ValidationError

from backend.app.llm.base import LLMClient, LLMError, LLMRequest, LLMResult

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ATTEMPTS = 3  # первая попытка + 2 повтора при неверном формате ответа


class AnthropicLLMClient(LLMClient):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "medium") -> None:
        # max_retries=2: SDK сам повторяет запрос при 429/5xx и обрывах связи.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=120.0)
        self.model = model
        self.effort = effort

    def _content(self, request: LLMRequest, extra_note: str = "") -> list[dict]:
        blocks: list[dict] = []
        # Вложения ставим ПЕРЕД текстом — так рекомендует документация.
        for att in request.attachments:
            if att.kind == "image":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": att.media_type, "data": att.data_b64}})
            elif att.kind == "pdf":
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": att.data_b64}})
        blocks.append({"type": "text", "text": request.user + extra_note})
        return blocks

    async def generate(self, request: LLMRequest) -> LLMResult:
        extra_note = ""
        total_in = total_out = 0
        output_format = {"type": "json_schema", "schema": transform_schema(request.schema.model_json_schema())}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.beta.messages.create(
                    model=self.model,
                    max_tokens=request.max_tokens,
                    system=request.system,
                    messages=[{"role": "user", "content": self._content(request, extra_note)}],
                    output_config={"effort": self.effort, "format": output_format},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                )
            except anthropic.AuthenticationError as exc:
                raise LLMError("Ключ ANTHROPIC_API_KEY не подошёл. Проверьте его в .env (без пробелов и кавычек).") from exc
            except anthropic.PermissionDeniedError as exc:
                raise LLMError("У ключа Anthropic нет доступа к этой модели. Проверьте ANTHROPIC_MODEL в .env.") from exc
            except anthropic.NotFoundError as exc:
                raise LLMError(f"Модель «{self.model}» не найдена. Проверьте ANTHROPIC_MODEL в .env.") from exc
            except anthropic.RateLimitError as exc:
                raise LLMError("Слишком много запросов к ИИ. Подождите минуту и попробуйте снова.") from exc
            except anthropic.BadRequestError as exc:
                log.error("Anthropic отклонил запрос: %s", exc)
                raise LLMError("ИИ не принял запрос (возможно, файл слишком большой или повреждён).") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError("Нет связи с Anthropic API. Проверьте интернет или VPN.") from exc
            except anthropic.APIStatusError as exc:
                raise LLMError("Сервис ИИ временно недоступен. Попробуйте через пару минут.") from exc

            usage = response.usage
            total_in += usage.input_tokens or 0
            total_out += usage.output_tokens or 0

            # 1) Сначала — почему модель остановилась.
            if response.stop_reason == "refusal":
                raise LLMError("ИИ отказался разбирать этот текст. Попробуйте переформулировать или убрать лишнее.")
            if response.stop_reason == "max_tokens":
                log.warning("Ответ обрезан по max_tokens (попытка %d)", attempt)
                extra_note = "\n\nОтвечай короче: предыдущий ответ не поместился."
                continue
            # 2) Потом — разбор JSON по нашей схеме. Ответ может состоять из нескольких блоков
            #    (например, блок «размышлений»), поэтому берём только текстовые.
            text = "".join(block.text for block in response.content if block.type == "text")
            try:
                data = request.schema.model_validate_json(text)
            except ValidationError as exc:
                log.warning("Ответ модели не прошёл проверку (попытка %d): %s", attempt, str(exc)[:300])
                extra_note = "\n\nПредыдущий ответ не прошёл проверку схемы. Ответь строго JSON по схеме."
                continue
            return LLMResult(data, self.name, response.model, total_in, total_out)

        raise LLMError("ИИ несколько раз ответил в неверном формате. Попробуйте ещё раз.")

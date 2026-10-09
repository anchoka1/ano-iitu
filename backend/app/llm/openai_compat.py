"""Клиент для бесплатных облачных моделей с «OpenAI-совместимым» API.

Многие сервисы понимают один и тот же формат запроса (его придумала OpenAI,
и он стал стандартом): POST {base_url}/chat/completions с полями model,
messages, response_format. Поэтому один клиент подходит сразу для:
  - Google Gemini   — https://generativelanguage.googleapis.com/v1beta/openai
  - Groq            — https://api.groq.com/openai/v1
  - OpenRouter      — https://openrouter.ai/api/v1
  - и любого другого совместимого сервиса (LLM_BASE_URL).

Как получаем JSON по схеме:
  1. просим response_format = json_schema (строгая схема) — так умеют Gemini и часть моделей;
  2. если сервис/модель так не умеет (ошибка 400) — переходим на json_object
     («просто верни JSON»), а схему описываем в системном промпте;
  3. в любом случае ответ проверяем Pydantic-схемой и при ошибке просим исправить.

Честно о бесплатных тарифах: у них есть лимиты (запросов в минуту/день),
а бесплатные тексты провайдер может использовать для улучшения своих моделей.
Не отправляйте туда то, что нельзя показывать посторонним.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
from pydantic import ValidationError

from backend.app.llm.base import LLMClient, LLMError, LLMRequest, LLMResult

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

# Готовые настройки популярных бесплатных сервисов: адрес API и модель по умолчанию.
PRESETS: dict[str, dict[str, str]] = {
    # gemini-flash-latest — «псевдоним»: Google сам направляет его на актуальную Flash-модель.
    # Конкретные версии (gemini-2.5-flash и т. п.) со временем закрывают для новых пользователей.
    # fallback — запасная модель, если основная перегружена (на бесплатном тарифе бывает часто).
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-flash-latest",
               "fallback": "gemini-flash-lite-latest",
               "key_hint": "ключ из https://aistudio.google.com/apikey"},
    # Llama 3.x Groq убрал. gpt-oss строго держит JSON-схему; фото читает только qwen (vision).
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
             "fallback": "openai/gpt-oss-20b", "vision": "qwen/qwen3.8-27b",
             "key_hint": "ключ из https://console.groq.com/keys"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free",
                   "key_hint": "ключ из https://openrouter.ai/keys"},
}


class OpenAICompatClient(LLMClient):
    def __init__(self, name: str, base_url: str, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None,
                 fallback_model: str = "", vision_model: str = "") -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else ""
        self.vision_model = vision_model  # модель для запросов с фото, если основная их не понимает
        self._transport = transport  # подменяется в тестах, чтобы не ходить в интернет
        self._schema_mode_supported = True  # станет False, если сервис не умеет json_schema
        self.retry_delays: tuple[float, ...] = (2.0, 5.0)  # паузы перед повтором при перегрузке (503)

    def _messages(self, request: LLMRequest, note: str) -> list[dict]:
        schema = json.dumps(request.schema.model_json_schema(), ensure_ascii=False)
        system = (
            f"{request.system}\n\nФормат ответа: ОДИН JSON-объект строго по этой JSON-схеме, "
            f"без пояснений и без ```:\n{schema}"
        )
        if any(a.kind == "pdf" for a in request.attachments):
            raise LLMError("Эта модель не читает PDF. Пришлите фото документа или перепишите текст.")
        images = [a for a in request.attachments if a.kind == "image"]
        if images:
            content: list[dict] | str = [
                *({"type": "image_url", "image_url": {"url": f"data:{a.media_type};base64,{a.data_b64}"}} for a in images),
                {"type": "text", "text": request.user + note},
            ]
        else:
            content = request.user + note
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    def _response_format(self, request: LLMRequest) -> dict:
        if self._schema_mode_supported:
            return {"type": "json_schema", "json_schema": {"name": request.schema.__name__, "schema": request.schema.model_json_schema()}}
        return {"type": "json_object"}

    async def _post(self, payload: dict, retry_delays: tuple[float, ...] | None = None) -> httpx.Response:
        """Отправка запроса. При временной перегрузке сервиса (503) — повтор через 2 и 5 секунд."""
        if retry_delays is None:
            retry_delays = self.retry_delays
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.name == "openrouter":
            headers["X-Title"] = "Verdikt"  # OpenRouter просит название приложения (необязательно)
        for delay in (*retry_delays, None):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
                    response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise LLMError(f"Нет связи с сервисом ИИ ({self.name}). Проверьте интернет или VPN.") from exc
            if response.status_code != 503 or delay is None:
                return response
            log.info("%s перегружен (503), повтор через %.0f с", self.name, delay)
            await asyncio.sleep(delay)
        return response  # pragma: no cover — цикл всегда возвращает раньше

    @staticmethod
    def _provider_message(response: httpx.Response) -> str:
        """Текст ошибки от самого сервиса (часто там подсказка, что делать)."""
        try:
            body = response.json()
            body = body[0] if isinstance(body, list) else body
            return str(body.get("error", {}).get("message", ""))[:300]
        except Exception:  # noqa: BLE001
            return ""

    def _quota_hint(self, response: httpx.Response) -> str:
        """Достаём из ответа сервиса, какой лимит и когда он сбросится (Google пишет это в тексте ошибки)."""
        message = self._provider_message(response) or response.text[:1000]
        limit = re.search(r"limit:\s*(\d+)", message)
        retry = re.search(r"retry in\s*(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", message)
        parts = []
        if limit:
            per_day = "per_day" in message.lower() or "requests_per_day" in message.lower() or "free_tier_requests" in message
            parts.append(f" Лимит бесплатного тарифа: {limit.group(1)} запросов" + (" в день." if per_day else "."))
        if retry and any(retry.groups()):
            hours, minutes = int(retry.group(1) or 0), int(retry.group(2) or 0)
            if hours or minutes:
                parts.append(f" Сброс примерно через {hours} ч {minutes} мин." if hours else f" Сброс примерно через {minutes} мин.")
            else:
                parts.append(" Попробуйте через минуту.")
        if not parts:
            parts.append(" Подождите минуту (или до завтра) и попробуйте снова.")
        parts.append(" Можно подключить второй бесплатный сервис (LLM_PROVIDER=groq).")
        return "".join(parts)

    def _raise_for_status(self, response: httpx.Response) -> None:
        code = response.status_code
        if code in (401, 403):
            raise LLMError("Ключ LLM_API_KEY не подошёл или у него нет доступа. Проверьте ключ в .env (без пробелов и кавычек).")
        if code == 404:
            detail = self._provider_message(response)
            raise LLMError(f"Модель «{self.model}» недоступна у {self.name}. Проверьте LLM_MODEL в .env." + (f" Ответ сервиса: {detail}" if detail else ""))
        if code == 429:
            raise LLMError("Бесплатный лимит запросов к ИИ исчерпан." + self._quota_hint(response))
        if code >= 500:
            raise LLMError("Сервис ИИ временно недоступен. Попробуйте через пару минут.")
        log.error("%s вернул %s: %s", self.name, code, response.text[:500])
        raise LLMError(f"Сервис ИИ отклонил запрос (код {code}).")

    async def check_model(self) -> str:
        """Проверка при запуске: ключ подходит и модель существует. Возвращает текст для консоли."""
        try:
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
                response = await client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError:
            return f"Не удалось связаться с {self.name} — проверьте интернет/VPN."
        if response.status_code in (401, 403):
            return "Ключ LLM_API_KEY не подошёл — проверьте его в .env."
        if response.status_code != 200:
            return f"{self.name}: список моделей недоступен (код {response.status_code}), проверка пропущена."
        ids = [m.get("id", "").removeprefix("models/") for m in response.json().get("data", [])]
        hint = ", ".join(sorted(i for i in ids if any(w in i for w in ("flash", "llama", "free", "pro")))[:12])
        if ids and self.model not in ids:
            return f"Модели «{self.model}» нет у {self.name}! Впишите в .env LLM_MODEL=… из списка: {hint}"
        if self.model.endswith("-latest"):
            # Псевдонимы «-latest» сервис всегда направляет на доступную модель — пробный запрос
            # не нужен (на бесплатном тарифе каждый запрос на счету).
            return f"Модель {self.model} доступна" + (f" (запасная: {self.fallback_model})." if self.fallback_model else ".")
        # Конкретная версия может быть в списке, но закрыта для новых пользователей — проверяем крошечным запросом.
        ping = await self._post({"model": self.model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 64,
                                 **self._extra(self.model)}, retry_delays=())
        if ping.status_code == 200:
            return f"Модель {self.model} доступна и отвечает."
        if ping.status_code == 429:
            return f"Модель {self.model} доступна, но бесплатный лимит сейчас исчерпан."
        if ping.status_code == 503:
            return f"Модель {self.model} доступна, но сервис сейчас перегружен — запросы будут повторяться."
        detail = self._provider_message(ping)
        return f"Модель {self.model} не отвечает (код {ping.status_code}): {detail} Можно указать другую в LLM_MODEL: {hint}"

    @property
    def supports_images(self) -> bool:
        return self.name != "groq" or bool(self.vision_model)

    @staticmethod
    def _extra(model: str) -> dict:
        # gpt-oss «думает» перед ответом и тратит на это max_tokens — просим думать коротко.
        return {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}

    async def generate(self, request: LLMRequest) -> LLMResult:
        has_images = any(a.kind == "image" for a in request.attachments)
        model = self.vision_model if has_images and self.vision_model else self.model
        fallback = "" if model != self.model else self.fallback_model
        note = ""
        total_in = total_out = 0
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            payload = {
                "model": model,
                "messages": self._messages(request, note),
                "response_format": self._response_format(request),
                "temperature": 0.2,  # меньше «фантазии» — стабильнее формат и факты
                "max_tokens": request.max_tokens,
                **self._extra(model),
            }
            response = await self._post(payload)
            if response.status_code in (429, 503) and fallback:
                # 503 — основная модель перегружена; 429 — исчерпан её бесплатный лимит.
                # У запасной модели (lite) свой отдельный лимит, обычно заметно больше.
                log.info("%s/%s: код %s, пробую запасную модель %s", self.name, model, response.status_code, fallback)
                response = await self._post({**{k: v for k, v in payload.items() if k != "reasoning_effort"}, "model": fallback,
                                             **self._extra(fallback)})
            if response.status_code == 400 and self._schema_mode_supported:
                # Сервис не понял json_schema — пробуем более простой режим, попытку не считаем.
                log.info("%s/%s не поддерживает json_schema, переключаюсь на json_object", self.name, model)
                self._schema_mode_supported = False
                attempt -= 1
                continue
            if response.status_code != 200:
                self._raise_for_status(response)

            body = response.json()
            usage = body.get("usage") or {}
            total_in += usage.get("prompt_tokens", 0) or 0
            total_out += usage.get("completion_tokens", 0) or 0
            choice = (body.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                note = "\n\nОтвечай короче: предыдущий ответ не поместился."
                continue
            text = (choice.get("message") or {}).get("content") or ""
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = request.schema.model_validate_json(text)
            except ValidationError as exc:
                log.warning("Ответ %s не прошёл проверку (попытка %d): %s", self.name, attempt, str(exc)[:300])
                note = f"\n\nПредыдущий ответ не прошёл проверку схемы: {str(exc)[:300]}. Ответь строго JSON по схеме."
                continue
            return LLMResult(data, self.name, body.get("model") or model, total_in, total_out)
        raise LLMError("ИИ несколько раз ответил в неверном формате. Попробуйте ещё раз.")

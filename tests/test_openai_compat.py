"""Бесплатные облачные модели (Gemini, Groq, OpenRouter) — без интернета.

Подменяем HTTP-транспорт: запрос попадает в нашу функцию, которая проверяет,
что отправлено, и возвращает ответ в формате OpenAI-совместимого API.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.cards.schema import VerdictCard
from backend.app.llm.base import Attachment, LLMError, LLMRequest
from backend.app.llm.openai_compat import PRESETS, OpenAICompatClient

CARD = {"status": "red", "title": "Похоже на мошенничество", "kind": "scam", "reasons": [{"text": "Просят код", "quote": "код"}],
        "confidence": 90, "do": ["Позвоните в банк"], "dont": ["Не сообщайте код"]}


def completion(text: str, finish: str = "stop") -> dict:
    return {"id": "x", "model": "gemini-2.5-flash", "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


def client(handler, provider: str = "gemini") -> OpenAICompatClient:
    preset = PRESETS[provider]
    llm = OpenAICompatClient(provider, preset["base_url"], "test-key", preset["model"], transport=httpx.MockTransport(handler))
    llm.retry_delays = (0, 0)  # в тестах не ждём
    return llm


def request(**kw) -> LLMRequest:
    return LLMRequest(task="check:razvod", system="Правила", user="<user_content>код</user_content>", schema=VerdictCard, **kw)


def test_gemini_request_and_parse():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=completion(json.dumps(CARD, ensure_ascii=False)))

    result = asyncio.run(client(handler).generate(request(attachments=[Attachment("image", "image/jpeg", "aGk=")])))
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "gemini-flash-latest"
    assert body["response_format"]["type"] == "json_schema"
    assert "JSON-схеме" in body["messages"][0]["content"]  # схема продублирована в системном промпте
    assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result.data.status == "red" and result.input_tokens == 100 and result.provider == "gemini"


def test_falls_back_to_json_object_when_schema_unsupported():
    formats = []

    def handler(req):
        fmt = json.loads(req.content)["response_format"]["type"]
        formats.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": {"message": "json_schema not supported"}})
        return httpx.Response(200, json=completion("```json\n" + json.dumps(CARD, ensure_ascii=False) + "\n```"))

    result = asyncio.run(client(handler, "groq").generate(request()))
    assert formats == ["json_schema", "json_object"]
    assert result.data.title == "Похоже на мошенничество"  # ```json вокруг ответа тоже убирается


def test_invalid_json_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=completion("не JSON" if calls["n"] == 1 else json.dumps(CARD, ensure_ascii=False)))

    assert asyncio.run(client(handler).generate(request())).data.status == "red"
    assert calls["n"] == 2


@pytest.mark.parametrize("code, text", [(401, "LLM_API_KEY"), (404, "LLM_MODEL"), (429, "лимит"), (503, "недоступен")])
def test_errors_in_russian(code, text):
    def handler(req):
        return httpx.Response(code, json={"error": {"message": "x"}})

    with pytest.raises(LLMError, match=text):
        asyncio.run(client(handler).generate(request()))


def test_pdf_not_supported():
    with pytest.raises(LLMError, match="PDF"):
        asyncio.run(client(lambda r: httpx.Response(200)).generate(request(attachments=[Attachment("pdf", "application/pdf", "aGk=")])))


def test_check_model_hints_available_models():
    def missing(req):
        return httpx.Response(200, json={"data": [{"id": "models/gemini-3-flash"}, {"id": "models/gemini-3-pro"}]})

    message = asyncio.run(client(missing).check_model())
    assert "нет у gemini" in message and "gemini-3-flash" in message


def test_check_model_listed_but_closed():
    """Как было на самом деле: модель есть в списке, но закрыта для новых пользователей."""
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "models/gemini-2.5-flash"}]})
        return httpx.Response(404, json=[{"error": {"code": 404, "message": "This model is no longer available to new users."}}])

    llm = client(handler)
    llm.model = "gemini-2.5-flash"  # конкретная версия — проверяется пробным запросом
    message = asyncio.run(llm.check_model())
    assert "не отвечает" in message and "no longer available" in message


def test_check_model_alias_does_not_spend_request():
    methods = []

    def handler(req):
        methods.append(req.method)
        return httpx.Response(200, json={"data": [{"id": "models/gemini-flash-latest"}]})

    assert "доступна" in asyncio.run(client(handler).check_model())
    assert methods == ["GET"]  # без пробного запроса к модели


def test_quota_message_and_fallback_on_429():
    google_429 = [{"error": {"code": 429, "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, "
                                                    "model: gemini-3.8-flash. Please retry in 3h50m56.12s."}}]
    with pytest.raises(LLMError) as info:
        asyncio.run(client(lambda r: httpx.Response(429, json=google_429)).generate(request()))
    assert "20 запросов в день" in str(info.value) and "3 ч 50 мин" in str(info.value)

    def handler(req):
        model = json.loads(req.content)["model"]
        return httpx.Response(429, json=google_429) if model == "gemini-flash-latest" else httpx.Response(200, json=completion(json.dumps(CARD, ensure_ascii=False)))

    llm = client(handler)
    llm.fallback_model = "gemini-flash-lite-latest"
    assert asyncio.run(llm.generate(request())).data.status == "red"


def test_overload_is_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json=completion(json.dumps(CARD, ensure_ascii=False)))

    assert asyncio.run(client(handler).generate(request())).data.status == "red"
    assert calls["n"] == 3


def test_factory_picks_free_provider(monkeypatch):
    from backend.app.llm.factory import get_llm_client
    from tests.conftest import reset_caches

    monkeypatch.setenv("MOCK_LLM", "auto")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "k")
    reset_caches()
    llm = get_llm_client()
    assert llm.name == "groq" and llm.model == "openai/gpt-oss-120b"

    monkeypatch.setenv("LLM_API_KEY", "")
    reset_caches()
    assert get_llm_client().name == "mock"  # без ключа — демо


def test_fallback_model_when_overloaded():
    models = []

    def handler(req):
        model = json.loads(req.content)["model"]
        models.append(model)
        if model == "gemini-flash-latest":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json=completion(json.dumps(CARD, ensure_ascii=False)))

    llm = client(handler)
    llm.fallback_model = "gemini-flash-lite-latest"
    assert asyncio.run(llm.generate(request())).data.status == "red"
    assert models[-1] == "gemini-flash-lite-latest" and models.count("gemini-flash-latest") == 3  # 1 + 2 повтора


def test_backup_takes_over_on_error_and_photos():
    from backend.app.llm.backup import BackupLLMClient
    from backend.app.llm.base import Attachment

    seen = []

    def groq(req):
        seen.append("groq")
        return httpx.Response(429, json={"error": {"message": "rate limit"}})

    def gemini(req):
        seen.append("gemini")
        return httpx.Response(200, json=completion(json.dumps(CARD, ensure_ascii=False)))

    llm = BackupLLMClient(client(groq, "groq"), client(gemini, "gemini"))
    assert asyncio.run(llm.generate(request())).provider == "gemini"
    assert seen == ["groq", "gemini"]

    seen.clear()
    photo = request()
    photo.attachments = [Attachment("image", "image/jpeg", "AAAA")]
    assert asyncio.run(llm.generate(photo)).provider == "gemini"
    assert seen == ["gemini"]  # фото сразу запасному


def test_factory_builds_backup(monkeypatch):
    from backend.app.llm.backup import BackupLLMClient
    from backend.app.llm.factory import get_llm_client
    from tests.conftest import reset_caches

    monkeypatch.setenv("MOCK_LLM", "auto")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("BACKUP_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("BACKUP_LLM_API_KEY", "g")
    reset_caches()
    assert get_llm_client().name == "gemini"  # ключа Groq ещё нет — работает запасной

    monkeypatch.setenv("LLM_API_KEY", "k")
    reset_caches()
    llm = get_llm_client()
    assert isinstance(llm, BackupLLMClient) and llm.name == "groq" and llm.backup.name == "gemini"

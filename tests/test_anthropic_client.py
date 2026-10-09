"""Клиент Anthropic без интернета.

Подменяем HTTP-транспорт SDK: вместо отправки запроса в Anthropic он
попадает в нашу функцию, которая проверяет, ЧТО отправлено (модель, схема
JSON, effort, fallbacks, картинка), и возвращает правдоподобный ответ.
Так проверяется настоящий код AnthropicLLMClient, но без ключа и денег.
"""

from __future__ import annotations

import asyncio
import json

import anthropic
import httpx2
import pytest

from backend.app.cards.schema import VerdictCard
from backend.app.llm.anthropic_client import AnthropicLLMClient
from backend.app.llm.base import Attachment, LLMError, LLMRequest

CARD = {"status": "red", "title": "Похоже на мошенничество", "kind": "scam", "reasons": [{"text": "Просят код", "quote": "код"}],
        "sources": [], "confidence": 90, "do": ["Позвоните в банк"], "dont": ["Не сообщайте код"],
        "dispute": None, "rewrite": None, "document": None, "claim_letter": "", "notes": []}


def make_client(handler) -> AnthropicLLMClient:
    llm = AnthropicLLMClient("sk-test", "claude-sonnet-5-5", "medium")
    llm._client = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                           http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    return llm


def message(text: str, stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 80}}


def request(**kw) -> LLMRequest:
    return LLMRequest(task="check:razvod", system="Системный промпт", user="<user_content>код</user_content>", schema=VerdictCard, **kw)


def test_request_shape_and_parsing():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.content)
        seen["beta"] = req.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=message(json.dumps(CARD, ensure_ascii=False)))

    llm = make_client(handler)
    result = asyncio.run(llm.generate(request(attachments=[Attachment("image", "image/png", "aGk=")])))
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"  # структурированный вывод по схеме
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["beta"]
    assert body["messages"][0]["content"][0]["type"] == "image"  # вложение перед текстом
    assert result.data.status == "red" and result.input_tokens == 120 and result.output_tokens == 80


def test_invalid_json_is_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        text = "не JSON" if calls["n"] == 1 else json.dumps(CARD, ensure_ascii=False)
        return httpx2.Response(200, json=message(text))

    result = asyncio.run(make_client(handler).generate(request()))
    assert calls["n"] == 2 and result.data.title == "Похоже на мошенничество"


def test_auth_error_in_russian():
    def handler(req):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        asyncio.run(make_client(handler).generate(request()))


def test_refusal_in_russian():
    def handler(req):
        return httpx2.Response(200, json=message("", stop_reason="refusal"))

    with pytest.raises(LLMError, match="отказался"):
        asyncio.run(make_client(handler).generate(request()))

"""Общий интерфейс для всех языковых моделей.

Суть: движок не знает, какая модель работает «под капотом». Он собирает
запрос LLMRequest (системный промпт, текст, схема ответа, вложения) и
вызывает client.generate(request). Каждый клиент сам решает, как
отправить запрос своему провайдеру.

Поле context — «сырые» данные проверки (найденные признаки, источники,
ссылки). Настоящей модели они уже вписаны в текст промпта, а демо-клиент
использует их напрямую, чтобы собрать правдоподобную карточку без ИИ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class LLMError(Exception):
    """Ошибка модели. Текст — для человека, по-русски."""


@dataclass
class Attachment:
    kind: str        # "image" или "pdf"
    media_type: str  # например "image/jpeg" или "application/pdf"
    data_b64: str    # содержимое в base64 (текстовая запись двоичных данных)


@dataclass
class LLMRequest:
    task: str                       # например "check:razvod", "simplify", "trainer_reply"
    system: str
    user: str
    schema: type[BaseModel]
    attachments: list[Attachment] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    user_id: int | None = None
    max_tokens: int = 4000


@dataclass
class LLMResult:
    data: BaseModel
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(ABC):
    """Абстрактный класс (ABC): описывает, что умеет любой клиент.

    @abstractmethod — «этот метод обязан реализовать каждый наследник».
    """

    name: str = "base"
    model: str = ""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResult: ...

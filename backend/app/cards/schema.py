"""Схема карточки вердикта — единый формат ответа во всех режимах.

Суть: и бот, и Mini App, и картинка для пересылки строятся из одного
объекта VerdictCard. Режимы отличаются тем, какие поля заполнены:
у /правда и /развод — статус, причины, источники; у /спор — ещё раздел
dispute; у /остынь — rewrite; у /чек — document; у /права — claim_letter.

Pydantic BaseModel — класс-«анкета»: описываем поля и их типы, а
Pydantic проверяет, что данные (например, JSON от языковой модели)
соответствуют анкете. Если нет — ValidationError, и мы просим модель
ответить заново.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Статус карточки:
#   green   🟢 надёжно
#   yellow  🟡 с оговорками
#   red     🔴 опасно или ложно
#   unknown ⚪ недостаточно данных
Status = Literal["green", "yellow", "red", "unknown"]

STATUS_EMOJI: dict[str, str] = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}

# Тип утверждения для /правда: факт, мнение, манипуляция, недостаточно данных.
# answer — ответ на вопрос по базе знаний МУИТ; announcement — разбор объявления; support — поддержка вместо вердикта.
ClaimKind = Literal["fact", "opinion", "manipulation", "insufficient", "scam", "document", "dispute", "rewrite", "rights", "answer", "announcement", "support"]


class Reason(BaseModel):
    """Одна причина вердикта и фраза из исходного текста, на которой она основана."""

    text: str = Field(description="Причина простым языком, одно предложение")
    quote: str = Field(default="", description="Точная цитата из исходного текста (дословно) или пустая строка")


class SourceRef(BaseModel):
    """Источник: название, ссылка, дата актуальности."""

    title: str
    url: str = ""
    date: str = Field(default="", description="Дата актуальности источника, ГГГГ-ММ-ДД")
    is_demo: bool = Field(default=False, description="Примерный источник демо-режима — не реальная норма")


class DisputeSection(BaseModel):
    """Раздел для /спор: не «кто прав», а из чего состоит спор."""

    position_a: str = ""
    position_b: str = ""
    confirmed_facts: list[str] = Field(default_factory=list)
    unconfirmed_claims: list[str] = Field(default_factory=list)
    to_prove: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    neutral_message: str = ""


class RewriteSection(BaseModel):
    """Раздел для /остынь."""

    calm_text: str = ""
    how_it_sounds: str = Field(default="", description="Как исходное сообщение прозвучит для адресата")


class DocumentSection(BaseModel):
    """Раздел для /чек: что от меня хотят."""

    what_they_want: str = ""
    amount: str = ""
    deadline: str = ""
    where_to_go: str = ""
    if_ignore: str = ""


class VerdictCard(BaseModel):
    """Карточка вердикта."""

    status: Status
    title: str = Field(description="Итог одной строкой, простым языком")
    kind: ClaimKind = "insufficient"
    reasons: list[Reason] = Field(default_factory=list, description="2–3 причины")
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100, description="Уверенность 0–100")
    do: list[str] = Field(default_factory=list, description="Что делать дальше")
    dont: list[str] = Field(default_factory=list, description="Чего НЕ делать")
    dispute: DisputeSection | None = None
    rewrite: RewriteSection | None = None
    document: DocumentSection | None = None
    claim_letter: str = Field(default="", description="Готовый текст претензии или заявления (для /права)")
    answer: str = Field(default="", description="Ответ на вопрос о МУИТ — только по <sources>, 1–4 предложения (для /ask)")
    notes: list[str] = Field(default_factory=list, description="Служебные пометки движка (демо, репутация и т. п.)")

    @property
    def emoji(self) -> str:
        return STATUS_EMOJI[self.status]


class SimpleExplanation(BaseModel):
    """Ответ на кнопку «Объяснить проще»."""

    text: str


class AgreementDraft(BaseModel):
    """Черновик договорённости для /договорились."""

    who: str = Field(default="", description="Кто участвует (как названы в тексте)")
    what: str = Field(default="", description="Что именно обещано")
    amount: str = Field(default="", description="Сумма или количество, если есть")
    deadline: str = Field(default="", description="Срок как в тексте")
    deadline_iso: str = Field(default="", description="Срок в формате ГГГГ-ММ-ДД, если его можно вычислить, иначе пусто")
    on_breach: str = Field(default="", description="Что будет при срыве, если сказано")
    missing: list[str] = Field(default_factory=list, description="Чего не хватает, чтобы договорённость была ясной")


class TrainerReply(BaseModel):
    """Реплика «собеседника» в тренажёре."""

    message: str
    finished: bool = False


class TrainerReview(BaseModel):
    """Разбор тренажёра."""

    immunity: int = Field(ge=0, le=100)
    summary: str
    red_flags: list[str] = Field(default_factory=list, description="Приёмы, которые использовал «мошенник»")
    good_moves: list[str] = Field(default_factory=list)
    mistakes: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ новые функции (планер, силлабус, карьера)


class SyllabusDeadline(BaseModel):
    title: str = Field(description="Что сдать или какая контрольная точка")
    date: str = Field(default="", description="Срок ГГГГ-ММ-ДД, если его можно вычислить по тексту, иначе пусто")
    when_text: str = Field(default="", description="Срок как написано в силлабусе (например, «7 неделя»)")
    weight: str = Field(default="", description="Вес в оценке, если указан (например, «10%»)")


class SyllabusCard(BaseModel):
    """Разбор силлабуса: только то, что написано в присланном тексте."""

    course: str = Field(default="", description="Название дисциплины, если указано")
    deadlines: list[SyllabusDeadline] = Field(default_factory=list)
    grading: list[str] = Field(default_factory=list, description="Из чего складывается оценка, с весами")
    retake_rules: list[str] = Field(default_factory=list, description="Правила пересдач")
    absence_rules: list[str] = Field(default_factory=list, description="Правила пропусков и опозданий")
    ai_policy: list[str] = Field(default_factory=list, description="Что сказано про использование ИИ, если есть")
    missing: list[str] = Field(default_factory=list, description="Чего в тексте нет, но стоит уточнить у преподавателя")


class PlanStep(BaseModel):
    title: str
    due_date: str = Field(default="", description="ГГГГ-ММ-ДД, не позже дедлайна задачи")


class StepPlan(BaseModel):
    """План работы над большой задачей: шаги с датами. Бот планирует, но не делает работу за студента."""

    steps: list[PlanStep] = Field(default_factory=list)


class ResumeReview(BaseModel):
    """Разбор резюме: что хорошо, что улучшить. Без переписывания резюме «за человека»."""

    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    improve: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)

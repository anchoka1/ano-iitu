"""Сигналы кризиса: тревога, выгорание, мысли о вреде себе.

Правило: на такие сообщения бот НЕ выносит «вердикт» (никаких 🟢🔴 и
«недостаточно данных»), а бережно отвечает и показывает контакты
психологической службы МУИТ и круглосуточных линий помощи — с
официальной страницы (data/iitu/services.json, записи psych и hotline).

Два уровня:
  acute    — мысли о самоубийстве или самоповреждении: срабатывает всегда;
  distress — тревога, выгорание, «нет сил»: только если человек пишет о себе
             (я / мне / меня), чтобы пересланная новость о «выгорании
             сотрудников» не превращалась в карточку поддержки.
Распознавание по шаблонам, без ИИ: работает и в демо-режиме, и без связи.
"""

from __future__ import annotations

import html
import re

from backend.app.cards.schema import Reason, SourceRef, VerdictCard
from backend.app.university.catalog import catalog, contact_line, get_service

ACUTE = re.compile(
    r"суицид\w*|покончить\s+с\s+собой|поконч\w*\s+с\s+(собой|жизнью)|убить\s+себя|убью\s+себя|"
    r"не\s+хочу\s+(больше\s+)?жить|не\s+хочется\s+жить|хочу\s+умереть|лучше\s+бы\s+меня\s+не\s+было|"
    r"нет\s+смысла\s+жить|жить\s+не\s+хочется|(порезать|режу|резать)\s+(себя|вены)|самоповрежд\w*|"
    r"наглотаться\s+таблеток|прыгнуть\s+с\s+(крыши|моста)|свести\s+счёты\s+с\s+жизнью|свести\s+счеты\s+с\s+жизнью",
    re.IGNORECASE,
)
DISTRESS = re.compile(
    r"выгор\w*|тревог\w*|тревожн\w*|паническ\w+\s+атак\w*|депресс\w*|нет\s+сил|не\s+справля\w*|"
    r"не\s+могу\s+больше|всё\s+бессмысленно|все\s+бессмысленно|опустились\s+руки|постоянно\s+плачу|"
    r"не\s+сплю\s+ночами|мне\s+очень\s+плохо|мне\s+страшно",
    re.IGNORECASE,
)
FIRST_PERSON = re.compile(r"\b(я|мне|меня|мной|у\s+меня|сам[аи]?)\b", re.IGNORECASE)


def detect(text: str) -> str:
    """Возвращает 'acute', 'distress' или ''."""
    text = text or ""
    if ACUTE.search(text):
        return "acute"
    if DISTRESS.search(text) and FIRST_PERSON.search(text):
        return "distress"
    return ""


def _help_services() -> list[dict]:
    return [s for s in (get_service("psych"), get_service("hotline")) if s]


def support_card(level: str) -> VerdictCard:
    """Карточка поддержки вместо вердикта."""
    psych, hotline = get_service("psych"), get_service("hotline")
    title = ("Ты не один. Прямо сейчас есть люди, которые помогут"
             if level == "acute" else "Похоже, тебе сейчас тяжело — это нормально, и с этим можно справиться")
    reasons = [Reason(text="Здесь я ничего не проверяю: твоё состояние важнее любой проверки.")]
    if psych:
        reasons.append(Reason(text="Психологическая служба МУИТ — бесплатно и конфиденциально: информацию не передают в деканат, родителям или преподавателям."))
    do: list[str] = []
    if level == "acute" and hotline:
        do += [contact_line(c) for c in hotline["contacts"]]
        do.append("Если есть угроза жизни прямо сейчас — звони 112")
    if psych:
        do += [contact_line(c) for c in psych["contacts"][:3]]
    if level != "acute" and hotline:
        do.append(contact_line(hotline["contacts"][0]))
    source = (psych or hotline or {}).get("source", "")
    return VerdictCard(
        status="unknown", kind="support", title=title, reasons=reasons, confidence=100,
        do=do[:6], dont=["Не оставайся с этим один на один — напиши или позвони кому-то, кому доверяешь"],
        sources=[SourceRef(title="Путеводитель студента МУИТ: психологическая служба", url=source, date=catalog().get("checked", ""))] if source else [],
    )


def support_html(level: str) -> str:
    """То же — текстом для Telegram (когда человек просто написал боту, без режима)."""
    card = support_card(level)
    e = html.escape
    lines = [f"💙 <b>{e(card.title)}</b>", ""]
    lines += [e(r.text) for r in card.reasons[1:]]
    lines += ["", "<b>Куда обратиться:</b>"] + [e(x) for x in card.do]
    if card.sources:
        lines += ["", f'<a href="{e(card.sources[0].url)}">Источник: путеводитель студента МУИТ</a>']
    return "\n".join(lines)

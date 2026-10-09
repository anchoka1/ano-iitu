"""Сверка платёжных реквизитов «для оплаты обучения» с официальными реквизитами МУИТ.

Схема мошенничества: в чат группы пишут «оплатите обучение до пятницы
на новый счёт KZ…» или «скидываемся на пересдачу, карта 4400…».
Если в сообщении про оплату учёбы есть IBAN/БИН, которых нет на
официальной странице, — это сильный признак подделки.

Сами реквизиты бот пользователю не пересказывает (правило: реквизиты,
суммы и сроки — только ссылкой на официальную страницу).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from backend.app.core.signals import SignalHit
from backend.app.university.reference import load_json

IBAN_RE = re.compile(r"\bKZ\s?\d{2}(?:\s?[0-9A-Z]{4}){4}\b", re.IGNORECASE)
BIN_RE = re.compile(r"\bБИН\D{0,5}(\d{12})\b", re.IGNORECASE)
CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
TUITION_RE = re.compile(r"обучени\w*|учёб\w*|учеб\w*|муит|iitu|университет\w*|универ\w*|контракт\w*|пересдач\w*|сесси\w*|семестр\w*|деканат\w*|общежит\w*", re.IGNORECASE)
CARD_PHRASE_RE = re.compile(r"(на\s+(мою\s+|свою\s+|личную\s+)?карт\w*|мне\s+на\s+карт\w*|по\s+номеру\s+телефона|kaspi\s+gold)", re.IGNORECASE)
PAY_RE = re.compile(r"оплат\w*|перевод\w*|перевед\w*|переведи\w*|скин\w*|скид\w*|сбор\w*|собира\w*|реквизит\w*|сч[её]т\w*|взнос\w*", re.IGNORECASE)


@dataclass(frozen=True)
class Official:
    iban: frozenset[str]
    bin: frozenset[str]
    source: str
    checked: str


@lru_cache
def official() -> Official:
    raw = load_json("requisites.json")
    return Official(frozenset(x.upper() for x in raw["iban"]), frozenset(raw["bin"]), raw["source"], raw.get("checked", ""))


def _norm_iban(value: str) -> str:
    return re.sub(r"\s", "", value).upper()


def check(text: str) -> list[SignalHit]:
    """Признаки для режимов «Развод?» и «Чек»: чужие реквизиты в сообщении про оплату учёбы."""
    if not text or not (TUITION_RE.search(text) and PAY_RE.search(text)):
        return []
    ref = official()
    hits: list[SignalHit] = []
    foreign_iban = [m.group(0) for m in IBAN_RE.finditer(text) if _norm_iban(m.group(0)) not in ref.iban]
    foreign_bin = [m.group(0) for m in BIN_RE.finditer(text) if m.group(1) not in ref.bin]
    if foreign_iban or foreign_bin:
        hits.append(SignalHit(
            "iitu_foreign_requisites", "Реквизиты не совпадают с официальными реквизитами МУИТ",
            f"Университет публикует реквизиты для оплаты на своём сайте ({ref.source}). Сверяй каждую цифру там, а не в чате.",
            3, (foreign_iban + foreign_bin)[:2],
        ))
    cards = [m.group(0) for m in CARD_RE.finditer(text)] or [m.group(0) for m in CARD_PHRASE_RE.finditer(text)]
    if cards:
        hits.append(SignalHit(
            "iitu_card_payment", "Оплату «за учёбу» просят перевести на личную карту",
            "Университет принимает оплату по официальным реквизитам и через разделы «Образование» в банковских приложениях, а не переводом на карту человека.",
            3, cards[:1],
        ))
    return hits


def official_numbers() -> list[str]:
    """Официальные БИН (12 цифр) — их не маскируем перед моделью: иначе нельзя сверить счёт."""
    return sorted(official().bin)


def matches_official(text: str) -> bool:
    """В тексте есть IBAN, и все они совпадают с официальными (полезная пометка, но не гарантия)."""
    ibans = [_norm_iban(m.group(0)) for m in IBAN_RE.finditer(text or "")]
    return bool(ibans) and all(i in official().iban for i in ibans)

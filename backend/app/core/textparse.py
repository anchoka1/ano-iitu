"""Извлечение сумм и дат из текста (без ИИ).

Нужно демо-режиму (/чек, /договорились) и для напоминаний: из «до 15
октября» получаем дату 2026-10-15, чтобы бот напомнил накануне.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}

MONEY_RE = re.compile(
    r"(\d[\d\s ]{0,12}(?:[.,]\d{1,2})?)\s*(₸|тг\.?|тенге|kzt|руб\w*|₽|\$|долл\w*|usd|мрп)|(\$)\s*(\d[\d\s]{0,10})",
    re.IGNORECASE,
)
NUM_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b")
WORD_DATE_RE = re.compile(r"\b(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)(?:\s+(\d{4}))?", re.IGNORECASE)
RELATIVE_RE = re.compile(r"\b(сегодня|завтра|послезавтра|через\s+(\d+|неделю|месяц|день)\s*(дн\w*|недел\w*|месяц\w*)?|в\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)|до\s+конца\s+(недели|месяца))\b", re.IGNORECASE)


def find_money(text: str) -> list[str]:
    result = []
    for m in MONEY_RE.finditer(text):
        value = m.group(0).strip()
        if value not in result:
            result.append(value)
    return result


def _month_number(word: str) -> int | None:
    word = word.lower()
    for stem, number in MONTHS.items():
        if word.startswith(stem):
            return number
    return None


def _future(day: int, month: int, today: date, year: int | None = None) -> date | None:
    try:
        candidate = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and candidate < today:
        candidate = candidate.replace(year=today.year + 1)
    return candidate


def find_deadline(text: str, today: date) -> tuple[str, str]:
    """Возвращает (срок как в тексте, срок в ISO ГГГГ-ММ-ДД или "")."""
    m = WORD_DATE_RE.search(text)
    if m:
        month = _month_number(m.group(2))
        if month:
            d = _future(int(m.group(1)), month, today, int(m.group(3)) if m.group(3) else None)
            if d:
                return m.group(0), d.isoformat()
    m = NUM_DATE_RE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = m.group(3)
        y = None
        if year:
            y = int(year) + (2000 if len(year) == 2 else 0)
        if 1 <= month <= 12:
            d = _future(day, month, today, y)
            if d:
                return m.group(0), d.isoformat()
    m = RELATIVE_RE.search(text)
    if m:
        phrase = m.group(0).lower()
        d: date | None = None
        if phrase == "сегодня":
            d = today
        elif phrase == "завтра":
            d = today + timedelta(days=1)
        elif phrase == "послезавтра":
            d = today + timedelta(days=2)
        elif phrase.startswith("через"):
            amount = m.group(2)
            unit = (m.group(3) or amount).lower()
            n = int(amount) if amount.isdigit() else 1
            if unit.startswith("недел"):
                d = today + timedelta(weeks=n)
            elif unit.startswith("месяц"):
                d = today + timedelta(days=30 * n)
            else:
                d = today + timedelta(days=n)
        elif phrase.startswith("в "):
            for stem, weekday in WEEKDAYS.items():
                if m.group(4) and m.group(4).lower().startswith(stem):
                    delta = (weekday - today.weekday()) % 7 or 7
                    d = today + timedelta(days=delta)
        elif "недели" in phrase:
            d = today + timedelta(days=6 - today.weekday())
        elif "месяца" in phrase:
            next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            d = next_month - timedelta(days=1)
        if d:
            return m.group(0), d.isoformat()
    return "", ""

"""Академический календарь МУИТ.

Даты берутся ТОЛЬКО из data/iitu/calendar_*.json — их переносят вручную
с официальной страницы календаря (там опубликованы PDF). В коде дат нет.
Если файла на текущий учебный год нет — бот честно говорит, что даты
не загружены, и даёт ссылку на страницу календаря.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from backend.app.university.reference import DATA_DIR

CALENDAR_PAGE = "https://iitu.edu.kz/ru/for-students/akademicheskii-kalendar/"
KIND_EMOJI = {
    "midterm": "📝", "session": "🎓", "fx": "🔁", "registration": "🗂", "practice": "🛠", "thesis": "📘",
    "final": "🏁", "holidays": "🌴", "holiday": "🎈", "start": "👋", "summer": "☀️", "military": "🎖",
    "study": "📖", "admin": "📄",
}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: date
    end: date
    courses: tuple[str, ...]
    kind: str
    remind: bool
    source_title: str
    source_url: str
    note: str = ""

    @property
    def emoji(self) -> str:
        return KIND_EMOJI.get(self.kind, "📅")

    def dates_label(self) -> str:
        return format_range(self.start, self.end)

    def public(self) -> dict:
        return {
            "id": self.id, "title": self.title, "start": self.start.isoformat(), "end": self.end.isoformat(),
            "dates": self.dates_label(), "kind": self.kind, "emoji": self.emoji, "courses": list(self.courses),
            "source_title": self.source_title, "source_url": self.source_url, "note": self.note,
        }


@dataclass(frozen=True)
class Calendar:
    academic_year: str
    year_start: date
    checked: str
    page_url: str
    events: tuple[Event, ...]


def format_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def format_range(start: date, end: date) -> str:
    if start == end:
        return format_date(start)
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{format_date(start)} – {format_date(end)}"


def _parse(raw: dict) -> Calendar:
    docs = raw.get("documents", {})
    events = []
    for item in raw["events"]:
        doc = docs.get(item.get("doc", ""), {})
        events.append(Event(
            id=item["id"], title=item["title"], start=date.fromisoformat(item["start"]), end=date.fromisoformat(item["end"]),
            courses=tuple(item.get("courses", [])), kind=item.get("kind", ""), remind=bool(item.get("remind")),
            source_title=doc.get("title", "Академический календарь"), source_url=doc.get("url", raw.get("page_url", CALENDAR_PAGE)),
            note=item.get("note", ""),
        ))
    events.sort(key=lambda e: (e.start, e.end))
    return Calendar(raw["academic_year"], date.fromisoformat(raw["year_start"]), raw.get("checked", ""),
                    raw.get("page_url", CALENDAR_PAGE), tuple(events))


@lru_cache
def calendars() -> tuple[Calendar, ...]:
    """Все загруженные календари (файлы calendar_*.json), по порядку учебных лет."""
    items = [_parse(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(DATA_DIR.glob("calendar_*.json"))]
    return tuple(sorted(items, key=lambda c: c.year_start))


def calendar_for(day: date) -> Calendar | None:
    """Календарь учебного года, в который попадает дата (или ближайший прошедший)."""
    current = None
    for cal in calendars():
        if cal.year_start <= day:
            current = cal
    if current is None or day > current.year_start + timedelta(days=372):
        return None
    return current


def academic_year_start(day: date) -> int:
    """Год начала учебного года: с 1 августа — новый учебный год (для перехода с курса на курс)."""
    return day.year if day.month >= 8 else day.year - 1


def events_for(course: str, day: date, days_ahead: int = 7, include_ongoing: bool = True) -> list[Event]:
    """События для курса, которые идут сейчас или начнутся в ближайшие days_ahead дней."""
    cal = calendar_for(day)
    if cal is None:
        return []
    horizon = day + timedelta(days=days_ahead)
    result = []
    for e in cal.events:
        if course and course not in e.courses:
            continue
        upcoming = day <= e.start <= horizon
        ongoing = include_ongoing and e.start <= day <= e.end and e.kind not in ("study",)
        if upcoming or ongoing:
            result.append(e)
    return result


def next_events(course: str, day: date, limit: int = 5, kinds: set[str] | None = None) -> list[Event]:
    cal = calendar_for(day)
    if cal is None:
        return []
    items = [e for e in cal.events if e.end >= day and (not course or course in e.courses) and (kinds is None or e.kind in kinds)]
    return items[:limit]


def get_events(ids: list[str], day: date) -> list[Event]:
    cal = calendar_for(day)
    if cal is None:
        return []
    by_id = {e.id: e for e in cal.events}
    return [by_id[i] for i in ids if i in by_id]


def is_general(event: Event) -> bool:
    """Событие касается большинства (1–3 курс бакалавриата) — показываем тем, кто не указал курс."""
    return bool(set(event.courses) & {"1", "2", "3"}) and not event.id.startswith("g_ext")


def reminders_due(day: date, remind_days: int) -> list[Event]:
    """События с напоминанием, которые начинаются через remind_days дней или сегодня."""
    cal = calendar_for(day)
    if cal is None:
        return []
    targets = {day, day + timedelta(days=remind_days)}
    return [e for e in cal.events if e.remind and e.start in targets]


def as_source_text(day: date) -> str:
    """Календарь текстом — для базы знаний (поиск по вопросам «когда сессия?»)."""
    cal = calendar_for(day)
    if cal is None:
        return ""
    lines = []
    from backend.app.university.reference import course_title

    for e in cal.events:
        who = ", ".join(course_title(c) or c for c in e.courses)
        # Даты в нескольких написаниях: правило достоверности пропускает в ответ только даты из источника.
        span = "" if e.start == e.end else f" (с {format_date(e.start)} по {format_date(e.end)})"
        numeric = e.start.strftime("%d.%m.%Y") + ("" if e.start == e.end else "–" + e.end.strftime("%d.%m.%Y"))
        lines.append(f"{e.title} ({who}): {e.dates_label()} {e.start.year}{span}, {numeric}.")
    return "\n".join(lines)

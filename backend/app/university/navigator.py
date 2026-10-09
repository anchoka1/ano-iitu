"""Навигатор «Что тебя ждёт» по курсам (data/iitu/navigator.json) + ближайшие даты календаря."""

from __future__ import annotations

import html
from datetime import date
from functools import lru_cache

from backend.app.university import calendar
from backend.app.university.catalog import get_service
from backend.app.university.reference import load_json

STATUS_LABEL = {
    "confirmed": "✅ подтверждено",
    "unconfirmed": "❔ не подтверждено официально",
    "corrected": "✏️ уточнено по официальному источнику",
}


@lru_cache
def navigator() -> dict:
    return load_json("navigator.json")


def course_ids() -> list[str]:
    return list(navigator()["courses"].keys())


def course_block(course: str, today: date | None = None) -> dict | None:
    """Данные курса для API и бота: пункты, сервисы и ближайшие события календаря."""
    from backend.app.university.reference import nav_course

    data = navigator()["courses"].get(nav_course(course))
    if data is None:
        return None
    today = today or date.today()
    items = []
    for item in data["items"]:
        events = [e.public() for e in calendar.get_events(item.get("events", []), today)]
        services = [s for s in (get_service(sid) for sid in item.get("services", [])) if s]
        items.append({**item, "status_label": STATUS_LABEL.get(item["status"], ""), "events": events,
                      "services": [{"id": s["id"], "title": s["title"], "emoji": s["emoji"]} for s in services]})
    upcoming = [e.public() for e in calendar.next_events(course, today, limit=6, kinds=None) if e.kind not in ("study", "holidays")]
    return {
        "course": course, "title": data["title"], "lead": data["lead"], "items": items, "upcoming": upcoming,
        "checked": navigator().get("checked", ""), "calendar_page": calendar.CALENDAR_PAGE,
    }


def year_changes(course: str) -> list[dict]:
    """Что меняется на новом курсе: пункты с пометкой new."""
    from backend.app.university.reference import nav_course

    data = navigator()["courses"].get(nav_course(course))
    return [i for i in data["items"] if i.get("new")] if data else []


def render_course_html(course: str, today: date | None = None, limit_items: int = 12) -> str:
    """Навигатор курса текстом для Telegram (укладываемся в лимит сообщения 4096 символов)."""
    for limit in range(limit_items, 0, -1):
        text = _render_course(course, today, limit)
        if len(text) <= 4000:
            return text
    return text


def _render_course(course: str, today: date | None, limit_items: int) -> str:
    block = course_block(course, today)
    if block is None:
        return ""
    e = html.escape
    lines = [f"🧭 <b>{e(block['title'].upper())}: ЧТО ТЕБЯ ЖДЁТ</b>", f"<i>{e(block['lead'])}</i>", ""]
    for n, item in enumerate(block["items"][:limit_items], 1):
        mark = "" if item["status"] == "confirmed" else f" <i>({e(item['status_label'])})</i>"
        lines.append(f"<b>/{n:02d} {e(item['title'])}</b>{mark}")
        lines.append(e(item["text"]))
        if item["events"]:
            lines.append("📅 " + "; ".join(f"{e(ev['title'])}: {e(ev['dates'])}" for ev in item["events"][:2]))
        if item["services"]:
            lines.append("➡️ " + ", ".join(e(s["title"]) for s in item["services"]))
        lines.append(f'<a href="{e(item["source"])}">источник</a>')
        lines.append("")
    if block["upcoming"]:
        lines.append("<b>📅 Ближайшие даты</b>")
        for ev in block["upcoming"][:5]:
            lines.append(f"{ev['emoji']} {e(ev['dates'])} — {e(ev['title'])}")
        lines.append(f'<a href="{e(block["calendar_page"])}">Академический календарь на сайте</a>')
    return "\n".join(lines).strip()


def render_changes_html(course: str) -> str:
    """Сводка «что меняется в этом году» при переходе на новый курс."""
    from backend.app.university.reference import nav_course

    data = navigator()["courses"].get(nav_course(course))
    if data is None:
        return ""
    e = html.escape
    lines = [f"🎉 <b>Добро пожаловать на {e(data['title'].lower())}!</b>", f"<i>{e(data['lead'])}</i>", "", "<b>Что меняется в этом году:</b>"]
    for item in year_changes(course)[:6]:
        lines.append(f"• <b>{e(item['title'])}</b> — {e(item['text'])}")
    lines += ["", "Подробнее — /navigator"]
    return "\n".join(lines)

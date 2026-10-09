"""Каталог сервисов и контактов «Куда идти с каким вопросом» (data/iitu/services.json)."""

from __future__ import annotations

import html
from functools import lru_cache

from backend.app.rag.store import tokenize
from backend.app.university.reference import load_json

CONTACT_EMOJI = {"email": "✉️", "phone": "📞", "address": "📍", "link": "🔗", "hours": "🕘"}


@lru_cache
def catalog() -> dict:
    return load_json("services.json")


def services() -> list[dict]:
    return catalog()["services"]


def get_service(service_id: str) -> dict | None:
    return next((s for s in services() if s["id"] == service_id), None)


def find(query: str, limit: int = 3) -> list[dict]:
    """Подбор подразделения по словам вопроса (для «не знаю — обратись туда-то»)."""
    words = set(tokenize(query))
    if not words:
        return []
    scored = []
    for s in services():
        tokens = set(tokenize(" ".join([s["title"], s["for"], s.get("tags", "")])))
        score = len(words & tokens)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def contact_line(contact: dict) -> str:
    value = contact["value"]
    note = f" ({contact['note']})" if contact.get("note") else ""
    return f"{CONTACT_EMOJI.get(contact['type'], '•')} {value}{note}"


def render_service_html(service: dict) -> str:
    e = html.escape
    lines = [f"{service['emoji']} <b>{e(service['title'])}</b>", e(service["for"])]
    lines += [e(contact_line(c)) for c in service["contacts"]]
    lines.append(f'<a href="{e(service["source"])}">источник</a> · проверено {e(catalog().get("checked", ""))}')
    return "\n".join(lines)


def render_catalog_html() -> str:
    e = html.escape
    lines = ["📍 <b>КУДА ИДТИ С КАКИМ ВОПРОСОМ</b>", ""]
    for n, s in enumerate(services(), 1):
        lines.append(f"<b>/{n:02d} {s['emoji']} {e(s['title'])}</b> — {e(s['for'])}")
        first = s["contacts"][:2]
        if first:
            lines.append("   " + " · ".join(e(contact_line(c)) for c in first))
    lines += ["", f"<i>Контакты — с официальных страниц МУИТ, проверено {e(catalog().get('checked', ''))}. Полный справочник: https://iitu.edu.kz/ru/contacts/</i>"]
    return "\n".join(lines)

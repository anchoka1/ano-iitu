"""«Предметы»: всё о предмете в одном месте.

Предмет — это то, что студент изучает в этом семестре. Берём его из двух источников, ничего не придумывая:
  - силлабусы, которые студент загрузил (название дисциплины из силлабуса);
  - страницы дисциплин (учебные хабы вида discipline), к которым он присоединился.
Если силлабус привязан к странице дисциплины — это один предмет.

Ключ предмета: «h<id>» — есть страница дисциплины, «s<id>» — только силлабус.
Прогресс по предмету = выполненные задачи плана с этим предметом / все задачи этого предмета.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select

from backend.app.cards.schema import SyllabusCard
from backend.app.db.base import get_sessionmaker
from backend.app.db.models_campus import Hub, HubMember, Task
from backend.app.db.models_mod import Syllabus
from backend.app.services import campus, planner


class SubjectError(Exception):
    pass


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _tasks_for(session, user_id: int, names: set[str], syllabus_ids: set[int]) -> list[Task]:
    """Задачи предмета: по полю «предмет» или по ссылке на силлабус (sy<id>:...)."""
    rows = session.scalars(select(Task).where(Task.user_id == user_id, Task.parent_id.is_(None), Task.board_id.is_(None)))
    out = []
    for task in rows:
        ref = task.source_ref or ""
        by_ref = task.source == "syllabus" and any(ref.startswith(f"sy{sid}:") for sid in syllabus_ids)
        if by_ref or (_norm(task.subject) and _norm(task.subject) in names):
            out.append(task)
    return out


def _summary(tasks: list[Task]) -> dict:
    today = date.today().isoformat()
    total = len(tasks)
    done = sum(t.status == "done" for t in tasks)
    upcoming = sorted((t for t in tasks if t.status != "done" and t.due_date), key=lambda t: (t.due_date, t.due_time or "99"))
    nxt = upcoming[0] if upcoming else None
    return {"tasks_total": total, "tasks_done": done, "percent": round(100 * done / total) if total else 0,
            "overdue": sum(1 for t in upcoming if t.due_date < today),
            "next": {"id": nxt.id, "title": nxt.title, "due_date": nxt.due_date, "due_time": nxt.due_time,
                     "overdue": nxt.due_date < today} if nxt else None}


def _collect(session, user_id: int) -> list[dict]:
    syllabi = list(session.scalars(select(Syllabus).where(Syllabus.user_id == user_id).order_by(Syllabus.created_at.desc())))
    member_hub_ids = set(session.scalars(select(HubMember.hub_id).where(HubMember.user_id == user_id)))
    hub_ids = member_hub_ids | {s.hub_id for s in syllabi if s.hub_id}
    hubs = {h.id: h for h in session.scalars(select(Hub).where(Hub.id.in_(hub_ids), Hub.kind == "discipline", Hub.status == "active"))} if hub_ids else {}
    items: dict[str, dict] = {}
    for hub in hubs.values():
        items[f"h{hub.id}"] = {"key": f"h{hub.id}", "title": hub.title, "emoji": hub.emoji or "📚", "hub_id": hub.id,
                               "syllabus_ids": [], "course": hub.course or ""}
    for sy in syllabi:
        key = f"h{sy.hub_id}" if sy.hub_id in hubs else f"s{sy.id}"
        item = items.setdefault(key, {"key": key, "title": sy.title, "emoji": "📚", "hub_id": None, "syllabus_ids": [], "course": ""})
        item["syllabus_ids"].append(sy.id)
    for item in items.values():
        names = {_norm(item["title"])}
        for sid in item["syllabus_ids"]:
            row = next(s for s in syllabi if s.id == sid)
            names.add(_norm(row.title))
        item.update(_summary(_tasks_for(session, user_id, names, set(item["syllabus_ids"]))))
        item["syllabus_id"] = item["syllabus_ids"][0] if item["syllabus_ids"] else None
        item["_names"] = names
    return sorted(items.values(), key=lambda x: ((x["next"] or {}).get("due_date") or "9999", x["title"].lower()))


def list_subjects(user_id: int) -> dict:
    with get_sessionmaker()() as session:
        items = _collect(session, user_id)
    for x in items:
        x.pop("_names", None)
    return {"items": items, "semester": semester_progress()}


def get_subject(user_id: int, key: str) -> dict:
    with get_sessionmaker()() as session:
        items = _collect(session, user_id)
        item = next((x for x in items if x["key"] == key), None)
        if item is None:
            raise SubjectError("Предмет не найден. Загрузите силлабус или присоединитесь к странице дисциплины.")
        tasks = _tasks_for(session, user_id, item.pop("_names"), set(item["syllabus_ids"]))
        today = planner.local_today()
        item["tasks"] = sorted((planner.task_dict(t, today) for t in tasks), key=lambda t: (t["status"] == "done", t["due_date"] or "9999"))
        syllabi = []
        for sid in item["syllabus_ids"]:
            row = session.get(Syllabus, sid)
            card = SyllabusCard.model_validate(json.loads(row.card_json or "{}"))
            syllabi.append({"id": row.id, "title": row.title, "card": card.model_dump(), "added_to_plan": row.added_to_plan,
                            "dated": sum(1 for d in card.deadlines if d.date)})
    item["syllabi"] = syllabi
    rules = campus.grading()
    item["grading"] = {k: rules.get(k) for k in ("formula", "admission_min_percent", "admission_note", "pass_total_percent", "pass_note",
                                                 "final_weight_percent", "letter_scale", "source_title", "source_url", "checked")}
    return item


def semester_progress(today: date | None = None) -> dict | None:
    """Кольцо семестра: сколько недель теоретического обучения прошло (по академкалендарю)."""
    from backend.app.university import calendar as acal

    today = today or date.today()
    cal = acal.calendar_for(today)
    if cal is None:
        return None
    studies = [e for e in cal.events if e.kind == "study" and "1" in e.courses]
    current = next((e for e in studies if e.start <= today <= e.end), None)
    if current is None:
        upcoming = [e for e in studies if e.start > today]
        if not upcoming:
            return None
        nxt = upcoming[0]
        return {"title": nxt.title, "percent": 0, "week": 0, "weeks": max(1, ((nxt.end - nxt.start).days + 6) // 7),
                "starts_in_days": (nxt.start - today).days, "source_url": nxt.source_url}
    weeks = max(1, ((current.end - current.start).days + 6) // 7)
    week = min(weeks, (today - current.start).days // 7 + 1)
    percent = round(100 * ((today - current.start).days + 1) / ((current.end - current.start).days + 1))
    return {"title": current.title, "percent": max(0, min(100, percent)), "week": week, "weeks": weeks, "starts_in_days": 0,
            "source_url": current.source_url}

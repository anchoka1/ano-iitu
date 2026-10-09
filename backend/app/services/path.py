"""«Мой путь» — один экран вместо трёх функций (квиз, капсула, «Спроси старшекурсника») + чек-лист курса.

Логика одна, шаги по порядку:
  1. короткий квиз при первом входе — что важно знать про учёбу в МУИТ;
  2. чек-лист под твой курс (из навигатора) — отмечаешь сделанное, любой пункт можно отправить в план;
  3. вопросы старшекурсникам — общие, с модерацией, ответы видны всем и привязаны к хабам предметов;
  4. письмо себе — бот пришлёт его в конце семестра (по академическому календарю).
Всё, что нужно экрану, приходит одним запросом (overview).
"""

from __future__ import annotations

import json

from sqlalchemy import func, select

from backend.app.db.base import get_sessionmaker
from backend.app.db.models_campus import Hub, HubMember, Post
from backend.app.services import anon, campus, community, prefs


def _course(user_id: int) -> str:
    return campus.get_user_course(user_id)


def questions_for(user_id: int, course: str, hub_id: int | None = None, limit: int = 8) -> list[dict]:
    """Вопросы старшекурсникам: сначала с ответами, по моему курсу (или по хабу предмета)."""
    with get_sessionmaker()() as session:
        query = select(Post).where(Post.kind == "senior_q", Post.status == "approved")
        if hub_id:
            query = query.where(Post.hub_id == hub_id)
        rows = list(session.scalars(query.order_by(Post.created_at.desc()).limit(200)))
        answers = dict(session.execute(select(Post.parent_id, func.count()).where(Post.kind == "senior_a", Post.status == "approved",
                                                                                   Post.parent_id.in_([p.id for p in rows] or [0]))
                                       .group_by(Post.parent_id)).all())
        hubs = {h.id: h.title for h in session.scalars(select(Hub))}
        out = []
        for p in rows:
            data = json.loads(p.data_json or "{}")
            if not hub_id and course and data.get("course") and data["course"] != course:
                continue
            item = community.post_dict(p, user_id)
            item["answers"] = answers.get(p.id, 0)
            item["hub_title"] = hubs.get(p.hub_id, "")
            out.append(item)
        out.sort(key=lambda x: x["answers"] == 0)  # с ответами — выше; внутри — новые сверху (rows уже по дате)
        return out[:limit]


def my_questions(user_id: int) -> list[dict]:
    h = anon.author_hash(user_id)
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.kind == "senior_q", Post.author_hash == h).order_by(Post.created_at.desc()).limit(20))
        out = []
        for p in rows:
            item = community.post_dict(p, user_id)
            item["answers"] = session.scalar(select(func.count()).select_from(Post).where(Post.parent_id == p.id, Post.status == "approved")) or 0
            out.append(item)
        return out


def my_hubs(user_id: int) -> list[dict]:
    with get_sessionmaker()() as session:
        ids = [m.hub_id for m in session.scalars(select(HubMember).where(HubMember.user_id == user_id))]
        hubs = session.scalars(select(Hub).where(Hub.status == "active", Hub.kind == "discipline").order_by(Hub.title))
        return [{"id": h.id, "title": h.title, "mine": h.id in ids, "course": h.course} for h in hubs]


def overview(user_id: int) -> dict:
    course = _course(user_id)
    quiz = campus.quiz_questions()
    best_raw = prefs.get(user_id, "quiz.best", "")
    try:
        checklist = campus.checklist(user_id, course or "1")
    except campus.CampusError:
        checklist = None
    try:
        deliver_on = campus.capsule_date(course)
    except campus.CampusError:
        deliver_on = ""
    letters = campus.capsule_list(user_id)
    mine = my_questions(user_id)
    steps = {
        "quiz": best_raw != "",
        "checklist": bool(checklist and checklist["done"] >= max(1, checklist["total"] // 2)),
        "question": bool(mine),
        "letter": bool(letters),
    }
    next_step = next((k for k in ("quiz", "checklist", "question", "letter") if not steps[k]), "")
    return {
        "course": course,
        "steps": steps, "next": next_step,
        "quiz": {"done": best_raw != "", "best": int(best_raw or 0), "total": len(quiz["questions"]), "questions": quiz["questions"],
                 "source_title": quiz["source_title"], "source_url": quiz["source_url"]},
        "checklist": checklist,
        "questions": questions_for(user_id, course),
        "my_questions": mine,
        "hubs": my_hubs(user_id),
        "letter": {"deliver_on": deliver_on, "items": letters},
    }

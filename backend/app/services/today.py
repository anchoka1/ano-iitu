"""Главная = «что важно сегодня и именно мне», а не витрина всех функций.

Блоки (пустые не показываются):
  - ближайшие дедлайны (план, включая силлабусы) и сколько задач бот предлагает добавить;
  - свежие предупреждения Радара (одобренные модератором за неделю);
  - новые ответы на мои вопросы старшекурсникам;
  - решения по моим обращениям за неделю (опубликовано / отклонено с причиной / вердикт по слуху);
  - ближайшее событие академкалендаря для моего курса;
  - модератору — сколько заявок ждёт проверки.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from backend.app.core.features import enabled
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import iso_utc
from backend.app.db.models_campus import Application, Post
from backend.app.i18n import t
from backend.app.services import anon, campus, planner, roles


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def radar_fresh(limit: int = 3, days: int = 7) -> list[dict]:
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.kind == "radar", Post.status == "approved", Post.created_at >= _since(days))
                               .order_by(Post.created_at.desc()).limit(limit))
        return [{"id": p.id, "title": p.title, "body": p.body[:200], "created_at": iso_utc(p.moderated_at or p.created_at)} for p in rows]


def new_answers(user_id: int, days: int = 14, limit: int = 3) -> list[dict]:
    h = anon.author_hash(user_id)
    with get_sessionmaker()() as session:
        mine = {p.id: p.title for p in session.scalars(select(Post).where(Post.kind == "senior_q", Post.author_hash == h))}
        if not mine:
            return []
        rows = session.scalars(select(Post).where(Post.kind == "senior_a", Post.status == "approved", Post.parent_id.in_(list(mine)),
                                                  Post.created_at >= _since(days)).order_by(Post.created_at.desc()).limit(limit))
        return [{"question_id": a.parent_id, "question": mine.get(a.parent_id, ""), "answer": (a.body or a.title)[:200],
                 "is_mentor": a.is_mentor, "created_at": iso_utc(a.created_at)} for a in rows]


def decisions(user_id: int, days: int = 7, limit: int = 4) -> list[dict]:
    """Мои обращения, по которым за неделю что-то решили."""
    h = anon.author_hash(user_id)
    out = []
    with get_sessionmaker()() as session:
        for p in session.scalars(select(Post).where(Post.author_hash == h, Post.status.in_(("approved", "rejected", "hidden")),
                                                    Post.moderated_at.is_not(None), Post.moderated_at >= _since(days))
                                 .order_by(Post.moderated_at.desc()).limit(limit)):
            data = json.loads(p.data_json or "{}")
            out.append({"id": p.id, "kind": p.kind, "kind_label": t(f"cm.kind.{p.kind}"), "title": p.title, "status": p.status,
                        "status_label": submission_status(p.kind, p.status, data), "reason": p.reject_reason, "when": iso_utc(p.moderated_at)})
        for a in session.scalars(select(Application).where(Application.user_id == user_id, Application.status != "pending",
                                                           Application.decided_at >= _since(days))):
            data = json.loads(a.data_json or "{}")
            out.append({"id": a.id, "kind": f"app_{a.kind}", "kind_label": t(f"mod.app.{a.kind}"), "title": "", "status": a.status,
                        "status_label": t(f"sub.status.{a.status}"), "reason": data.get("_reason", ""), "when": iso_utc(a.decided_at)})
    out.sort(key=lambda x: x["when"] or "", reverse=True)
    return out[:limit]


def submission_tone(kind: str, status: str, data: dict) -> str:
    """Цвет метки: ok (зелёный), alert (красный), warn (жёлтый), muted (серый). У слуха — по вердикту, а не по «опубликовано»."""
    if kind == "rumor" and status == "approved":
        return {"confirmed": "ok", "refuted": "alert", "partly": "warn"}.get(data.get("verdict", ""), "muted")
    return {"approved": "ok", "rejected": "alert", "hidden": "alert"}.get(status, "muted")


def submission_status(kind: str, status: str, data: dict) -> str:
    if kind == "rumor" and status == "approved":
        return t(f"truth.{data.get('verdict', 'unconfirmed')}")
    return t(f"sub.status.{status}")


def build(user_id: int, first_name: str = "") -> dict:
    course = campus.get_user_course(user_id)
    out: dict = {"name": first_name}
    if enabled("planner"):
        out["deadlines"] = planner.today_brief(user_id, 5)
        out["suggestions"] = len(planner.list_suggestions(user_id, course))
    if enabled("scam_radar"):
        out["radar"] = radar_fresh()
    if enabled("ask_senior"):
        out["answers"] = new_answers(user_id)
    out["decisions"] = decisions(user_id)
    if enabled("countdown"):
        out["countdown"] = campus.countdown(course)[:2]
    if roles.is_moderator(user_id):
        from backend.app.services.moderation import queue_count

        out["mod_queue"] = queue_count()
    out["date"] = date.today().isoformat()
    return out

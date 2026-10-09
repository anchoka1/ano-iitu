"""«Мои обращения»: всё, что человек отправлял на проверку, и что с этим стало.

Публикации сообщества (Радар, вопросы, отзывы, потеряшки...), слухи, отправленные модератору,
заявки (ментор, преподаватель, хаб). У каждой — статус, понятный автору:
«На проверке» / «Опубликовано» / «Отклонено: причина» / вердикт по слуху, и где её видят остальные.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.db.base import get_sessionmaker
from backend.app.db.models import iso_utc
from backend.app.db.models_campus import Application, Post
from backend.app.i18n import t
from backend.app.services import anon
from backend.app.services.today import submission_status, submission_tone

# Где опубликованное видят остальные (экран Mini App).
WHERE = {
    "radar": "#radar", "rumor": "#facts", "senior_q": "#post={id}", "senior_a": "#post={parent}", "team": "#post={id}",
    "lost": "#post={id}", "event": "#post={id}", "review": "#post={id}", "resource": "#post={id}", "vacancy": "#post={id}",
}


def list_mine(user_id: int) -> list[dict]:
    h = anon.author_hash(user_id)
    out: list[dict] = []
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(((Post.author_hash == h) | (Post.author_id == user_id)),
                                                  Post.kind.in_(tuple(WHERE))).order_by(Post.created_at.desc()).limit(100))
        for p in rows:
            data = json.loads(p.data_json or "{}")
            link = WHERE[p.kind].format(id=p.id, parent=p.parent_id or p.id) if p.status == "approved" else f"#post={p.id}"
            if p.kind == "rumor" and data.get("check_id"):
                link = f"#check={data['check_id']}"
            out.append({
                "key": f"post:{p.id}", "kind": p.kind, "kind_label": t(f"cm.kind.{p.kind}"), "title": p.title or p.body[:120],
                "status": p.status, "status_label": submission_status(p.kind, p.status, data), "reason": p.reject_reason,
                "tone": submission_tone(p.kind, p.status, data),
                "created_at": iso_utc(p.created_at), "decided_at": iso_utc(p.moderated_at), "link": link,
                "who_sees": t(f"sub.who.{p.kind}") if p.status == "approved" else t(f"sub.who_pending.{p.status}"),
            })
        for a in session.scalars(select(Application).where(Application.user_id == user_id).order_by(Application.created_at.desc())):
            data = json.loads(a.data_json or "{}")
            out.append({
                "key": f"app:{a.id}", "kind": f"app_{a.kind}", "kind_label": t(f"mod.app.{a.kind}"), "title": data.get("title") or data.get("about", "")[:120],
                "status": a.status, "status_label": t(f"sub.status.{a.status}"), "reason": data.get("_reason", ""),
                "tone": submission_tone("app", a.status, data),
                "created_at": iso_utc(a.created_at), "decided_at": iso_utc(a.decided_at), "link": "#hubs",
                "who_sees": t(f"sub.who_pending.{a.status}") if a.status != "approved" else t("sub.who.app"),
            })
    out.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return out


def counts(user_id: int) -> dict:
    items = list_mine(user_id)
    return {"total": len(items), "pending": sum(1 for x in items if x["status"] == "pending")}

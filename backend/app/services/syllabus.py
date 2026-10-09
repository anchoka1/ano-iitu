"""Силлабус: файл (PDF, DOCX, фото) или текст → структура → план, хаб предмета, напоминания.

  1. Файл проверяем по содержимому (core/docparse.py), текст извлекаем в памяти. Оригинал НЕ сохраняем.
  2. PDF-скан без текста → картинки страниц → модель со зрением. Не вышло — честно говорим и просим вставить текст.
  3. Разбор (ИИ или правила без ИИ): дедлайны и контрольные точки, веса оценок, правила пересдач и пропусков, ИИ.
  4. Сохраняем только разобранную карточку (таблица syllabi) и подбираем хаб предмета по названию.
  5. «Добавить в план» — дедлайны с датами становятся задачами; бот напомнит за день (включаем напоминания).
"""

from __future__ import annotations

import base64
import json

from sqlalchemy import delete, select

from backend.app.cards.schema import SyllabusCard
from backend.app.core import docparse
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import iso_utc
from backend.app.db.models_campus import Hub, HubMember
from backend.app.db.models_mod import Syllabus
from backend.app.i18n import t
from backend.app.llm.base import Attachment

ALLOWED = ("pdf", "docx", "jpeg", "png", "webp")
MAX_TEXT = 20000


class SyllabusError(Exception):
    pass


def doc_error_text(code: str) -> str:
    return t(f"file.err.{code}", mb=docparse.max_bytes() // (1024 * 1024))


async def text_from_file(raw: bytes) -> tuple[str, list[Attachment]]:
    """(текст, картинки для модели). Ошибки — SyllabusError с понятным текстом."""
    try:
        kind = docparse.check_upload(raw, ALLOWED)
        if kind in ("jpeg", "png", "webp"):
            return "", [Attachment("image", docparse.IMAGE_TYPES[kind], base64.b64encode(raw).decode())]
        text = docparse.extract(raw, kind)
    except docparse.DocError as exc:
        raise SyllabusError(doc_error_text(exc.code)) from exc
    if len(text) >= 80:
        return text[:MAX_TEXT], []
    if kind == "pdf":
        images = docparse.pdf_images(raw)
        if images:
            return "", [Attachment("image", mime, base64.b64encode(data).decode()) for mime, data in images]
        raise SyllabusError(t("file.err.scan"))
    raise SyllabusError(t("file.err.empty"))


def _guess_hub(session, title: str, user_id: int) -> int | None:
    from backend.app.services.hubs import find_hub

    if not title:
        return None
    found = find_hub(title)
    if not found:
        return None
    mine = {m.hub_id for m in session.scalars(select(HubMember).where(HubMember.user_id == user_id))}
    found.sort(key=lambda h: h["id"] not in mine)
    return found[0]["id"]


def save(user_id: int, card: SyllabusCard) -> dict:
    with get_sessionmaker()() as session:
        row = Syllabus(user_id=user_id, title=(card.course or t("cm.syllabus.title"))[:256], card_json=card.model_dump_json(),
                       hub_id=_guess_hub(session, card.course, user_id))
        session.add(row)
        session.commit()
        return _dict(session, row)


def _dict(session, row: Syllabus) -> dict:
    hub = session.get(Hub, row.hub_id) if row.hub_id else None
    card = SyllabusCard.model_validate(json.loads(row.card_json or "{}"))
    return {"id": row.id, "title": row.title, "card": card.model_dump(), "hub_id": row.hub_id, "hub_title": hub.title if hub else "",
            "added_to_plan": row.added_to_plan, "dated": sum(1 for d in card.deadlines if d.date), "created_at": iso_utc(row.created_at)}


def _own(session, user_id: int, syllabus_id: int) -> Syllabus:
    row = session.get(Syllabus, syllabus_id)
    if row is None or row.user_id != user_id:
        raise SyllabusError(t("sy.err.not_found"))
    return row


def get(user_id: int, syllabus_id: int) -> dict:
    with get_sessionmaker()() as session:
        return _dict(session, _own(session, user_id, syllabus_id))


def list_mine(user_id: int, hub_id: int | None = None) -> list[dict]:
    with get_sessionmaker()() as session:
        query = select(Syllabus).where(Syllabus.user_id == user_id)
        if hub_id is not None:
            query = query.where(Syllabus.hub_id == hub_id)
        return [_dict(session, r) for r in session.scalars(query.order_by(Syllabus.created_at.desc()).limit(50))]


def set_hub(user_id: int, syllabus_id: int, hub_id: int | None) -> dict:
    """Привязать к хабу предмета (и подписаться на хаб — там дедлайны курса и вопросы старшекурсникам)."""
    with get_sessionmaker()() as session:
        row = _own(session, user_id, syllabus_id)
        if hub_id is not None:
            hub = session.get(Hub, hub_id)
            if hub is None or hub.status != "active":
                raise SyllabusError(t("hub.err.not_found"))
            if not session.scalar(select(HubMember.id).where(HubMember.hub_id == hub_id, HubMember.user_id == user_id)):
                session.add(HubMember(hub_id=hub_id, user_id=user_id, role="member"))
        row.hub_id = hub_id
        session.commit()
        return _dict(session, row)


def to_plan(user_id: int, syllabus_id: int) -> dict:
    """Дедлайны с датами → задачи плана (повторно не дублируются). Включаем напоминание за день."""
    from backend.app.services import planner, prefs

    with get_sessionmaker()() as session:
        row = _own(session, user_id, syllabus_id)
        card = SyllabusCard.model_validate(json.loads(row.card_json or "{}"))
        hub = session.get(Hub, row.hub_id) if row.hub_id else None
        subject = (hub.title if hub else card.course or row.title)[:64]
    from backend.app.db.models_campus import Task

    with get_sessionmaker()() as session:
        existing = set(session.scalars(select(Task.source_ref).where(Task.user_id == user_id, Task.source == "syllabus")))
    added = 0
    for d in card.deadlines:
        if not d.date:
            continue
        ref = f"sy{syllabus_id}:{d.title[:40]}"
        if ref in existing:
            continue
        planner.create_task(user_id, d.title, due_date=d.date, subject=subject, source="syllabus", source_ref=ref,
                            priority=2 if d.weight else 1, note=t("cm.syllabus.weight_note", weight=d.weight) if d.weight else "")
        added += 1
    reminders_on = False
    if added and not prefs.is_set(user_id, "plan.remind"):
        # Человек ещё не трогал напоминания — включаем «за день», чтобы дедлайны из силлабуса не пропали.
        prefs.set_value(user_id, "plan.remind", "1")
        prefs.set_value(user_id, "plan.remind_offset", "1440")
        reminders_on = True
    with get_sessionmaker()() as session:
        row = _own(session, user_id, syllabus_id)
        row.added_to_plan += added
        session.commit()
    return {"added": added, "reminders_on": reminders_on or prefs.get_bool(user_id, "plan.remind")}


def delete_one(user_id: int, syllabus_id: int) -> None:
    with get_sessionmaker()() as session:
        session.delete(_own(session, user_id, syllabus_id))
        session.commit()


def delete_user(session, user_id: int) -> None:
    session.execute(delete(Syllabus).where(Syllabus.user_id == user_id))

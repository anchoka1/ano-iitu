"""API проверок: проверить, история, карточка, голоса, источники, картинка.

POST /api/check                     — проверить текст (и/или файл в base64)
GET  /api/checks                    — история
GET  /api/checks/{id}               — одна проверка
POST /api/checks/{id}/vote          — 👍/👎
POST /api/checks/{id}/sources       — добавить источник
POST /api/checks/{id}/simplify      — «Объяснить проще»
GET  /api/checks/{id}/image.png     — карточка картинкой
POST /api/checks/{id}/send-image    — прислать картинку в чат с ботом

Доступ: свою проверку видит автор; проверки из групп (chat_id < 0) видят
все, кто открыл приложение по ссылке из группы, — текст там и так был общим.
"""

from __future__ import annotations

import asyncio
import base64
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.cards.image import render_card_png
from backend.app.core.engine import CheckInput, EngineError, card_from_json, get_verdict_engine
from backend.app.core.fonts import FontNotFound
from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_db
from backend.app.db.models import Check, iso_utc
from backend.app.i18n import t
from backend.app.llm.base import Attachment
from backend.app.security.auth import CurrentUser, get_current_user

router = APIRouter(prefix="/api")


class CheckIn(BaseModel):
    mode: str
    text: str = Field(default="", max_length=8000)
    file_base64: str = ""
    file_type: str = ""


def file_to_input(file_base64: str, user_id: int) -> tuple[list[Attachment], str]:
    """Файл к проверке (чек, письмо, заявление): тип — по содержимому; PDF и DOCX → текст, фото → картинка для модели.
    Оригинал нигде не сохраняется: после проверки остаётся только карточка."""
    from backend.app.api.flows import decode_upload, require_consent
    from backend.app.core import docparse
    from backend.app.services.syllabus import doc_error_text

    require_consent(user_id)
    raw = decode_upload(file_base64)
    try:
        kind = docparse.check_upload(raw, ("pdf", "docx", "jpeg", "png", "webp"))
        if kind in ("jpeg", "png", "webp"):
            return [Attachment("image", docparse.IMAGE_TYPES[kind], base64.b64encode(raw).decode())], ""
        text = docparse.extract(raw, kind)
        if len(text) < 20 and kind == "pdf":
            images = docparse.pdf_images(raw, 2)
            if images:
                return [Attachment("image", m, base64.b64encode(d).decode()) for m, d in images], ""
            raise docparse.DocError("scan")
        if not text:
            raise docparse.DocError("empty")
        return [], text[:6000]
    except docparse.DocError as exc:
        raise HTTPException(status_code=422, detail=doc_error_text(exc.code)) from exc


def check_to_dict(check: Check, db: Session, viewer_id: int | None = None) -> dict:
    agree, disagree = repo.vote_counts(db, check.id)
    out = {
        "id": check.id, "mode": check.mode, "status": check.status, "input_text": check.input_text,
        "card": card_from_json(check.card_json).model_dump(), "created_at": iso_utc(check.created_at),
        "chat_id": check.chat_id, "provider": check.provider, "votes": {"agree": agree, "disagree": disagree},
        "user_sources": [{"url": s.url, "note": s.note} for s in repo.list_user_sources(db, check.id)],
        "mine": viewer_id == check.user_id,
    }
    if check.mode == "pravda":
        # Шкала «Подтверждено / Опровергнуто / Частично / Не подтверждено / На проверке», счётчик и проверка модератором.
        from backend.app.services.facts import truth_info

        out["truth"] = truth_info(db, check, viewer_id)
    return out


def _get_allowed(db: Session, check_id: int, user_id: int) -> Check:
    check = repo.get_check(db, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="Проверка не найдена.")
    if check.user_id != user_id and not (check.chat_id and check.chat_id < 0):
        raise HTTPException(status_code=403, detail=t("api.error.forbidden"))
    return check


@router.post("/check")
async def run_check(body: CheckIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    attachments: list[Attachment] = []
    text = body.text
    if body.file_base64:
        attachments, extra = file_to_input(body.file_base64, current.user.id)
        text = f"{text}\n{extra}".strip() if extra else text
    try:
        outcome = await get_verdict_engine().check(CheckInput(
            mode=body.mode, text=text, user_id=current.user.id, user_name=current.user.first_name, attachments=attachments,
        ))
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return check_to_dict(repo.get_check(db, outcome.check_id), db, current.user.id)


@router.get("/checks")
def history(limit: int = 30, offset: int = 0, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    items = repo.list_checks(db, current.user.id, min(limit, 100), max(offset, 0))
    return [
        {"id": c.id, "mode": c.mode, "status": c.status, "title": card_from_json(c.card_json).title,
         "preview": c.input_text[:120], "created_at": iso_utc(c.created_at), "in_group": bool(c.chat_id and c.chat_id < 0)}
        for c in items
    ]


@router.get("/checks/{check_id}")
def get_one(check_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return check_to_dict(_get_allowed(db, check_id, current.user.id), db, current.user.id)


class VoteIn(BaseModel):
    value: int = Field(ge=-1, le=1)


@router.post("/checks/{check_id}/vote")
def vote(check_id: int, body: VoteIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    _get_allowed(db, check_id, current.user.id)
    if body.value == 0:
        raise HTTPException(status_code=400, detail="Голос должен быть +1 или −1.")
    agree, disagree = repo.vote(db, check_id, current.user.id, body.value)
    return {"agree": agree, "disagree": disagree}


class SourceIn(BaseModel):
    url: str = Field(max_length=1024)
    note: str = Field(default="", max_length=512)


@router.post("/checks/{check_id}/sources")
def add_source(check_id: int, body: SourceIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    _get_allowed(db, check_id, current.user.id)
    if not re.match(r"^https?://[^\s]+\.[^\s]+", body.url.strip()):
        raise HTTPException(status_code=400, detail="Укажите ссылку, начинающуюся с http:// или https://")
    repo.add_user_source(db, check_id, current.user.id, body.url.strip(), body.note.strip())
    return {"ok": True}


@router.post("/checks/{check_id}/simplify")
async def simplify(check_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    check = _get_allowed(db, check_id, current.user.id)
    try:
        text = await get_verdict_engine().simplify(card_from_json(check.card_json), current.user.id)
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"text": text}


async def _png(check: Check) -> bytes:
    try:
        return await asyncio.to_thread(render_card_png, card_from_json(check.card_json))
    except FontNotFound as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/checks/{check_id}/image.png")
async def image(check_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    png = await _png(_get_allowed(db, check_id, current.user.id))
    return Response(png, media_type="image/png")


@router.post("/checks/{check_id}/send-image")
async def send_image(check_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    png = await _png(_get_allowed(db, check_id, current.user.id))
    notifier = get_notifier()
    if notifier is None or not await notifier.send_photo(current.user.id, f"verdikt_{check_id}.png", png, t("bot.image_caption")):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}

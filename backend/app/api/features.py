"""API остальных разделов Mini App: договорённости, тренажёр,
семья, мои чаты, источники.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.engine import EngineError
from backend.app.core.fonts import FontNotFound
from backend.app.core.notify import get_notifier
from backend.app.core.pdf import agreement_pdf
from backend.app.db import repo
from backend.app.db.base import get_db
from backend.app.i18n import t
from backend.app.rag.store import get_store
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import agreements as agreements_service
from backend.app.services import family as family_service
from backend.app.services import trainer as trainer_service

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ договорённости


class AgreementIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    group: bool = False  # договорённость с группой: подтверждает каждый участник


def _agreement_for(code: str, user_id: int, allow_pending_view: bool = True):
    agreement = agreements_service.get(code)
    if agreement is None:
        raise HTTPException(status_code=404, detail=t("agr.err.not_found"))
    participant = agreements_service.is_participant(agreement, user_id)
    # Неподтверждённую договорённость может открыть вторая сторона по ссылке — иначе её не подтвердить.
    # Групповую — любой участник группы, пока она открыта.
    open_for_answer = agreement.status == "pending" or (agreement.multi and agreement.status == "confirmed")
    if not participant and not (allow_pending_view and open_for_answer):
        raise HTTPException(status_code=403, detail=t("api.error.forbidden"))
    return agreement


@router.get("/agreements")
def agreements_list(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [agreements_service.to_dict(a) for a in repo.list_agreements(db, current.user.id)]


@router.post("/agreements")
async def agreements_create(body: AgreementIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        agreement = await agreements_service.create_agreement(body.text, current.user.id, current.user.first_name, multi=body.group)
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return agreements_service.to_dict(agreement)


@router.get("/agreements/{code}")
def agreements_get(code: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    return agreements_service.to_dict(_agreement_for(code, current.user.id))


class AgreementAction(BaseModel):
    action: str  # confirm | decline | done | cancel


@router.post("/agreements/{code}/action")
async def agreements_action(code: str, body: AgreementAction, current: CurrentUser = Depends(get_current_user)) -> dict:
    user = current.user
    try:
        if body.action in ("confirm", "decline"):
            agreement = agreements_service.respond(code, user.id, user.first_name, user.username, accept=body.action == "confirm")
            await agreements_service.notify_creator(agreement, accepted=body.action == "confirm")
        elif body.action in ("done", "cancel"):
            agreement = agreements_service.set_status(code, user.id, "done" if body.action == "done" else "cancelled")
        else:
            raise HTTPException(status_code=400, detail="Неизвестное действие.")
    except agreements_service.AgreementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return agreements_service.to_dict(agreement)


async def _agreement_pdf(code: str, user_id: int) -> bytes:
    agreement = _agreement_for(code, user_id, allow_pending_view=False)
    try:
        return await asyncio.to_thread(agreement_pdf, agreement)
    except FontNotFound as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/agreements/{code}/pdf")
async def agreements_pdf(code: str, current: CurrentUser = Depends(get_current_user)) -> Response:
    data = await _agreement_pdf(code, current.user.id)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="dogovorennost_{code}.pdf"'})


@router.post("/agreements/{code}/send-pdf")
async def agreements_send_pdf(code: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    data = await _agreement_pdf(code, current.user.id)
    notifier = get_notifier()
    if notifier is None or not await notifier.send_document(current.user.id, f"dogovorennost_{code}.pdf", data, t("agr.pdf_caption")):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}


# ------------------------------------------------------------------ тренажёр


class TrainerStart(BaseModel):
    scenario: str


class TrainerMessage(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.get("/trainer/scenarios")
def trainer_scenarios(current: CurrentUser = Depends(get_current_user)) -> dict:
    return {"scenarios": trainer_service.scenarios(), "history": trainer_service.history(current.user.id)}


@router.post("/trainer/start")
def trainer_start(body: TrainerStart, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return trainer_service.start(current.user.id, current.user.first_name, body.scenario)
    except trainer_service.TrainerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/trainer/{session_id}")
def trainer_get(session_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return trainer_service.get(current.user.id, session_id)
    except trainer_service.TrainerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/trainer/{session_id}/message")
async def trainer_message(session_id: int, body: TrainerMessage, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await trainer_service.reply(current.user.id, session_id, body.text)
    except trainer_service.TrainerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/trainer/{session_id}/finish")
async def trainer_finish(session_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await trainer_service.finish(current.user.id, session_id)
    except trainer_service.TrainerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------------------ семья


class FamilyJoin(BaseModel):
    token: str = Field(min_length=4, max_length=64)


class FamilySettings(BaseModel):
    notify: bool | None = None
    code_word_set: bool | None = None


@router.get("/family")
def family_get(current: CurrentUser = Depends(get_current_user)) -> dict:
    return family_service.info(current.user.id)


@router.post("/family")
def family_create(current: CurrentUser = Depends(get_current_user)) -> dict:
    return family_service.create(current.user.id, current.user.first_name)


@router.post("/family/join")
def family_join(body: FamilyJoin, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return family_service.join(current.user.id, current.user.first_name, body.token)
    except family_service.FamilyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/family/leave")
def family_leave(current: CurrentUser = Depends(get_current_user)) -> dict:
    return family_service.leave(current.user.id)


@router.patch("/family/settings")
def family_settings(body: FamilySettings, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return family_service.update_settings(current.user.id, body.notify, body.code_word_set)
    except family_service.FamilyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ------------------------------------------------------------------ мои чаты и источники


@router.get("/chats")
def chats(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    return [{"id": c.id, "title": c.title, "week": repo.chat_stats(db, c.id, since)} for c in repo.user_chats(db, current.user.id)]


@router.get("/sources")
def sources(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return {
        "base": [s.public() for s in get_store().sources],
        "reputation": [{"key": r.key, "total": r.total, "bad": r.bad, "good": r.good} for r in repo.list_reputation(db)],
    }

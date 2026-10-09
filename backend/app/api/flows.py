"""API сквозных сценариев: Главная «Сегодня», «Мои обращения», модерация, «Слухи и факты», силлабус, «Мой путь».

GET    /api/today                      — что важно сегодня именно мне
GET    /api/me/submissions             — мои обращения и их статусы
GET    /api/me/consent, POST /api/me/consent — согласие на обработку загружаемых документов
GET    /api/mod/queue                  — очередь модерации (только модераторам — проверяет сервер)
GET    /api/mod/item?key=post:12       — одна заявка
POST   /api/mod/decide                 — решение {key, action, reason, title, body, verdict, comment, source_url}
GET    /api/mod/log                    — журнал: кто, что, когда
GET    /api/mod/staff, POST /api/mod/staff {user_id, role}, DELETE /api/mod/staff/{id} — модераторы (админ)
POST   /api/checks/{id}/escalate       — «Отправить на проверку модератору» (только автор проверки)
GET    /api/facts                      — лента «Слухи и факты»
GET    /api/radar                      — лента Радара разводов (одобренное)
POST   /api/syllabus/upload {file_base64}        — разобрать файл (PDF, DOCX, фото); оригинал не сохраняется
POST   /api/syllabus {text}                       — разобрать вставленный текст
GET    /api/syllabi, GET/PATCH/DELETE /api/syllabi/{id}, POST /api/syllabi/{id}/to-plan
GET    /api/subjects, GET /api/subjects/{key}       — «Предметы»: мои предметы с прогрессом, страница предмета
GET    /api/path                       — «Мой путь»: квиз, чек-лист, вопросы старшекурсникам, письмо себе
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.cards.schema import SyllabusCard
from backend.app.core.engine import EngineError, get_verdict_engine
from backend.app.core.features import enabled, require
from backend.app.i18n import t
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import facts, moderation, path, prefs, roles, submissions, syllabus, today

router = APIRouter(prefix="/api")


def decode_upload(file_base64: str) -> bytes:
    from backend.app.core.docparse import max_bytes

    # base64 длиннее исходника на треть — грубая проверка размера ещё до декодирования.
    if len(file_base64) > max_bytes() * 4 // 3 + 16:
        raise HTTPException(status_code=413, detail=syllabus.doc_error_text("too_big"))
    try:
        return base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=t("file.err.unreadable")) from exc


def require_consent(user_id: int) -> None:
    """Без согласия файл не принимаем: 428 — Mini App покажет текст согласия и повторит запрос."""
    if not prefs.get(user_id, "consent.docs"):
        raise HTTPException(status_code=428, detail=t("consent.required"))


# ------------------------------------------------------------------ главная и мои обращения


@router.get("/today")
def get_today(current: CurrentUser = Depends(get_current_user)) -> dict:
    return today.build(current.user.id, current.user.first_name)


@router.get("/me/submissions")
def my_submissions(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return submissions.list_mine(current.user.id)


@router.get("/me/consent")
def consent_get(current: CurrentUser = Depends(get_current_user)) -> dict:
    return {"given": bool(prefs.get(current.user.id, "consent.docs")), "date": prefs.get(current.user.id, "consent.docs"), "text": t("consent.text")}


class ConsentIn(BaseModel):
    given: bool = True


@router.post("/me/consent")
def consent_set(body: ConsentIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    from backend.app.services.planner import local_today

    prefs.set_value(current.user.id, "consent.docs", local_today().isoformat() if body.given else "")
    return {"given": body.given}


# ------------------------------------------------------------------ модерация


def _mod(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except roles.RoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except moderation.ModerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/mod/queue")
def mod_queue(current: CurrentUser = Depends(get_current_user)) -> dict:
    items = _mod(moderation.queue, current.user.id)
    return {"items": items, "reasons": [{"code": c, "text": t(f"mod.reason.{c}")} for c in moderation.REJECT_REASONS],
            "verdicts": [{"code": v, "text": t(f"truth.{v}")} for v in moderation.VERDICTS], "is_admin": roles.is_admin(current.user.id)}


@router.get("/mod/item")
def mod_item(key: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _mod(moderation.get_item, current.user.id, key)


class DecideIn(BaseModel):
    key: str = Field(max_length=32)
    action: str = Field(max_length=16)
    reason: str = Field(default="", max_length=500)
    title: str | None = Field(default=None, max_length=256)
    body: str | None = Field(default=None, max_length=4000)
    verdict: str = Field(default="", max_length=16)
    comment: str = Field(default="", max_length=1000)
    source_url: str = Field(default="", max_length=500)


@router.post("/mod/decide")
async def mod_decide(body: DecideIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await moderation.decide(current.user.id, current.user.first_name, body.key, body.action, reason=body.reason,
                                       title=body.title, body=body.body, verdict=body.verdict, comment=body.comment, source_url=body.source_url)
    except roles.RoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except moderation.ModerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/mod/log")
def mod_log(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return _mod(moderation.journal, current.user.id)


@router.get("/mod/staff")
def staff_list(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    _mod(roles.require_moderator, current.user.id)
    return roles.list_staff()


class StaffIn(BaseModel):
    user_id: int
    role: str = "moderator"


@router.post("/mod/staff")
def staff_add(body: StaffIn, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    _mod(roles.grant, current.user.id, current.user.first_name, body.user_id, body.role)
    return roles.list_staff()


@router.delete("/mod/staff/{user_id}")
def staff_remove(user_id: int, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    _mod(roles.revoke, current.user.id, current.user.first_name, user_id)
    return roles.list_staff()


# ------------------------------------------------------------------ слухи и факты, радар


@router.post("/checks/{check_id}/escalate")
async def escalate(check_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await facts.escalate(check_id, current.user.id, current.user.first_name)
    except facts.FactsError as exc:
        raise HTTPException(status_code=403 if str(exc) == t("truth.err.not_yours") else 409, detail=str(exc)) from exc


@router.get("/facts")
def facts_feed(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("fact_feed")
    return facts.feed(current.user.id)


@router.get("/radar")
def radar_feed(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("scam_radar")
    from backend.app.services import community

    return {"items": community.list_posts("radar", current.user.id, limit=100), "subscribed": prefs.get_bool(current.user.id, "sub.radar")}


# ------------------------------------------------------------------ силлабус


class UploadIn(BaseModel):
    file_base64: str
    file_name: str = Field(default="", max_length=200)


class SyllabusTextIn(BaseModel):
    text: str = Field(min_length=20, max_length=20000)


async def _parse(user_id: int, text: str, attachments) -> dict:
    try:
        card: SyllabusCard = await get_verdict_engine().parse_syllabus(text, user_id, attachments)
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc) if "фото" not in str(exc).lower() else t("file.err.scan_model")) from exc
    if not card.deadlines and not card.grading and not card.retake_rules and not card.absence_rules:
        raise HTTPException(status_code=422, detail=t("sy.err.nothing"))
    saved = syllabus.save(user_id, card)
    # Дедлайны с датами сразу попадают в «План» (повторно не дублируются) — студенту не нужно ничего нажимать.
    if enabled("planner") and saved["dated"]:
        saved["plan"] = syllabus.to_plan(user_id, saved["id"])
        saved["added_to_plan"] = saved["plan"]["added"]
    return saved


@router.post("/syllabus/upload")
async def syllabus_upload(body: UploadIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("syllabus")
    require_consent(current.user.id)
    raw = decode_upload(body.file_base64)
    try:
        text, attachments = await syllabus.text_from_file(raw)
    except syllabus.SyllabusError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        del raw  # оригинал больше не нужен и нигде не сохранён
    return await _parse(current.user.id, text, attachments)


@router.post("/syllabus")
async def syllabus_text(body: SyllabusTextIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("syllabus")
    return await _parse(current.user.id, body.text, [])


def _sy(func, *args):
    try:
        return func(*args)
    except syllabus.SyllabusError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/syllabi")
def syllabi(hub_id: int | None = None, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("syllabus")
    return syllabus.list_mine(current.user.id, hub_id)


@router.get("/syllabi/{syllabus_id}")
def syllabus_get(syllabus_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _sy(syllabus.get, current.user.id, syllabus_id)


class HubIn(BaseModel):
    hub_id: int | None = None


@router.patch("/syllabi/{syllabus_id}")
def syllabus_hub(syllabus_id: int, body: HubIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _sy(syllabus.set_hub, current.user.id, syllabus_id, body.hub_id)


@router.post("/syllabi/{syllabus_id}/to-plan")
def syllabus_plan(syllabus_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("planner")
    return _sy(syllabus.to_plan, current.user.id, syllabus_id)


@router.delete("/syllabi/{syllabus_id}")
def syllabus_delete(syllabus_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    _sy(syllabus.delete_one, current.user.id, syllabus_id)
    return {"deleted": True}


# ------------------------------------------------------------------ мой путь


@router.get("/path")
def my_path(current: CurrentUser = Depends(get_current_user)) -> dict:
    return path.overview(current.user.id)


@router.get("/questions")
def questions(hub_id: int | None = None, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("ask_senior")
    from backend.app.services.campus import get_user_course

    return path.questions_for(current.user.id, get_user_course(current.user.id), hub_id, limit=50)


# ------------------------------------------------------------------ предметы


@router.get("/subjects")
def subjects_list(current: CurrentUser = Depends(get_current_user)) -> dict:
    """Мои предметы (из силлабусов и страниц дисциплин) с прогрессом по задачам и кольцо семестра."""
    from backend.app.services import subjects

    return subjects.list_subjects(current.user.id)


@router.get("/subjects/{key}")
def subject_page(key: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    """Всё о предмете: силлабус, как считается оценка, задачи и дедлайны; страница дисциплины — /api/hubs/{id}."""
    from backend.app.services import subjects

    try:
        return subjects.get_subject(current.user.id, key)
    except subjects.SubjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

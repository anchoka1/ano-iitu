"""API функций волн 1–3 для Mini App.

GET  /api/features                  — какие функции включены + роль (владелец, подтверждённый преподаватель)
GET  /api/home/extra                — Главная: обратный отсчёт, блок «Сегодня», слух недели
GET  /api/rumor, /api/rumor/image.png, POST /api/rumor/send
POST /api/gpa/final {admission, target}, POST /api/gpa/gpa {courses}, GET /api/gpa/rules
GET  /api/countdown
POST /api/polls (опрос после пары), GET /api/polls/mine, GET /api/polls/{id}, POST /api/polls/{id}/answer,
GET  /api/polls/{id}/results, POST /api/polls/{id}/close
GET  /api/pulse
GET  /api/checklist, POST /api/checklist/{key} {done}, POST /api/checklist/{key}/to-plan
GET  /api/morning
GET  /api/appeals, POST /api/appeals/draft
GET  /api/badges;  GET /api/quiz, POST /api/quiz/score;  GET/POST/DELETE /api/capsule
GET  /api/career, POST /api/career/resume
GET  /api/rating
GET/PATCH /api/subscriptions
GET  /api/agreements/{code}/report  — отчёт по договорённости (только автору)
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.app.core.engine import EngineError, get_verdict_engine
from backend.app.core.features import all_flags, enabled, is_owner, require
from backend.app.core.notify import get_notifier
from backend.app.i18n import t
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import campus, planner, polls, prefs
from backend.app.services.campus import get_user_course

router = APIRouter(prefix="/api")


def _c(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (campus.CampusError, polls.PollError, planner.PlannerError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/features")
def features(current: CurrentUser = Depends(get_current_user)) -> dict:
    from backend.app.services.hubs import is_verified_teacher

    return {"flags": all_flags(), "is_owner": is_owner(current.user.id), "teacher_verified": is_verified_teacher(current.user.id)}


@router.get("/home/extra")
def home_extra(current: CurrentUser = Depends(get_current_user)) -> dict:
    uid = current.user.id
    course = get_user_course(uid)
    out: dict = {}
    if enabled("countdown"):
        out["countdown"] = campus.countdown(course)
    if enabled("planner"):
        out["today"] = planner.today_brief(uid)
        out["suggestions"] = len(planner.list_suggestions(uid, course))
    if enabled("rumor_week"):
        rumor = campus.rumor_of_week()
        out["rumor"] = {k: rumor[k] for k in ("check_id", "text", "status", "verdict_label", "title")} if rumor else None
    if enabled("pulse"):
        p = polls.current_pulse(None, uid)
        out["pulse"] = {"id": p["id"], "question": p["question"], "answered": p["answered"]}
    return out


# ------------------------------------------------------------------ слух недели


@router.get("/rumor")
def rumor(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("rumor_week")
    return {"rumor": campus.rumor_of_week()}


async def _rumor_png() -> bytes:
    from backend.app.cards.image import render_card_png
    from backend.app.core.engine import card_from_json
    from backend.app.core.fonts import FontNotFound
    from backend.app.db import repo
    from backend.app.db.base import get_sessionmaker

    data = campus.rumor_of_week()
    if not data:
        raise HTTPException(status_code=404, detail=t("cm.rumor.none"))
    with get_sessionmaker()() as session:
        check = repo.get_check(session, data["check_id"])
    try:
        return await asyncio.to_thread(render_card_png, card_from_json(check.card_json))
    except FontNotFound as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rumor/image.png")
async def rumor_png(current: CurrentUser = Depends(get_current_user)) -> Response:
    require("rumor_week")
    return Response(await _rumor_png(), media_type="image/png")


@router.post("/rumor/send")
async def rumor_send(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("rumor_week")
    png = await _rumor_png()
    notifier = get_notifier()
    if notifier is None or not await notifier.send_photo(current.user.id, "sluh_nedeli.png", png, t("cm.rumor.share_hint")):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}


# ------------------------------------------------------------------ GPA


class FinalIn(BaseModel):
    admission: float = Field(default=0, ge=0, le=100)   # рейтинг допуска (РК1 + РК2) / 2 — или передайте r1 и r2
    target: float = Field(ge=0, le=100)
    r1: float | None = Field(default=None, ge=0, le=100)
    r2: float | None = Field(default=None, ge=0, le=100)


class GpaIn(BaseModel):
    courses: list[dict] = Field(default_factory=list, max_length=40)


@router.get("/gpa/rules")
def gpa_rules(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("gpa")
    return campus.grading()


@router.post("/gpa/final")
def gpa_final(body: FinalIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("gpa")
    return _c(campus.final_needed, body.admission, body.target, body.r1, body.r2)


@router.post("/gpa/gpa")
def gpa_value(body: GpaIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("gpa")
    return _c(campus.gpa, body.courses)


@router.get("/countdown")
def countdown(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("countdown")
    return campus.countdown(get_user_course(current.user.id))


# Силлабус (файл, текст, план, хаб) — в api/flows.py.


# ------------------------------------------------------------------ опросы


class PollIn(BaseModel):
    question: str = Field(min_length=3, max_length=512)
    options: list[str] = Field(default_factory=list, max_length=6)
    circle_id: int | None = None


class AnswerIn(BaseModel):
    option: int | None = None
    text: str = Field(default="", max_length=1000)


@router.post("/polls")
async def poll_create(body: PollIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    """«Опрос после пары». Если указана группа (circle) — бот разошлёт ссылку участникам."""
    require("class_poll")
    from backend.app.db import repo
    from backend.app.db.base import get_sessionmaker

    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, current.user.id, current.user.first_name)
        if user.role != "teacher":
            raise HTTPException(status_code=403, detail=t("cm.poll.teacher_only"))
    sent = 0
    if body.circle_id:
        from backend.app.services import circles

        try:
            info = circles.info(body.circle_id, current.user.id)
        except circles.CircleError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if info["my_role"] not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail=t("grp.err.admin_only"))
    poll = _c(polls.create, "class", body.question, current.user.id, body.options, None, body.circle_id)
    if body.circle_id and (notifier := get_notifier()):
        import html

        from backend.app.services import circles

        info = circles.info(body.circle_id, current.user.id)
        for m in info.get("members", []):
            if m["user_id"] != current.user.id and m["can_receive"]:
                sent += await notifier.send(m["user_id"], html.escape(t("cm.poll.dm", question=body.question)),
                                            [[(o, f"poll:a:{poll['id']}:{i}")] for i, o in enumerate(body.options)] or None)
        if not body.options:
            pass  # свободный ответ — по ссылке t.me/бот?start=poll_ID (её показывает Mini App)
    return {**poll, "sent": sent}


@router.get("/polls/mine")
def polls_mine(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("class_poll")
    return polls.my_polls(current.user.id)


@router.get("/polls/{poll_id}")
def poll_get(poll_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _c(polls.get, poll_id, current.user.id)


@router.post("/polls/{poll_id}/answer")
def poll_answer(poll_id: int, body: AnswerIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    from backend.app.university import crisis

    result = _c(polls.answer, poll_id, current.user.id, body.option, body.text)
    if result.get("crisis"):
        result["support"] = crisis.support_card(result["crisis"]).model_dump()
    return result


@router.get("/polls/{poll_id}/results")
def poll_results(poll_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _c(polls.results, poll_id, current.user.id)


@router.post("/polls/{poll_id}/close")
def poll_close(poll_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _c(polls.close, poll_id, current.user.id)


@router.get("/pulse")
def pulse(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("pulse")
    return polls.current_pulse(None, current.user.id)


# ------------------------------------------------------------------ чек-лист курса


class MarkIn(BaseModel):
    done: bool = True


def _course_or_default(user_id: int) -> str:
    return get_user_course(user_id) or "1"


@router.get("/checklist")
def checklist(course: str = "", current: CurrentUser = Depends(get_current_user)) -> dict:
    require("checklist")
    return _c(campus.checklist, current.user.id, course or _course_or_default(current.user.id))


@router.post("/checklist/{key}")
def checklist_mark(key: str, body: MarkIn, course: str = "", current: CurrentUser = Depends(get_current_user)) -> dict:
    require("checklist")
    return _c(campus.checklist_mark, current.user.id, course or _course_or_default(current.user.id), key, body.done)


@router.post("/checklist/{key}/to-plan")
def checklist_to_plan(key: str, course: str = "", current: CurrentUser = Depends(get_current_user)) -> dict:
    require("checklist")
    require("planner")
    data = _c(campus.checklist, current.user.id, course or _course_or_default(current.user.id))
    item = next((x for x in data["items"] if x["key"] == key), None)
    if item is None:
        raise HTTPException(status_code=404, detail=t("cm.checklist.no_item"))
    return _c(planner.create_task, current.user.id, item["title"], current.user.first_name, note=item["text"], source="checklist",
              source_ref=f"{data['course']}:{key}")


@router.get("/morning")
def morning(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("morning_digest")
    return {"html": planner.morning_text(current.user.id, get_user_course(current.user.id)),
            "subscribed": prefs.get_bool(current.user.id, "sub.morning")}


# ------------------------------------------------------------------ помощник обращений


class AppealIn(BaseModel):
    type: str
    fields: dict = Field(default_factory=dict)


@router.get("/appeals")
def appeals(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("appeal_helper")
    return {"types": campus.appeal_types(), "checked": campus.appeals().get("checked", "")}


@router.post("/appeals/draft")
def appeal_draft(body: AppealIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("appeal_helper")
    return _c(campus.appeal_draft, body.type, body.fields)


# ------------------------------------------------------------------ значки, квиз, капсула


@router.get("/badges")
def badges(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("badges")
    return campus.badges(current.user.id)


class ScoreIn(BaseModel):
    score: int = Field(ge=0, le=100)


@router.get("/quiz")
def quiz(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("quiz")
    return {**campus.quiz_questions(), "best": int(prefs.get(current.user.id, "quiz.best", "0") or 0)}


@router.post("/quiz/score")
def quiz_score(body: ScoreIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("quiz")
    return campus.quiz_score(current.user.id, body.score)


class CapsuleIn(BaseModel):
    text: str = Field(min_length=10, max_length=4000)


@router.get("/capsule")
def capsule_list(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("time_capsule")
    course = get_user_course(current.user.id)
    try:
        deliver = campus.capsule_date(course)
    except campus.CampusError:
        deliver = ""
    return {"items": campus.capsule_list(current.user.id), "deliver_on": deliver, "course": course}


@router.post("/capsule")
def capsule_create(body: CapsuleIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("time_capsule")
    return _c(campus.capsule_create, current.user.id, get_user_course(current.user.id), body.text)


@router.delete("/capsule/{capsule_id}")
def capsule_delete(capsule_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("time_capsule")
    _c(campus.capsule_delete, current.user.id, capsule_id)
    return {"deleted": True}


# ------------------------------------------------------------------ карьера, рейтинг, подписки, отчёт


class ResumeIn(BaseModel):
    text: str = Field(min_length=10, max_length=12000)


@router.get("/career")
def career(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("career")
    from backend.app.services import community

    return {**campus.career(), "vacancies": community.list_posts("vacancy", current.user.id)}


@router.post("/career/resume")
async def resume(body: ResumeIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("career")
    try:
        return (await get_verdict_engine().review_resume(body.text, current.user.id)).model_dump()
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/rating")
def rating(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    require("group_rating")
    return campus.group_rating()


class SubIn(BaseModel):
    key: str
    on: bool


@router.get("/subscriptions")
def subscriptions(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return prefs.subscriptions_view(current.user.id)


@router.patch("/subscriptions")
def subscription_set(body: SubIn, current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    if body.key not in prefs.SUBSCRIPTIONS:
        raise HTTPException(status_code=404, detail="Нет такой подписки.")
    prefs.set_value(current.user.id, body.key, "1" if body.on else "0")
    return prefs.subscriptions_view(current.user.id)


@router.get("/agreements/{code}/report")
def agreement_report(code: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("agreement_report")
    try:
        return campus.agreement_report(code, current.user.id)
    except campus.CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

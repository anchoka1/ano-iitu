"""API планера «Мой план» для Mini App.

GET    /api/plan?view=today|week|semester|board|matrix  — виды
POST   /api/plan/parse                    — разобрать текст «сдать лабу по Python в пятницу до 18:00»
POST   /api/plan/tasks                    — добавить задачу
GET    /api/plan/tasks/{id}               — задача с подзадачами
PATCH  /api/plan/tasks/{id}               — изменить (в т. ч. статус для доски, срочно/важно для матрицы)
DELETE /api/plan/tasks/{id}               — удалить
POST   /api/plan/tasks/{id}/move          — перенести {days} или {date}
POST   /api/plan/tasks/{id}/frog          — «Лягушка дня»
POST   /api/plan/tasks/{id}/steps/preview — «Разбей на шаги» (предпросмотр)
POST   /api/plan/tasks/{id}/steps         — добавить шаги подзадачами
POST   /api/plan/tasks/{id}/promise       — «Договор с собой» (+ ссылка свидетелю)
GET    /api/plan/suggestions              — задачи, которые пришли сами (с подтверждением)
POST   /api/plan/suggestions/{id}/accept|dismiss
GET    /api/plan/traffic                  — светофор следующей недели
GET    /api/plan/week-verdict(.png)       — вердикт недели (виден только мне); POST .../send — прислать в чат
GET/PATCH /api/plan/settings              — напоминания, за сколько, тихие часы
GET    /api/plan/export.ics, POST /api/plan/export/send
DELETE /api/plan                          — удалить все задачи и данные планера
GET/POST /api/boards, GET /api/boards/{id}, POST /api/boards/{id}/tasks  — командная доска
POST   /api/focus/start, /api/focus/{id}/finish, GET /api/focus/room     — «Помидор» и «Фокус-комната»
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.app.core.features import require
from backend.app.core.notify import get_notifier
from backend.app.i18n import t
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import planner, prefs
from backend.app.services.campus import get_user_course

router = APIRouter(prefix="/api")


def _plan(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    require("planner")
    return current


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except planner.PlannerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    note: str = Field(default="", max_length=4000)
    due_date: str = ""
    due_time: str = ""
    subject: str = Field(default="", max_length=64)
    priority: int = 1
    repeat: str = ""
    parent_id: int | None = None
    status: str = "todo"


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=4000)
    due_date: str | None = None
    due_time: str | None = None
    subject: str | None = Field(default=None, max_length=64)
    priority: int | None = None
    repeat: str | None = None
    status: str | None = None
    urgent: bool | str | None = None      # true | false | "auto"
    important: bool | str | None = None


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MoveIn(BaseModel):
    days: int | None = None
    date: str | None = None


class StepsIn(BaseModel):
    steps: list[dict] = Field(default_factory=list)


class PromiseIn(BaseModel):
    witness: bool = False


class SettingsIn(BaseModel):
    remind: bool | None = None
    remind_offset: int | None = Field(default=None, ge=5, le=4320)
    quiet: str | None = Field(default=None, pattern=r"^\d{1,2}-\d{1,2}$")


class BoardIn(BaseModel):
    title: str = ""
    agreement_code: str = ""
    circle_id: int | None = None


class BoardTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    assignee_id: int | None = None
    due_date: str = ""


class FocusIn(BaseModel):
    minutes: int = 25
    task_id: int | None = None


@router.get("/plan")
def plan_view(view: str = "today", current: CurrentUser = Depends(_plan)) -> dict:
    return _call(planner.view, current.user.id, view, None, get_user_course(current.user.id))


@router.post("/plan/parse")
def plan_parse(body: TextIn, current: CurrentUser = Depends(_plan)) -> dict:
    return planner.parse_task_text(body.text)


@router.post("/plan/tasks")
def task_create(body: TaskIn, current: CurrentUser = Depends(_plan)) -> dict:
    data = body.model_dump()
    title = data.pop("title")
    return _call(planner.create_task, current.user.id, title, current.user.first_name, **data)


@router.get("/plan/tasks/{task_id}")
def task_get(task_id: int, current: CurrentUser = Depends(_plan)) -> dict:
    try:
        return planner.get_task(current.user.id, task_id)
    except planner.PlannerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/plan/tasks/{task_id}")
async def task_patch(task_id: int, body: TaskPatch, current: CurrentUser = Depends(_plan)) -> dict:
    result = _call(planner.update_task, current.user.id, task_id, **body.model_dump(exclude_unset=True))
    await planner.flush_witness_queue()
    return result


@router.delete("/plan/tasks/{task_id}")
def task_delete(task_id: int, current: CurrentUser = Depends(_plan)) -> dict:
    _call(planner.delete_task, current.user.id, task_id)
    return {"deleted": True}


@router.post("/plan/tasks/{task_id}/move")
def task_move(task_id: int, body: MoveIn, current: CurrentUser = Depends(_plan)) -> dict:
    return _call(planner.move_task, current.user.id, task_id, body.days, body.date)


@router.post("/plan/tasks/{task_id}/frog")
def task_frog(task_id: int, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_frog")
    return _call(planner.set_frog, current.user.id, task_id)


@router.post("/plan/tasks/{task_id}/steps/preview")
async def steps_preview(task_id: int, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_steps")
    try:
        return {"steps": await planner.steps_preview(current.user.id, task_id)}
    except planner.PlannerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plan/tasks/{task_id}/steps")
def steps_add(task_id: int, body: StepsIn, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_steps")
    return _call(planner.add_steps, current.user.id, task_id, body.steps)


@router.post("/plan/tasks/{task_id}/promise")
def task_promise(task_id: int, body: PromiseIn, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_promise")
    return _call(planner.make_promise, current.user.id, task_id, body.witness)


@router.get("/plan/suggestions")
def suggestions(current: CurrentUser = Depends(_plan)) -> list[dict]:
    return planner.list_suggestions(current.user.id, get_user_course(current.user.id))


@router.post("/plan/suggestions/{suggestion_id}/{action}")
def suggestion_action(suggestion_id: int, action: str, current: CurrentUser = Depends(_plan)) -> dict:
    if action == "accept":
        return _call(planner.accept_suggestion, current.user.id, suggestion_id)
    if action == "dismiss":
        _call(planner.dismiss_suggestion, current.user.id, suggestion_id)
        return {"dismissed": True}
    raise HTTPException(status_code=400, detail="Неизвестное действие.")


@router.get("/plan/traffic")
def traffic(current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_traffic")
    return planner.traffic_light(current.user.id, get_user_course(current.user.id))


@router.get("/plan/week-verdict")
def week_verdict(current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_week_verdict")
    return planner.week_verdict(current.user.id)


async def _verdict_png(user_id: int) -> bytes:
    from backend.app.cards.image import render_card_png
    from backend.app.core.fonts import FontNotFound

    try:
        return await asyncio.to_thread(render_card_png, planner.week_verdict_card(user_id))
    except FontNotFound as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/plan/week-verdict.png")
async def week_verdict_png(current: CurrentUser = Depends(_plan)) -> Response:
    require("planner_week_verdict")
    return Response(await _verdict_png(current.user.id), media_type="image/png")


@router.post("/plan/week-verdict/send")
async def week_verdict_send(current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_week_verdict")
    png = await _verdict_png(current.user.id)
    notifier = get_notifier()
    if notifier is None or not await notifier.send_photo(current.user.id, "verdikt_nedeli.png", png, t("pl.verdict.share_caption")):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}


@router.get("/plan/settings")
def settings_get(current: CurrentUser = Depends(_plan)) -> dict:
    values = prefs.all_for(current.user.id)
    return {"remind": values["plan.remind"] == "1", "remind_offset": int(values["plan.remind_offset"] or 60), "quiet": values["plan.quiet"],
            "subscriptions": prefs.subscriptions_view(current.user.id)}


@router.patch("/plan/settings")
def settings_patch(body: SettingsIn, current: CurrentUser = Depends(_plan)) -> dict:
    uid = current.user.id
    if body.remind is not None:
        prefs.set_value(uid, "plan.remind", "1" if body.remind else "0")
    if body.remind_offset is not None:
        prefs.set_value(uid, "plan.remind_offset", str(body.remind_offset))
    if body.quiet is not None:
        start, end = (int(x) for x in body.quiet.split("-"))
        if not (0 <= start <= 23 and 0 <= end <= 23):
            raise HTTPException(status_code=422, detail="Часы — от 0 до 23.")
        prefs.set_value(uid, "plan.quiet", body.quiet)
    return settings_get(current)


@router.get("/plan/export.ics")
def export_ics(current: CurrentUser = Depends(_plan)) -> Response:
    return Response(planner.export_ics(current.user.id), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="moi_plan.ics"'})


@router.post("/plan/export/send")
async def export_send(current: CurrentUser = Depends(_plan)) -> dict:
    notifier = get_notifier()
    data = planner.export_ics(current.user.id).encode("utf-8")
    if notifier is None or not await notifier.send_document(current.user.id, "moi_plan.ics", data, t("pl.ics_caption")):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}


@router.delete("/plan")
def delete_all(current: CurrentUser = Depends(_plan)) -> dict:
    planner.delete_all(current.user.id)
    return {"deleted": True}


# ------------------------------------------------------------------ командная доска


@router.get("/boards")
def boards(current: CurrentUser = Depends(_plan)) -> list[dict]:
    require("planner_team_board")
    return planner.list_boards(current.user.id)


@router.post("/boards")
def board_create(body: BoardIn, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_team_board")
    return _call(planner.create_board, current.user.id, current.user.first_name, body.title, body.agreement_code, body.circle_id)


@router.get("/boards/{board_id}")
def board_get(board_id: int, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_team_board")
    try:
        return planner.board_view(current.user.id, board_id)
    except planner.PlannerError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/boards/{board_id}/tasks")
async def board_task(board_id: int, body: BoardTaskIn, current: CurrentUser = Depends(_plan)) -> dict:
    require("planner_team_board")
    try:
        return await planner.add_board_task(current.user.id, board_id, body.title, body.assignee_id, body.due_date)
    except planner.PlannerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ------------------------------------------------------------------ фокус


@router.post("/focus/start")
def focus_start(body: FocusIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("focus_room")
    return _call(planner.focus_start, current.user.id, body.minutes, body.task_id)


@router.post("/focus/{session_id}/finish")
def focus_finish(session_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    require("focus_room")
    return _call(planner.focus_finish, current.user.id, session_id)


@router.get("/focus/room")
def focus_room(current: CurrentUser = Depends(get_current_user)) -> dict:
    require("focus_room")
    return {**planner.focus_room(), "mine": planner.my_focus(current.user.id)}

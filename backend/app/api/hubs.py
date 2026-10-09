"""API учебных хабов для Mini App.

GET   /api/hubs                     — каталог: сквозные, по курсам, мои; «Преподавательская» — только подтверждённым
GET   /api/hubs/{id}                — страница хаба
POST  /api/hubs/{id}/join {on}      — вступить / выйти (дедлайны хаба будут приходить предложениями в план)
POST  /api/hubs/{id}/teach          — подтверждённый преподаватель закрепляется за хабом (кабинет дисциплины)
PATCH /api/hubs/{id}/settings       — настройки хаба (преподаватель хаба): пульс группы
GET   /api/hubs/{id}/summary        — сводка по предмету без имён (преподаватель хаба)
GET   /api/hubs/{id}/load?date=     — карта нагрузки группы на неделю (преподаватель хаба)
POST  /api/hubs/{id}/deadlines      — план курса: дедлайн → предложенная задача у участников
POST  /api/hubs/{id}/announce       — объявление с подтверждением прочтения
POST  /api/hubs/apply               — заявка: {kind: mentor|teacher|hub, data}
GET   /api/hubs/applications/mine   — мои заявки
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.features import require
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import community, hubs

router = APIRouter(prefix="/api/hubs")


def _h(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except hubs.HubError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except community.CommunityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _ah(coro):
    try:
        return await coro
    except hubs.HubError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except community.CommunityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _hubs(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    require("hubs")
    return current


class OnIn(BaseModel):
    on: bool = True


class SettingsIn(BaseModel):
    group_pulse: bool | None = None


class DeadlineIn(BaseModel):
    title: str = Field(min_length=3, max_length=256)
    due_date: str
    due_time: str = ""
    note: str = Field(default="", max_length=2000)


class AnnounceIn(BaseModel):
    title: str = Field(min_length=3, max_length=256)
    text: str = Field(default="", max_length=4000)


class ApplyIn(BaseModel):
    kind: str
    data: dict = Field(default_factory=dict)


@router.get("")
def catalog(current: CurrentUser = Depends(_hubs)) -> dict:
    return hubs.catalog(current.user.id)


@router.post("/apply")
async def apply(body: ApplyIn, current: CurrentUser = Depends(_hubs)) -> dict:
    require({"mentor": "mentors", "teacher": "teacher_cabinet", "hub": "hubs"}.get(body.kind, "hubs"))
    return await _ah(hubs.apply(body.kind, current.user.id, current.user.first_name, body.data))


@router.get("/applications/mine")
def my_applications(current: CurrentUser = Depends(_hubs)) -> list[dict]:
    return hubs.my_applications(current.user.id)


@router.get("/{hub_id}")
def hub_page(hub_id: int, current: CurrentUser = Depends(_hubs)) -> dict:
    try:
        return hubs.page(hub_id, current.user.id)
    except hubs.HubError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{hub_id}/join")
def hub_join(hub_id: int, body: OnIn, current: CurrentUser = Depends(_hubs)) -> dict:
    return _h(hubs.join, hub_id, current.user.id, current.user.first_name, body.on)


@router.post("/{hub_id}/teach")
def hub_teach(hub_id: int, current: CurrentUser = Depends(_hubs)) -> dict:
    require("teacher_cabinet")
    return _h(hubs.attach_teacher, hub_id, current.user.id)


@router.patch("/{hub_id}/settings")
def hub_settings(hub_id: int, body: SettingsIn, current: CurrentUser = Depends(_hubs)) -> dict:
    return _h(hubs.update_settings, hub_id, current.user.id, **body.model_dump())


@router.get("/{hub_id}/summary")
def hub_summary(hub_id: int, current: CurrentUser = Depends(_hubs)) -> dict:
    require("teacher_cabinet")
    return _h(hubs.subject_summary, hub_id, current.user.id)


@router.get("/{hub_id}/load")
def hub_load(hub_id: int, date: str, current: CurrentUser = Depends(_hubs)) -> dict:
    require("course_plan")
    return _h(hubs.load_map, hub_id, date, current.user.id)


@router.post("/{hub_id}/deadlines")
async def hub_deadline(hub_id: int, body: DeadlineIn, current: CurrentUser = Depends(_hubs)) -> dict:
    require("course_plan")
    return await _ah(hubs.publish_deadline(hub_id, current.user.id, current.user.first_name, body.title, body.due_date, body.due_time, body.note))


@router.post("/{hub_id}/announce")
async def hub_announce(hub_id: int, body: AnnounceIn, current: CurrentUser = Depends(_hubs)) -> dict:
    require("teacher_cabinet")
    return await _ah(hubs.announce(hub_id, current.user.id, current.user.first_name, body.title, body.text))

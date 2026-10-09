"""API «Групп и потоков» для Mini App.

GET    /api/circles                      — мои группы
POST   /api/circles                      — создать {kind, title, course}
GET    /api/circles/kinds                — типы групп
GET    /api/circles/invite/{token}       — посмотреть приглашение
POST   /api/circles/join                 — вступить {token}
GET    /api/circles/{id}                 — карточка (только участникам)
PATCH  /api/circles/{id}/settings        — мои уведомления {alerts, posts}
POST   /api/circles/{id}/leave           — выйти
DELETE /api/circles/{id}                 — удалить (владелец)
POST   /api/circles/{id}/posts           — объявление {text} (владелец/админ)
POST   /api/circles/{id}/agreements      — договорённость с группой {text} (владелец/админ)
POST   /api/circles/{id}/members/{uid}   — роль {role: admin|member} (владелец)
DELETE /api/circles/{id}/members/{uid}   — убрать участника (владелец/админ)
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.engine import EngineError
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.services import circles as service
from backend.app.university import reference

router = APIRouter(prefix="/api/circles")


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except service.CircleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class CircleIn(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    kind: str = "group"
    course: str = ""


class TokenIn(BaseModel):
    token: str = Field(min_length=4, max_length=64)


class SettingsIn(BaseModel):
    alerts: bool | None = None
    posts: bool | None = None


class TextIn(BaseModel):
    text: str = Field(min_length=3, max_length=service.MAX_POST_CHARS)


class RoleIn(BaseModel):
    role: str


@router.get("")
def my_circles(current: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return service.list_for_user(current.user.id)


@router.get("/kinds")
def kinds() -> list[dict]:
    return [{"id": k, "emoji": e, "title": title} for k, (e, title) in service.KINDS.items()]


@router.post("")
async def create(body: CircleIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    if body.kind not in service.KINDS or not reference.is_valid("course", body.course):
        raise HTTPException(status_code=422, detail="Неверный тип группы или курс.")
    return await asyncio.to_thread(_call, service.create, current.user.id, current.user.first_name, body.title, body.kind, body.course)


@router.get("/invite/{token}")
def invite(token: str, current: CurrentUser = Depends(get_current_user)) -> dict:
    data = service.by_token(token)
    if data is None:
        raise HTTPException(status_code=404, detail="Группа не найдена.")
    return {k: data[k] for k in ("id", "title", "kind", "kind_label", "members_count")}


@router.post("/join")
async def join(body: TokenIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(_call, service.join, current.user.id, current.user.first_name, body.token)


@router.get("/{circle_id}")
def get_circle(circle_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return service.info(circle_id, current.user.id)
    except service.CircleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.patch("/{circle_id}/settings")
def settings(circle_id: int, body: SettingsIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _call(service.update_settings, circle_id, current.user.id, body.alerts, body.posts)


@router.post("/{circle_id}/leave")
def leave(circle_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    service.leave(circle_id, current.user.id)
    return {"left": True}


@router.delete("/{circle_id}")
def delete(circle_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    _call(service.delete_circle, circle_id, current.user.id)
    return {"deleted": True}


@router.post("/{circle_id}/posts")
async def post(circle_id: int, body: TextIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await service.post_announcement(circle_id, current.user.id, current.user.first_name, body.text)
    except service.CircleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{circle_id}/agreements")
async def agreement(circle_id: int, body: TextIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    try:
        return await service.create_agreement(circle_id, current.user.id, current.user.first_name, body.text)
    except service.CircleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{circle_id}/members/{user_id}")
def set_role(circle_id: int, user_id: int, body: RoleIn, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _call(service.set_role, circle_id, current.user.id, user_id, body.role)


@router.delete("/{circle_id}/members/{user_id}")
def remove(circle_id: int, user_id: int, current: CurrentUser = Depends(get_current_user)) -> dict:
    return _call(service.remove_member, circle_id, current.user.id, user_id)

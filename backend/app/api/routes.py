"""Основные адреса API: здоровье, тексты, режимы, профиль, приватность.

GET    /api/health        — «живой ли сервер». Без авторизации.
GET    /api/i18n/{lang}   — тексты интерфейса.
GET    /api/modes         — режимы для главного экрана.
GET    /api/me            — кто я + статистика (иммунитет, серия, проверки).
PATCH  /api/me/settings   — крупный шрифт, подписки: новости МУИТ, календарь.
GET    /api/me/export     — все мои данные (JSON).
POST   /api/me/export/send — прислать файл с данными в чат с ботом.
DELETE /api/me            — удалить все мои данные.

APIRouter — «кусочек приложения» с набором адресов; в main.py его
подключают к FastAPI. Возвращённый словарь FastAPI сам превращает в JSON.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import __version__
from backend.app.core.config import get_settings
from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_db
from backend.app.db.init_db import check_db
from backend.app.db.models import Check
from backend.app.i18n import available_languages, strings_for, t
from backend.app.modes.registry import MODES
from backend.app.security.auth import CurrentUser, get_current_user
from backend.app.api.university import profile_dict

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict:
    settings = get_settings()
    # request.app.state — «общий ящик» приложения; туда main.py кладёт бота.
    runner = getattr(request.app.state, "bot_runner", None)
    return {
        "status": "ok",
        "version": __version__,
        "db": "ok" if check_db() else "fail",
        "bot": runner.status if runner else "disabled",
        "bot_username": runner.username if runner else "",
        "llm": "demo" if settings.use_mock_llm else "real",
        "dev_mode": settings.dev_mode,
    }


@router.get("/i18n/{lang}")
def i18n(lang: str) -> dict[str, str]:
    if lang not in available_languages():
        raise HTTPException(status_code=404, detail=f"Язык «{lang}» пока не поддерживается.")
    # Mini App нужны тексты интерфейса, режимов, карточек и договорённостей.
    prefixes = ("ui.", "ui2.", "mode.", "app.", "card.", "agr.", "adal.", "grp.")
    return {k: v for k, v in strings_for(lang).items() if k.startswith(prefixes)}


@router.get("/modes")
def modes(lang: str = "ru") -> list[dict]:
    return [
        {
            "key": m.key, "command": m.command, "emoji": m.emoji, "title": m.title(lang),
            "description": m.description(lang), "accepts_files": m.accepts_files, "is_check": m.is_check,
        }
        for m in MODES
    ]


@router.get("/me")
def me(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    user = repo.upsert_user(db, current.user.id, current.user.first_name, current.user.language_code)
    checks_count = db.scalar(select(func.count()).where(Check.user_id == user.telegram_id)) or 0
    runner = getattr(request.app.state, "bot_runner", None)
    return {
        "telegram_id": user.telegram_id,
        "first_name": user.first_name,
        "language_code": user.language_code,
        "is_dev": current.is_dev,
        "start_param": current.start_param,
        "large_font": user.large_font,
        "streak": user.streak,
        "immunity": repo.immunity_score(db, user.telegram_id),
        "checks_count": checks_count,
        "bot_started": user.bot_started,
        "bot_username": runner.username if runner else "",
        "llm": "demo" if get_settings().use_mock_llm else "real",
        **profile_dict(user),
        **_staff_info(user.telegram_id),
    }


def _staff_info(user_id: int) -> dict:
    from backend.app.services import roles

    moderator = roles.is_moderator(user_id)
    out = {"is_moderator": moderator, "is_admin": roles.is_admin(user_id), "staff_role": roles.role_of(user_id) if moderator else "",
           # Есть ли кому проверять заявки. Нет модераторов — Mini App честно предупреждает, что публикация задержится.
           "moderation_ready": bool(roles.moderator_ids())}
    if moderator:
        from backend.app.services.moderation import queue_count

        out["mod_queue"] = queue_count()
    return out


class SettingsIn(BaseModel):
    large_font: bool | None = None
    news_subscribed: bool | None = None
    calendar_reminders: bool | None = None


@router.patch("/me/settings")
def update_settings(body: SettingsIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    user = repo.upsert_user(db, current.user.id, current.user.first_name)
    if body.large_font is not None:
        user.large_font = body.large_font
    if body.news_subscribed is not None:
        user.news_subscribed = body.news_subscribed
    if body.calendar_reminders is not None:
        user.calendar_reminders = body.calendar_reminders
    db.commit()
    return {"large_font": user.large_font,
            "news_subscribed": user.news_subscribed, "calendar_reminders": user.calendar_reminders}


@router.get("/me/export")
def export(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return repo.export_user_data(db, current.user.id)


@router.post("/me/export/send")
async def export_send(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    notifier = get_notifier()
    data = json.dumps(repo.export_user_data(db, current.user.id), ensure_ascii=False, indent=2).encode("utf-8")
    if notifier is None or not await notifier.send_document(current.user.id, "verdikt_moi_dannye.json", data):
        raise HTTPException(status_code=409, detail=t("api.error.bot_needed"))
    return {"sent": True}


@router.delete("/me")
def delete_me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    repo.delete_user_data(db, current.user.id)
    return {"deleted": True}

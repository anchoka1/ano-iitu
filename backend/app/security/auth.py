"""Авторизация запросов к API.

Суть: каждый запрос Mini App несёт заголовок
    Authorization: tma <строка initData>
(«tma» = Telegram Mini App — договорённость, которую рекомендует Telegram).
Функция get_current_user проверяет подпись и возвращает пользователя.

Режим разработки (DEV_MODE): чтобы смотреть Mini App в обычном браузере,
где нет Telegram и подписи, сервер подставляет тестового пользователя.
Это разрешено ТОЛЬКО для запросов с этого же компьютера и только без
признаков проксирования (туннель Cloudflare/ngrok приходит на 127.0.0.1,
но добавляет заголовки X-Forwarded-For и т. п. — такие запросы считаем внешними).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from backend.app.core.config import get_settings
from backend.app.security.initdata import InitDataError, TelegramUser, validate_init_data

log = logging.getLogger(__name__)

# Адреса «этого компьютера». "testclient" — адрес, который подставляет
# тестовый клиент FastAPI; в реальной сети такого адреса не бывает.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

# Заголовки, которые добавляют прокси и туннели.
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "forwarded", "x-forwarded-host")

DEV_USER = TelegramUser(id=1, first_name="Разработчик", username="dev", language_code="ru")


@dataclass(frozen=True)
class CurrentUser:
    """Кто делает запрос и как это было установлено."""

    user: TelegramUser
    is_dev: bool = False  # True — тестовый пользователь режима разработки
    start_param: str = ""


def is_local_request(request: Request) -> bool:
    """Запрос пришёл с этого компьютера напрямую, а не через туннель."""
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        return False
    return not any(h in request.headers for h in _PROXY_HEADERS)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)


def get_current_user(request: Request) -> CurrentUser:
    """Зависимость FastAPI: проверяет, кто делает запрос, и не даёт одному человеку завалить сервер запросами.

    «Зависимость» (dependency) — функция, которую FastAPI вызывает сам перед
    обработчиком, если тот написан как `user = Depends(get_current_user)`.
    user_id берётся ТОЛЬКО из проверенной подписи Telegram — никогда из тела запроса.
    """
    current = _authenticate(request)
    from backend.app.core.guard import api_throttle

    if not api_throttle.allow(current.user.id, max(10, get_settings().api_requests_per_minute)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Слишком много запросов. Подождите минуту.")
    return current


def _authenticate(request: Request) -> CurrentUser:
    settings = get_settings()
    header = request.headers.get("authorization", "")
    init_data = header[4:].strip() if header.lower().startswith("tma ") else ""
    dev_allowed = settings.dev_mode and is_local_request(request)

    if init_data and settings.bot_enabled:
        # Есть подпись и есть чем её проверить — проверяем всегда, даже в DEV_MODE.
        try:
            data = validate_init_data(
                init_data, settings.telegram_bot_token, settings.initdata_max_age_seconds
            )
        except InitDataError as exc:
            log.warning("Отклонена авторизация Telegram: %s", exc)
            raise _unauthorized(str(exc)) from exc
        return CurrentUser(user=data.user, start_param=data.start_param)

    if dev_allowed:
        return CurrentUser(user=DEV_USER, is_dev=True)

    if init_data and not settings.bot_enabled:
        raise _unauthorized("Сервер не настроен: не задан TELEGRAM_BOT_TOKEN, вход через Telegram невозможен.")
    raise _unauthorized("Откройте приложение через Telegram — так мы узнаём, кто вы.")

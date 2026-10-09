"""Точка входа: собирает сервер, Mini App и бота в одно приложение.

Запуск:  python -m backend.app.main   (это делают run.bat / F5)

Что происходит при старте (функция lifespan):
  1. включаются логи с маскированием личных данных;
  2. создаются таблицы базы (если их ещё нет);
  3. в консоль выводится сводка: адрес, режимы, предупреждения;
  4. в фоне запускается бот (если задан токен).
При остановке (Ctrl+C) бот аккуратно отключается.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app import __version__
from backend.app.api.checks import router as checks_router
from backend.app.api.features import router as features_router
from backend.app.api.routes import router as api_router
from backend.app.api.university import router as university_router
from backend.app.api.circles import router as circles_router
from backend.app.api.campus import router as campus_router
from backend.app.api.community import router as community_router
from backend.app.api.hubs import router as hubs_router
from backend.app.api.planner import router as planner_router
from backend.app.api.flows import router as flows_router
from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.core.logging_setup import setup_logging
from backend.app.db.init_db import init_db
from backend.app.i18n import t
from backend.app.core.tunnel import Tunnel, TunnelError
from backend.app.llm.factory import get_llm_client
from backend.app.telegram.runner import BotRunner

log = logging.getLogger("verdikt")
MINIAPP_DIR = PROJECT_ROOT / "miniapp"


def _print_banner() -> None:
    s = get_settings()
    lines = [
        "",
        "=" * 64,
        f"  ANO IITU v{__version__} запущен",
        f"  Mini App:  http://localhost:{s.port}",
        f"  Проверка:  http://localhost:{s.port}/api/health",
        f"  Режим ИИ:  {'ДЕМО (по правилам, без ИИ; вставьте LLM_API_KEY в .env)' if s.use_mock_llm else 'реальный (' + s.llm_provider + ', ' + s.llm_model_name + ')'}",
    ]
    if s.dev_mode:
        lines.append("  ВНИМАНИЕ: DEV_MODE=true — подпись Telegram не проверяется для")
        lines.append("  запросов с этого компьютера. На сервере поставьте DEV_MODE=false.")
    if s.dev_mode and s.webapp_url:
        lines.append("  Задан WEBAPP_URL (туннель): запросы через туннель всё равно")
        lines.append("  проверяются по подписи, но для показа лучше DEV_MODE=false.")
    lines.append("  Остановить: Ctrl+C")
    lines.append("=" * 64)
    log.info("\n".join(lines))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Код до yield выполняется при старте сервера, после yield — при остановке."""
    settings = get_settings()
    setup_logging(settings.log_level)
    init_db()
    from backend.app.services.hubs import seed_hubs

    seed_hubs()  # стартовые учебные хабы из data/iitu/hubs.json (повторно не создаются)
    from backend.app.services.files import migrate_legacy_uploads, purge_expired

    migrate_legacy_uploads()  # старые открытые фото → зашифрованное хранилище
    purge_expired()
    _print_banner()

    tunnel = None
    if settings.tunnel.strip().lower() == "cloudflare":
        tunnel = Tunnel(settings.port)
        try:
            url = await tunnel.start()
            # Подставляем адрес в настройки «на лету» — бот и кнопки сразу его используют.
            settings.webapp_url = url
            log.info("HTTPS-туннель готов. Mini App в Telegram: %s", url)
        except TunnelError as exc:
            log.error("Туннель не запустился: %s Mini App будет доступен только на этом компьютере.", exc)
            tunnel = None

    # Проверка облачной модели при запуске: подходит ли ключ и есть ли такая модель.
    llm = get_llm_client()
    if hasattr(llm, "check_model"):
        log.info("ИИ (%s): %s", llm.name, await llm.check_model())

    runner = BotRunner(settings.telegram_bot_token)
    app.state.bot_runner = runner
    await runner.start()

    # Лента новостей МУИТ обновляется и без бота: она нужна Mini App.
    news_task = None
    if settings.news_enabled and settings.scheduler_enabled:
        from backend.app.university.news import run_news_loop

        news_task = asyncio.create_task(run_news_loop(), name="news")
    try:
        yield
    finally:
        if news_task:
            news_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await news_task
        await runner.stop()
        if tunnel:
            await tunnel.stop()
        log.info("Сервер остановлен.")


def create_app() -> FastAPI:
    """Создаёт приложение. Отдельная функция — чтобы тесты могли создать «чистое» приложение."""
    app = FastAPI(title="ANO IITU API", version=__version__, lifespan=lifespan)

    # --- Понятные ошибки на русском в едином формате {"error": "..."} ---
    # StarletteHTTPException — базовый класс: его бросают и FastAPI, и раздача файлов (404).
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if exc.status_code != 404 or exc.detail != "Not Found" else t("api.error.not_found")
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "Неверные данные в запросе.", "details": exc.errors()}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Необработанная ошибка: %s", exc)
        return JSONResponse({"error": t("api.error.internal")}, status_code=500)

    @app.middleware("http")
    async def no_stale_miniapp(request: Request, call_next):
        # Файлы Mini App браузер Telegram должен перепроверять (ETag), иначе после обновления
        # могут смешаться старые и новые JS-модули.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        else:
            # Ответы API с личными данными не кэшируем нигде по пути.
            response.headers.setdefault("Cache-Control", "no-store")
        # Браузер не угадывает тип файла по содержимому и не передаёт адрес страницы на чужие сайты.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    app.include_router(api_router)
    app.include_router(checks_router)
    app.include_router(features_router)
    app.include_router(university_router)
    app.include_router(circles_router)
    # Новые функции: планер, волны 1–3, учебные хабы (каждая выключается флагом FEATURE_*).
    app.include_router(planner_router)
    app.include_router(campus_router)
    app.include_router(community_router)
    app.include_router(hubs_router)
    app.include_router(flows_router)

    # Mini App — обычные файлы из папки miniapp/. html=True: по адресу "/"
    # отдаётся index.html. Подключаем ПОСЛЕДНИМ, чтобы /api/... обрабатывал API.
    app.mount("/", StaticFiles(directory=MINIAPP_DIR, html=True), name="miniapp")
    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    setup_logging(settings.log_level)  # чтобы сообщения uvicorn тоже шли через наши логи
    # log_config=None — не даём uvicorn перенастраивать логи по-своему
    # (у нас свои логи с маскированием).
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)

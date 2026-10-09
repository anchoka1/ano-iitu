"""Запуск бота в фоне рядом с сервером.

Суть: сервер (FastAPI) и бот работают в одном процессе. При старте
сервера мы запускаем фоновую задачу, которая опрашивает Telegram
(long polling — «спроси, есть ли новые сообщения, и жди ответа»).
Webhook и публичный адрес для этого не нужны.

После подключения к Telegram запускаются:
  - уведомления (Notifier) — чтобы ядро могло писать пользователям;
  - планировщик — «Проверка дня», индекс чата, напоминания.

Если токена нет или он неверный — бот не запускается, но сервер и
Mini App продолжают работать, а в консоли понятная подсказка.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage

from backend.app.core.config import get_settings
from backend.app.core.notify import set_notifier
from backend.app.core.scheduler import run_scheduler
from backend.app.telegram.handlers import build_router
from backend.app.telegram.notifier import BotNotifier
from backend.app.telegram.profile import apply_profile

log = logging.getLogger("verdikt.bot")

# Формат токена: цифры, двоеточие, 30+ символов (буквы, цифры, _ и -).
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")

NO_TOKEN_HINT = (
    "Telegram-бот НЕ запущен: не задан TELEGRAM_BOT_TOKEN.\n"
    "    Сервер и Mini App работают и без него (демо-режим).\n"
    "    Как получить токен: в Telegram откройте @BotFather -> /newbot -> \n"
    "    придумайте имя и username -> скопируйте токен в файл .env:\n"
    "    TELEGRAM_BOT_TOKEN=123456789:AAE...  и перезапустите сервер."
)


@lru_cache
def build_dispatcher() -> Dispatcher:
    """Диспетчер создаётся один раз: роутер aiogram можно подключить только к одному «родителю».

    MemoryStorage — состояния FSM («жду текст для /pravda») хранятся в памяти.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router())
    return dispatcher


class BotRunner:
    """Управляет жизненным циклом бота: старт, статус, остановка."""

    def __init__(self, token: str) -> None:
        self.token = token.strip()
        self.status = "disabled"  # disabled | starting | running | error
        self.username = ""
        self._bot: Bot | None = None
        self._task: asyncio.Task[None] | None = None
        self._scheduler: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self.token:
            log.warning(NO_TOKEN_HINT)
            return
        if not _TOKEN_RE.match(self.token):
            self.status = "error"
            log.error("TELEGRAM_BOT_TOKEN выглядит неправильно. Скопируйте токен из @BotFather целиком, без пробелов и кавычек.")
            return

        self.status = "starting"
        # DefaultBotProperties: по умолчанию все сообщения размечены HTML (<b>, <i>).
        self._bot = Bot(self.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        # create_task запускает корутину «в фоне» и сразу возвращает управление.
        self._task = asyncio.create_task(self._run(build_dispatcher()), name="telegram-bot")

    async def _run(self, dispatcher: Dispatcher) -> None:
        assert self._bot is not None
        try:
            me = await self._bot.get_me()  # первая проверка: токен верный?
            self.username = me.username or ""
            try:
                await apply_profile(self._bot)  # описание и меню команд из ru.json
            except TelegramAPIError as exc:
                # Ошибка профиля не должна мешать боту отвечать на сообщения.
                log.warning("Не удалось обновить профиль бота: %s", exc)

            notifier = BotNotifier(self._bot)
            set_notifier(notifier)
            if get_settings().scheduler_enabled:
                self._scheduler = asyncio.create_task(run_scheduler(notifier), name="scheduler")

            self.status = "running"
            log.info("Бот @%s подключён к Telegram и ждёт сообщений.", self.username)
            # allowed_updates — какие события просить у Telegram. resolve_used_update_types()
            # собирает их из наших обработчиков (сообщения, кнопки, реакции, inline, участники чата).
            await dispatcher.start_polling(
                self._bot, handle_signals=False, allowed_updates=dispatcher.resolve_used_update_types()
            )
        except TelegramUnauthorizedError:
            self.status = "error"
            log.error("Telegram отверг токен бота. Проверьте TELEGRAM_BOT_TOKEN в .env (возможно, токен перевыпущен в @BotFather).")
        except TelegramNetworkError:
            self.status = "error"
            log.error("Нет связи с Telegram. Проверьте интернет или VPN и перезапустите сервер.")
        except asyncio.CancelledError:
            raise  # нормальная остановка — пробрасываем дальше
        except Exception:
            self.status = "error"
            log.exception("Бот остановился из-за непредвиденной ошибки.")

    async def stop(self) -> None:
        set_notifier(None)
        for task in (self._scheduler, self._task):
            if task:
                task.cancel()
                # suppress — «проглотить» ожидаемое исключение отмены.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._bot:
            await self._bot.session.close()

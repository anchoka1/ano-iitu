"""Настройка логов (журнала событий в консоли).

Суть: все сообщения в консоль проходят через фильтр, который прячет
телефоны, номера карт и ИИН. Так личные данные не попадут в логи, даже
если кто-то случайно запишет туда текст пользователя.
"""

from __future__ import annotations

import logging
import sys

from backend.app.security.masking import mask_sensitive


class MaskingFilter(logging.Filter):
    """Фильтр логов: маскирует личные данные в каждом сообщении."""

    def filter(self, record: logging.LogRecord) -> bool:
        # getMessage() собирает итоговую строку из шаблона и аргументов.
        record.msg = mask_sensitive(record.getMessage())
        record.args = None  # аргументы уже подставлены в msg
        return True  # True = сообщение пропускаем дальше (уже без данных)


def setup_logging(level: str = "INFO") -> None:
    """Включает логирование в консоль с маскированием и UTF-8."""
    # На Windows консоль иногда не в UTF-8 — тогда кириллица превращается
    # в «кракозябры» или вызывает ошибку. reconfigure меняет кодировку вывода.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    )
    handler.addFilter(MaskingFilter())

    root = logging.getLogger()
    root.handlers.clear()  # убираем старые обработчики, чтобы не было дублей
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Библиотеки бывают «болтливыми» — оставляем им только предупреждения.
    for noisy in ("aiogram.event", "httpx", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

"""Уведомления из ядра — без зависимости от Telegram-библиотеки.

Проблема: движку и планировщику нужно отправлять сообщения (уведомить
семью, напомнить о сроке), но ядро не должно знать про aiogram.
Решение: ядро знает только «интерфейс» Notifier. Telegram-бот при запуске
регистрирует свою реализацию (set_notifier), а в тестах можно подставить
«фальшивую», которая просто запоминает вызовы.

Buttons — кнопки под сообщением: список рядов, ряд — список пар
(текст кнопки, callback_data).
"""

from __future__ import annotations

from typing import Protocol

Buttons = list[list[tuple[str, str]]]


class Notifier(Protocol):
    async def send(self, chat_id: int, html_text: str, buttons: Buttons | None = None, **kwargs) -> bool:
        """Отправить сообщение. True — доставлено. kwargs: thread_id — тема группы (необязательно)."""
        ...

    async def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> bool: ...

    async def send_photo(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> bool: ...


_notifier: Notifier | None = None


def set_notifier(notifier: Notifier | None) -> None:
    global _notifier  # global — меняем переменную модуля, а не создаём локальную
    _notifier = notifier


def get_notifier() -> Notifier | None:
    return _notifier

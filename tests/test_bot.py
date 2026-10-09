"""Обработчики бота с «поддельным» Telegram.

RecordingSession подменяет сетевую часть aiogram: вместо отправки запросов
в Telegram она их запоминает и возвращает правдоподобный ответ. Так мы
прогоняем настоящие обработчики (команды, кнопки) без интернета.
"""

from __future__ import annotations

import asyncio
import itertools
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendDocument, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.telegram.runner import build_dispatcher
from tests.conftest import TEST_TOKEN

BOT_USER = User(id=42, is_bot=True, first_name="Вердикт", username="verdikt_test_bot")
_ids = itertools.count(1000)


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, (SendMessage, EditMessageText, SendDocument)) or type(method).__name__.startswith("Send"):
            chat_id = getattr(method, "chat_id", None) or 1
            # .as_(bot) — «привязать» объект к боту, как делает настоящий aiogram с ответами Telegram.
            return Message(message_id=next(_ids), date=int(time.time()), chat=Chat(id=chat_id, type="private" if chat_id > 0 else "group"),
                           text=getattr(method, "text", None), from_user=BOT_USER).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover — не используется
        yield b""

    async def close(self) -> None:
        pass

    def texts(self) -> list[str]:
        return [m.text for m in self.calls if isinstance(m, (SendMessage, EditMessageText))]


class Harness:
    """Бот + диспетчер + удобные методы «прислать сообщение» и «нажать кнопку»."""

    def __init__(self) -> None:
        self.session = RecordingSession()
        self.bot = Bot(TEST_TOKEN, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = build_dispatcher()
        self.dp.storage.storage.clear()  # состояния FSM от прошлого теста не должны мешать
        self.update_id = itertools.count(1)

    def _user(self, user_id: int, name: str, username: str | None = None) -> dict:
        return {"id": user_id, "is_bot": False, "first_name": name, "username": username, "language_code": "ru"}

    def message(self, text: str, user_id: int = 10, name: str = "Айгерим", chat_id: int | None = None,
                reply_to: dict | None = None, username: str | None = None, chat_type: str | None = None) -> None:
        chat_id = chat_id or user_id
        msg = {"message_id": next(_ids), "date": int(time.time()), "text": text,
               "chat": {"id": chat_id, "type": chat_type or ("private" if chat_id > 0 else "group"), "title": "Семья" if chat_id < 0 else None},
               "from": self._user(user_id, name, username)}
        if reply_to:
            msg["reply_to_message"] = reply_to
        self._feed({"update_id": next(self.update_id), "message": msg})

    def click(self, data: str, user_id: int = 10, name: str = "Айгерим", chat_id: int | None = None, username: str | None = None) -> None:
        chat_id = chat_id or user_id
        cb = {"id": str(next(_ids)), "chat_instance": "ci", "data": data, "from": self._user(user_id, name, username),
              "message": {"message_id": next(_ids), "date": int(time.time()), "text": "x",
                          "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "group"}, "from": BOT_USER.model_dump()}}
        self._feed({"update_id": next(self.update_id), "callback_query": cb})

    def _feed(self, raw: dict) -> None:
        update = Update.model_validate(raw, context={"bot": self.bot})
        asyncio.run(self.dp.feed_update(self.bot, update))

    def last_text(self) -> str:
        return self.session.texts()[-1]

    def last_markup_data(self) -> list[str]:
        for call in reversed(self.session.calls):
            markup = getattr(call, "reply_markup", None)
            if markup is not None and getattr(markup, "inline_keyboard", None):
                return [b.callback_data or b.url or "" for row in markup.inline_keyboard for b in row]
        return []


def test_start_and_help():
    h = Harness()
    h.message("/start")
    assert "ANO IITU" in h.last_text()  # новый пользователь: онбординг (роль → курс → язык)
    with get_sessionmaker()() as session:
        assert repo.get_user(session, 10).bot_started
    h.message("/help")
    assert "/razvod" in h.last_text()


def test_razvod_with_text_returns_red_card_with_buttons():
    h = Harness()
    h.message("/razvod Ваша карта заблокирована, срочно назовите код из SMS")
    assert h.session.texts()[0] == "Проверяю…"
    card = h.last_text()
    assert "🔴" in card and "<u>" in card  # подсветка фраз
    data = h.last_markup_data()
    assert any(d.startswith("v:") for d in data) and any(d.startswith("simple:") for d in data)


def test_group_reply_checks_replied_message():
    h = Harness()
    suspicious = {"message_id": 5, "date": int(time.time()), "chat": {"id": -100, "type": "group", "title": "Семья"},
                  "from": {"id": 77, "is_bot": False, "first_name": "Кто-то"}, "text": "Мама, это мой новый номер, срочно нужны деньги, не звони"}
    h.message("/razvod@verdikt_test_bot", user_id=11, chat_id=-100, reply_to=suspicious)
    assert "🔴" in h.last_text()
    with get_sessionmaker()() as session:
        check = repo.list_checks(session, 11)[0]
        assert check.chat_id == -100 and "новый номер" in check.input_text
        assert session.get(__import__("backend.app.db.models", fromlist=["Chat"]).Chat, -100) is not None


def test_group_command_without_text_gives_hint():
    h = Harness()
    h.message("/pravda", user_id=11, chat_id=-100)
    assert "Ответь командой /pravda" in h.last_text()


def test_private_command_waits_for_text():
    h = Harness()
    h.message("/pravda")
    assert "Пришли слух" in h.last_text()
    h.message("С понедельника штраф за голосовые сообщения в общих чатах WhatsApp")
    assert "🔴" in h.last_text()


def test_forwarded_text_asks_mode_then_checks():
    h = Harness()
    h.message("Получите выплату 50 000 ₸: https://kaspi-bonus.kz/pay")
    assert "Что проверить" in h.last_text()
    pick = next(d for d in h.last_markup_data() if d.startswith("pick:razvod:"))
    h.click(pick)
    assert "🔴" in h.last_text()


def test_vote_callback_updates_counts():
    h = Harness()
    h.message("/razvod Срочно переведите деньги на безопасный счёт")
    vote = next(d for d in h.last_markup_data() if d.startswith("v:") and d.endswith(":1"))
    h.click(vote, user_id=12)
    answers = [c for c in h.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers and "Голос" in answers[-1].text
    check_id = int(vote.split(":")[1])
    with get_sessionmaker()() as session:
        assert repo.vote_counts(session, check_id) == (1, 0)


def test_agreement_flow_in_group():
    h = Harness()
    h.message("/dogovorilis Аскар отдаёт Марату 20 000 ₸ до 15 октября", user_id=21, name="Аскар", chat_id=-200)
    assert "Договорённость" in h.last_text()
    ok = next(d for d in h.last_markup_data() if d.startswith("agr:ok:"))
    h.click(ok, user_id=21, name="Аскар", chat_id=-200)  # автор не может подтвердить сам
    answers = [c for c in h.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert "вторая сторона" in answers[-1].text
    h.click(ok, user_id=22, name="Марат", chat_id=-200)
    assert "подтверждена" in h.last_text()


def test_delete_my_data():
    h = Harness()
    h.message("/razvod Назовите код из SMS")
    h.message("/delete")
    assert "Удалить все твои данные" in h.last_text()
    h.click("del:yes")
    assert "удалены" in h.last_text()
    with get_sessionmaker()() as session:
        assert repo.get_user(session, 10) is None and repo.list_checks(session, 10) == []


def test_family_invite_via_deep_link():
    from backend.app.services import family

    info = family.create(31, "Мама")
    h = Harness()
    h.message(f"/start fam_{info['invite_token']}", user_id=32, name="Дочь")
    assert "приглашают в семью" in h.last_text()
    h.click(f"fam:join:{info['invite_token']}", user_id=32, name="Дочь")
    assert len(family.info(31)["members"]) == 2


def test_surveillance_request_refused_in_bot():
    h = Harness()
    h.message("/pravda как узнать где находится человек по номеру телефона бывшей")
    assert "слежк" in h.last_text().lower()


def test_unknown_command_in_private():
    h = Harness()
    h.message("/abracadabra")
    assert "Не знаю такой команды" in h.last_text()

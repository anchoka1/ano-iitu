"""Новые функции в боте: темы групп, «Мой план», «Сохрани ответ», опрос после пары, модерация."""

from __future__ import annotations

import itertools
import time

from aiogram.methods import GetChatMember, SendMessage
from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from tests.test_bot import BOT_USER, Harness, RecordingSession

_ids = itertools.count(50000)


class AdminSession(RecordingSession):
    """Как RecordingSession, но умеет отвечать на getChatMember: пользователь 1000 — админ группы."""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="U")
            if method.user_id == 1000:
                # model_construct — без проверки полей: набор обязательных прав меняется от версии к версии Bot API.
                return ChatMemberAdministrator.model_construct(user=user, status="administrator")
            return ChatMemberMember(user=user)
        return await super().make_request(bot, method, timeout)


class TopicHarness(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.session = AdminSession()
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        from tests.conftest import TEST_TOKEN

        self.bot = Bot(TEST_TOKEN, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    def topic_message(self, text: str, user_id: int = 1000, chat_id: int = -1001, thread_id: int = 12, topic: str = "Math",
                      reply_to: dict | None = None) -> None:
        """Сообщение в теме форума: Telegram кладёт в reply_to_message служебное сообщение о создании темы."""
        created = {"message_id": thread_id, "date": int(time.time()), "chat": {"id": chat_id, "type": "supergroup", "title": "IITU 1 курс", "is_forum": True},
                   "from": {"id": 1, "is_bot": False, "first_name": "Админ"}, "forum_topic_created": {"name": topic, "icon_color": 7322096},
                   "message_thread_id": thread_id, "is_topic_message": True}
        msg = {"message_id": next(_ids), "date": int(time.time()), "text": text, "message_thread_id": thread_id, "is_topic_message": True,
               "chat": {"id": chat_id, "type": "supergroup", "title": "IITU 1 курс", "is_forum": True},
               "from": self._user(user_id, "Админ" if user_id == 1000 else "Студент"), "reply_to_message": reply_to or created}
        self._feed({"update_id": next(self.update_id), "message": msg})

    def sends(self) -> list[SendMessage]:
        return [c for c in self.session.calls if isinstance(c, SendMessage)]


def _owner(monkeypatch, uid: int = 1000):
    monkeypatch.setenv("OWNER_IDS", str(uid))
    from backend.app.core.config import get_settings

    get_settings.cache_clear()


def test_check_in_topic_answers_in_same_topic():
    h = TopicHarness()
    h.topic_message("/pravda С понедельника штраф за голосовые сообщения в общих чатах WhatsApp", user_id=11)
    sends = h.sends()
    assert sends and all(s.message_thread_id == 12 for s in sends)  # ответ — в ту же тему
    with get_sessionmaker()() as session:
        check = repo.list_checks(session, 11)[0]
        assert check.thread_id == 12 and check.chat_id == -1001
    from backend.app.services.hubs import topic_name

    assert topic_name(-1001, 12) == "Math"  # название темы запомнено как контекст


def test_hub_link_save_and_repeat_question(monkeypatch):
    _owner(monkeypatch)
    from backend.app.services import hubs

    hub = hubs.create_hub_direct(1000, "Математика", "1")
    h = TopicHarness()
    h.topic_message(f"/hub_link {hub['slug']}", user_id=11)
    assert "администраторы" in h.last_text()  # не админ чата
    h.topic_message(f"/hub_link {hub['slug']}", user_id=1000)
    assert "привязана" in h.last_text()
    answer = {"message_id": 777, "date": int(time.time()), "text": "FX пересдаётся только финальный экзамен в период сессии",
              "chat": {"id": -1001, "type": "supergroup", "title": "IITU 1 курс", "is_forum": True}, "message_thread_id": 12, "is_topic_message": True,
              "from": {"id": 22, "is_bot": False, "first_name": "Старшекурсник"}}
    h.topic_message("/save Как пересдать FX по математике?", user_id=11, reply_to=answer)
    data = next(d for d in h.last_markup_data() if d.startswith("faq:ok:"))
    h.click(data, user_id=11, chat_id=-1001)  # не ментор и не админ — нельзя
    assert hubs.find_similar(hub["id"], "как пересдать FX по математике") is None
    h.click(data, user_id=1000, chat_id=-1001)  # админ группы подтверждает
    assert hubs.find_similar(hub["id"], "как пересдать FX по математике")
    h.topic_message("/sprosi как пересдать FX по математике?", user_id=13)
    assert "уже разбирали" in h.last_text() and "/777" in h.last_text()
    assert any(d.startswith("pick:vopros:") for d in h.last_markup_data())


def test_task_from_group_goes_to_private_only():
    h = Harness()
    h.message("/start", user_id=31, name="Айгерим")
    target = {"message_id": 5, "date": int(time.time()), "chat": {"id": -100, "type": "group", "title": "Группа"},
              "from": {"id": 77, "is_bot": False, "first_name": "Староста"}, "text": "Сдать реферат по истории до 20 октября"}
    before = len(h.session.calls)
    h.message("/task", user_id=31, chat_id=-100, reply_to=target)
    new = [c for c in h.session.calls[before:] if isinstance(c, SendMessage)]
    assert new and all(c.chat_id == 31 for c in new)  # в общий чат ничего не пишем
    add = next(d for d in h.last_markup_data() if d.startswith("pl:add:"))
    h.click(add, user_id=31)
    from backend.app.services import planner

    tasks = planner.view(31, "board")["columns"]["todo"]
    assert tasks and tasks[0]["due_date"].endswith("-10-20")


def test_forwarded_message_can_become_task_and_plan_command():
    h = Harness()
    h.message("Сдать лабу по Python в пятницу до 18:00")
    make = next(d for d in h.last_markup_data() if d.startswith("mktask:"))
    h.click(make)
    assert "Новая задача" in h.last_text() and "18:00" in h.last_text()
    h.click(next(d for d in h.last_markup_data() if d.startswith("pl:add:")))
    h.message("/plan")
    assert "Мой план" in h.last_text()
    assert any(d.startswith("pl:remind:") for d in h.last_markup_data())


def test_official_question_and_gpa():
    h = Harness()
    h.message("Ты официальный бот МУИТ?")
    assert "бот для студентов и преподавателей МУИТ" in h.last_text()
    h.message("/gpa 70 75")
    assert "82.5" in h.last_text()


def test_class_poll_flow(monkeypatch):
    from backend.app.services import polls

    h = Harness()
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, 50, "Преподаватель", bot_started=True)
        repo.set_profile(session, user, role="teacher")
    h.message("/opros Что было непонятно?", user_id=11)
    assert "преподавателя" in h.last_text()  # студенту — подсказка
    h.message("/opros Что было непонятно?", user_id=50, chat_id=-100)
    poll = polls.my_polls(50)[0]
    h.message(f"/start poll_{poll['id']}", user_id=60, name="Студент")
    assert "Анонимн" in h.last_text() or "анонимн" in h.last_text()
    h.message("Не понял рекурсию", user_id=60)
    assert "анонимно" in h.last_text()
    assert polls.results(poll["id"], 50)["answers"] == 1


def test_owner_moderation_commands(monkeypatch):
    _owner(monkeypatch)
    from backend.app.services import community

    with get_sessionmaker()() as session:
        repo.upsert_user(session, 1000, "Владелец", bot_started=True)
    post = community.create_post("lost", 10, "Айгерим", "Потерял(а): синий шарф", "Аудитория 405")
    h = Harness()
    h.message("/mod", user_id=11)
    assert "модератор" in h.last_text()
    h.message("/mod", user_id=1000)
    assert "синий шарф" in h.last_text()
    h.click(f"mod:ok:{post['id']}", user_id=1000)
    assert community.get_post(post["id"], 12)["status"] == "approved"


def test_witness_deep_link():
    from backend.app.services import planner

    with get_sessionmaker()() as session:
        repo.upsert_user(session, 10, "Айгерим", bot_started=True)
    t = planner.create_task(10, "Пробежка 3 км", due_date="2026-10-20")
    token = planner.make_promise(10, t["id"], witness=True)["witness_token"]
    h = Harness()
    h.message(f"/start wit_{token}", user_id=30, name="Друг")
    assert "свидетелем" in h.last_text()
    h.click(f"wit:ok:{token}", user_id=30, name="Друг")
    assert planner.get_task(10, t["id"])["witness_state"] == "accepted"
    assert BOT_USER.id == 42

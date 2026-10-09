"""Таблицы новых функций (миграция 003): планер, анонимные опросы, контент сообщества, учебные хабы.

Принципы, которые видны прямо в схеме:
  - личные задачи (tasks) принадлежат одному владельцу и нигде не агрегируются;
  - в анонимных ответах (poll_answers, post_votes, reports) нет id пользователя —
    только хеш (services/anon.py), чтобы не дать проголосовать дважды;
  - анонимные вопросы и отзывы хранят только author_hash: связь «человек — текст» не сохраняется;
  - переписку групп бот не хранит: в FAQ хаба попадает только то, что участник явно отметил.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models import utcnow

# ------------------------------------------------------------------ планер


class Task(Base):
    """Задача «Моего плана». Видит только владелец (кроме задач командной доски — их видят участники доски)."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[str] = mapped_column(String(10), default="", index=True)   # ГГГГ-ММ-ДД или ""
    due_time: Mapped[str] = mapped_column(String(5), default="")                # ЧЧ:ММ или ""
    subject: Mapped[str] = mapped_column(String(64), default="")
    priority: Mapped[int] = mapped_column(Integer, default=1)                   # 0 низкий, 1 обычный, 2 высокий
    repeat: Mapped[str] = mapped_column(String(8), default="")                  # "" | daily | weekly
    status: Mapped[str] = mapped_column(String(8), default="todo")              # todo | doing | done
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")           # manual | agreement | syllabus | calendar | hub | event | consult | checklist | chat
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    urgent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)         # None — бот решает сам (матрица)
    important: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    frog_date: Mapped[str] = mapped_column(String(10), default="")
    promise: Mapped[bool] = mapped_column(Boolean, default=False)               # «Договор с собой»
    witness_token: Mapped[str] = mapped_column(String(16), default="", index=True)
    witness_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    witness_name: Mapped[str] = mapped_column(String(128), default="")
    witness_state: Mapped[str] = mapped_column(String(10), default="")          # "" | invited | accepted | declined
    witness_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    board_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    assignee_name: Mapped[str] = mapped_column(String(128), default="")
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    moved_count: Mapped[int] = mapped_column(Integer, default=0)
    focus_minutes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSuggestion(Base):
    """Задача, которую бот ПРЕДЛАГАЕТ (из договорённости, силлабуса, календаря, хаба...). В план — только после «Принять»."""

    __tablename__ = "task_suggestions"
    __table_args__ = (UniqueConstraint("user_id", "source", "source_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[str] = mapped_column(String(10), default="")
    due_time: Mapped[str] = mapped_column(String(5), default="")
    subject: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="")
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")          # pending | accepted | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Pref(Base):
    """Личные настройки и подписки «ключ — значение» (все уведомления по умолчанию выключены)."""

    __tablename__ = "prefs"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), default="")


class Board(Base):
    """Командная доска группового проекта (из договорённости или группы)."""

    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    agreement_code: Mapped[str] = mapped_column(String(16), default="")
    circle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BoardMember(Base):
    __tablename__ = "board_members"
    __table_args__ = (UniqueConstraint("board_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")


class FocusSession(Base):
    """Фокус-сессия («Помидор»). В «Фокус-комнате» видно только число активных сессий — без имён."""

    __tablename__ = "focus_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=25)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ анонимные опросы


class Poll(Base):
    """Опрос: после пары (class), Пульс МУИТ (pulse), пульс группы по предмету (group_pulse)."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    hub_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    circle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    question: Mapped[str] = mapped_column(String(512), default="")
    options_json: Mapped[str] = mapped_column(Text, default="[]")    # [] — свободный ответ текстом
    week_key: Mapped[str] = mapped_column(String(10), default="", index=True)
    subject: Mapped[str] = mapped_column(String(64), default="")
    open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PollAnswer(Base):
    """Анонимный ответ: вместо id — хеш (только чтобы не ответить дважды). Время — только дата."""

    __tablename__ = "poll_answers"
    __table_args__ = (UniqueConstraint("poll_id", "voter_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(Integer, index=True)
    voter_hash: Mapped[str] = mapped_column(String(64))
    option: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    day: Mapped[str] = mapped_column(String(10), default="")


# ------------------------------------------------------------------ контент сообщества


class Post(Base):
    """Пользовательский контент одним форматом.

    kind: senior_q / senior_a (Спроси старшекурсника), faq (сохранённый ответ хаба), radar (развод),
    team (поиск команды), lost (потеряшки), event (афиша), review (отзыв о предмете), resource (полка),
    vacancy (карьера), ai_rules (правила ИИ), announce (объявление преподавателя с подтверждением),
    hub_deadline (план курса), lecture / lecture_q (вопросы перед лекцией).
    status: pending (ждёт модерации) | approved | rejected | hidden (скрыт по жалобе).
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    hub_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # только у неанонимных видов
    author_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    author_name: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    is_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mentor: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Миграция 004: причина отказа (её видит автор), кто решил, зашифрованный «обратный адрес» автора
    # анонимной заявки — чтобы сообщить ему решение. Модераторы его не видят.
    reject_reason: Mapped[str] = mapped_column(String(512), default="")
    moderated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notify_enc: Mapped[str] = mapped_column(String(256), default="")


class PostVote(Base):
    __tablename__ = "post_votes"
    __table_args__ = (UniqueConstraint("post_id", "voter_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    voter_hash: Mapped[str] = mapped_column(String(64))
    value: Mapped[int] = mapped_column(Integer, default=1)


class PostAck(Base):
    """«Прочитал(а)» — подтверждение правил ИИ или объявления. По смыслу не анонимно: автор видит, кто подтвердил."""

    __tablename__ = "post_acks"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventGoing(Base):
    """Кнопка «Иду» на событии афиши (для напоминания)."""

    __tablename__ = "event_going"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)


class Report(Base):
    """Жалоба на контент. Кто пожаловался — не храним (хеш только против повторов)."""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("post_id", "reporter_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    reporter_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(512), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Slot(Base):
    """Слот консультации преподавателя."""

    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    owner_name: Mapped[str] = mapped_column(String(128), default="")
    hub_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    start: Mapped[str] = mapped_column(String(16), index=True)        # ГГГГ-ММ-ДДTЧЧ:ММ (время Алматы)
    minutes: Mapped[int] = mapped_column(Integer, default=15)
    place: Mapped[str] = mapped_column(String(128), default="")
    taken_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    taken_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Capsule(Base):
    """Капсула времени: письмо себе, которое бот вернёт на 4 курсе."""

    __tablename__ = "capsules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    deliver_on: Mapped[str] = mapped_column(String(10), index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChecklistMark(Base):
    """Отмеченный пункт чек-листа «Мой курс»."""

    __tablename__ = "checklist_marks"
    __table_args__ = (UniqueConstraint("user_id", "course", "item_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    course: Mapped[str] = mapped_column(String(8))
    item_key: Mapped[str] = mapped_column(String(64))


# ------------------------------------------------------------------ учебные хабы


class Hub(Base):
    """Учебный хаб: дисциплина, сквозная тема или «Преподавательская»."""

    __tablename__ = "hubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(12), default="discipline")   # discipline | cross | teachers
    course: Mapped[str] = mapped_column(String(8), default="")            # 1..4 | master | "" (любой)
    program: Mapped[str] = mapped_column(String(16), default="")
    emoji: Mapped[str] = mapped_column(String(8), default="📚")
    description: Mapped[str] = mapped_column(Text, default="")
    chat_url: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(10), default="active")     # active | pending | rejected
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")        # например {"group_pulse": true}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HubMember(Base):
    """Участник хаба: member (подписан на дедлайны), mentor, teacher (подтверждённый), curator."""

    __tablename__ = "hub_members"
    __table_args__ = (UniqueConstraint("hub_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hub_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HubChat(Base):
    """Привязка группы Telegram или её темы к хабу. thread_id = 0 — весь чат."""

    __tablename__ = "hub_chats"
    __table_args__ = (UniqueConstraint("chat_id", "thread_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hub_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    digest_on: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HubTopic(Base):
    """Название темы (топика) группы — только название, чтобы понимать контекст («Math» → математика)."""

    __tablename__ = "hub_topics"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")


class Application(Base):
    """Заявка: стать ментором, подтвердить статус преподавателя, создать хаб. Решает владелец бота."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)            # mentor | teacher | hub
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


CAMPUS_TABLES = (
    Task, TaskSuggestion, Pref, Board, BoardMember, FocusSession, Poll, PollAnswer, Post, PostVote, PostAck,
    EventGoing, Report, Slot, Capsule, ChecklistMark, Hub, HubMember, HubChat, HubTopic, Application,
)

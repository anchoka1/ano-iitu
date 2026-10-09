"""Таблицы базы данных.

Принцип минимума данных: храним только то, без чего функция не работает.
- Пользователь: id в Telegram, имя для приветствия, настройки.
- Проверка: текст, который человек САМ прислал на проверку, и карточка.
  В группах бот не читает и не хранит остальные сообщения.
- Телефоны/карты/ИИН в логах маскируются; всё удаляется командой /delete.

Связи между таблицами храним просто числами (telegram id пользователя,
id чата) — так проще объяснять и удалять данные.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


def utcnow() -> datetime:
    """Текущее время в UTC (единое время сервера, без часовых поясов)."""
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """Время в формате ISO с явной пометкой UTC.

    SQLite не хранит часовой пояс: из базы время возвращается «без пояса».
    Если отдать его так, браузер решит, что это местное время, и сдвинет
    на 5 часов. Поэтому явно помечаем: это UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class User(Base):
    """Пользователь, который хотя бы раз открыл бота или Mini App."""

    __tablename__ = "users"

    # Mapped[int] — «колонка с целым числом»; mapped_column задаёт детали.
    id: Mapped[int] = mapped_column(primary_key=True)
    # BigInteger: id в Telegram бывают больше 2^31.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str] = mapped_column(String(8), default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # True — человек нажал /start в личке, значит, бот может ему писать первым.
    bot_started: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- настройки ---
    large_font: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- серия дней: сколько дней подряд человек тренируется ---
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[str] = mapped_column(String(10), default="")  # ГГГГ-ММ-ДД

    # --- семья ---
    family_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    family_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    code_word_set: Mapped[bool] = mapped_column(Boolean, default=False)  # само слово НЕ храним

    # --- университет (миграция 001) ---
    # Минимум данных: роль, курс, факультет/программа или кафедра, язык.
    # ИИН, номера документов и оценки НЕ храним и не спрашиваем.
    role: Mapped[str] = mapped_column(String(16), default="")         # student | teacher | "" (не выбрано)
    course: Mapped[str] = mapped_column(String(8), default="")        # 1..4 | master | phd
    faculty: Mapped[str] = mapped_column(String(16), default="")      # fctc | fbmu
    program: Mapped[str] = mapped_column(String(16), default="")      # код ОП, например 6B06101
    department: Mapped[str] = mapped_column(String(32), default="")   # кафедра (для преподавателя)
    ui_lang: Mapped[str] = mapped_column(String(8), default="ru")     # язык интерфейса
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    course_year: Mapped[int] = mapped_column(Integer, default=0)      # учебный год (начало), когда указан курс
    news_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)       # по умолчанию не спамим
    calendar_reminders: Mapped[bool] = mapped_column(Boolean, default=True)    # напоминания календаря
    # --- миграция 003: статус преподавателя подтверждён владельцем бота вручную ---
    # Только подтверждённый преподаватель получает кабинет дисциплины, метку «ответ преподавателя»
    # и доступ в «Преподавательскую». Самозаявленной роли для этого недостаточно.
    teacher_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Chat(Base):
    """Группа, в которую добавили бота (для индекса чата)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # id чата в Telegram
    title: Mapped[str] = mapped_column(String(256), default="")
    type: Mapped[str] = mapped_column(String(16), default="group")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Миграция 003: группа участвует в «Рейтинге групп» (включает участник командой /reiting).
    rating_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)


class Check(Base):
    """Одна проверка: что прислали и какую карточку выдали."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)  # telegram id
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    input_text: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(256), default="")  # откуда переслано (канал/сайт)
    status: Mapped[str] = mapped_column(String(8))
    card_json: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(16), default="mock")
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # сообщение-карточка в чате
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # тема группы (миграция 003)
    # Миграция 004: «отпечаток» утверждения (набор основ слов) — чтобы находить, что тот же слух уже проверяли.
    claim_key: Mapped[str] = mapped_column(String(40), default="", index=True)


class Vote(Base):
    """Голос «согласен / не согласен» по карточке (народная проверка)."""

    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("check_id", "user_id"),)  # один голос на человека

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    value: Mapped[int] = mapped_column(Integer)  # +1 согласен, -1 не согласен
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSource(Base):
    """Источник, который добавил участник к карточке (не проверен ботом)."""

    __tablename__ = "user_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    url: Mapped[str] = mapped_column(String(1024))
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceReputation(Base):
    """Память о каналах и сайтах: сколько раз их сообщения оказывались ложными."""

    __tablename__ = "source_reputation"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(256), unique=True)  # домен или @канал
    total: Mapped[int] = mapped_column(Integer, default=0)
    bad: Mapped[int] = mapped_column(Integer, default=0)  # 🔴
    good: Mapped[int] = mapped_column(Integer, default=0)  # 🟢
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Agreement(Base):
    """Договорённость (/dogovorilis)."""

    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # для ссылок
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    creator_name: Mapped[str] = mapped_column(String(128), default="")
    counterparty_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    counterparty_name: Mapped[str] = mapped_column(String(128), default="")
    counterparty_username: Mapped[str] = mapped_column(String(64), default="")  # если указали @username
    text: Mapped[str] = mapped_column(Text, default="")
    draft_json: Mapped[str] = mapped_column(Text, default="{}")
    # pending — ждёт подтверждения; confirmed; declined; done — выполнено; cancelled
    status: Mapped[str] = mapped_column(String(12), default="pending")
    deadline_iso: Mapped[str] = mapped_column(String(10), default="")
    reminded_before: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded_due: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True — договорённость с группой (преподаватель и студенты): подтверждает каждый участник сам.
    multi: Mapped[bool] = mapped_column(Boolean, default=False)
    # Договорённость внутри «Группы» (миграция 002): участники группы получают её и подтверждают сами.
    circle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Миграция 003: тема группы, время срока (ЧЧ:ММ) и напоминание «за час».
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deadline_time: Mapped[str] = mapped_column(String(5), default="")
    reminded_hour: Mapped[bool] = mapped_column(Boolean, default=False)


class Circle(Base):
    """«Группы и потоки» (миграция 002) — университетская версия «Семьи».

    Учебная группа, поток, группа с преподавателем, проектная команда или клуб.
    Права даёт только создание группы (владелец) и назначение владельцем (админ),
    а не роль «преподаватель» в профиле. Вступление — только добровольно, по ссылке.
    """

    __tablename__ = "circles"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="group")   # group | stream | teacher | team | club
    title: Mapped[str] = mapped_column(String(128), default="")
    invite_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # привязанный чат Telegram
    course: Mapped[str] = mapped_column(String(8), default="")       # для ближайших дат календаря
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CircleMember(Base):
    """Участник группы: роль и что он хочет получать."""

    __tablename__ = "circle_members"
    __table_args__ = (UniqueConstraint("circle_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(8), default="member")   # owner | admin | member
    alerts: Mapped[bool] = mapped_column(Boolean, default=True)      # предупреждения о разводах в группе
    posts: Mapped[bool] = mapped_column(Boolean, default=True)       # объявления и договорённости группы
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CirclePost(Base):
    """Объявление в группе (рассылается участникам в личку от имени бота)."""

    __tablename__ = "circle_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(Integer, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    author_name: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    sent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AgreementParticipant(Base):
    """Ответ участника групповой договорённости (миграция 001)."""

    __tablename__ = "agreement_participants"
    __table_args__ = (UniqueConstraint("agreement_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsItem(Base):
    """Новость МУИТ из официального источника (сайт или Telegram-канал) — кэш ленты (миграция 001).

    Храним только заголовок, дату, короткий пересказ и ссылку: полный текст — на сайте.
    """

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("source", "ext_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))           # site | telegram
    ext_id: Mapped[str] = mapped_column(String(256))          # адрес новости или номер поста
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512), default="")
    published: Mapped[str] = mapped_column(String(10), default="", index=True)  # ГГГГ-ММ-ДД
    lead: Mapped[str] = mapped_column(Text, default="")       # первые фразы источника — только для пересказа и поиска
    summary: Mapped[str] = mapped_column(Text, default="")    # 1–2 предложения своими словами
    summary_by: Mapped[str] = mapped_column(String(16), default="")  # llm | excerpt
    image: Mapped[str] = mapped_column(String(1024), default="")
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyAnswer(Base):
    """Ответ на «Проверку дня»."""

    __tablename__ = "daily_answers"
    __table_args__ = (UniqueConstraint("user_id", "date"),)  # один ответ в день

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    date: Mapped[str] = mapped_column(String(10))
    question_id: Mapped[str] = mapped_column(String(32))
    option: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool] = mapped_column(Boolean)


class TrainerSession(Base):
    """Сессия тренажёра «Не дай себя обмануть»."""

    __tablename__ = "trainer_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    scenario: Mapped[str] = mapped_column(String(32))
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    immunity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Family(Base):
    """Семья: группа близких, которые получают уведомления друг о друге."""

    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    invite_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Meta(Base):
    """Служебные отметки «ключ — значение» (например, дата последней рассылки)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), default="")


class LLMUsage(Base):
    """Учёт токенов: сколько «съела» каждая проверка (для лимитов и расходов)."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

"""Операции с данными («репозиторий»).

Зачем отдельный файл: и бот, и API, и планировщик работают с одними и
теми же данными, поэтому SQL-запросы живут здесь, в одном месте.
Остальной код не пишет запросы сам, а вызывает эти функции.

select(...) — построение запроса SELECT; session.scalar() — получить
одно значение/объект; session.scalars() — список объектов.
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Agreement,
    AgreementParticipant,
    Chat,
    Check,
    DailyAnswer,
    Family,
    LLMUsage,
    Meta,
    SourceReputation,
    TrainerSession,
    User,
    UserSource,
    Vote,
    iso_utc,
    utcnow,
)

# ------------------------------------------------------------------ пользователи


def upsert_user(
    session: Session, telegram_id: int, first_name: str, language_code: str = "ru", bot_started: bool | None = None
) -> User:
    """Находит пользователя по telegram_id или создаёт нового.

    upsert = update + insert: «обнови, а если нет — вставь».
    """
    user = session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id)
        session.add(user)
    if first_name:
        user.first_name = first_name[:128]
    user.language_code = (language_code or "ru")[:8]
    user.last_seen_at = utcnow()
    if bot_started is not None:
        user.bot_started = bot_started or user.bot_started
    session.commit()
    return user


def get_user(session: Session, telegram_id: int) -> User | None:
    return session.scalar(select(User).where(User.telegram_id == telegram_id))


PROFILE_FIELDS = ("role", "course", "faculty", "program", "department", "ui_lang")


def set_profile(session: Session, user: User, academic_year: int | None = None, **fields: str) -> User:
    """Сохраняет роль/курс/факультет/программу/кафедру/язык. Значения проверяет вызывающий код."""
    for key, value in fields.items():
        if key in PROFILE_FIELDS and value is not None:
            setattr(user, key, value)
    if user.role == "teacher":
        user.course = user.faculty = user.program = ""
    elif user.role == "student":
        user.department = ""
    if "course" in fields and academic_year:
        user.course_year = academic_year
    user.onboarded = bool(user.role)
    session.commit()
    return user


def students_for_courses(session: Session, courses: tuple[str, ...]) -> list[User]:
    """Студенты, которым можно напомнить о событии календаря (подписаны и нажимали «Старт»)."""
    return list(session.scalars(select(User).where(
        User.role == "student", User.course.in_(courses), User.calendar_reminders.is_(True), User.bot_started.is_(True))))


def students_to_promote(session: Session, academic_year: int) -> list[User]:
    """Студенты бакалавриата, у которых курс указан в прошлом учебном году (спросить про новый курс)."""
    return list(session.scalars(select(User).where(
        User.role == "student", User.course.in_(("1", "2", "3")), User.course_year > 0,
        User.course_year < academic_year, User.bot_started.is_(True))))


def touch_streak(user: User, today: date) -> None:
    """Обновляет серию дней: вчера был активен -> +1, сегодня уже был -> без изменений, иначе 1."""
    today_s = today.isoformat()
    if user.last_active_date == today_s:
        return
    yesterday = (today - timedelta(days=1)).isoformat()
    user.streak = (user.streak or 0) + 1 if user.last_active_date == yesterday else 1
    user.last_active_date = today_s


# ------------------------------------------------------------------ проверки


def save_check(session: Session, **fields) -> Check:
    check = Check(**fields)
    session.add(check)
    session.commit()
    return check


def get_check(session: Session, check_id: int) -> Check | None:
    return session.get(Check, check_id)


def find_check_by_message(session: Session, chat_id: int, message_id: int) -> Check | None:
    return session.scalar(select(Check).where(Check.chat_id == chat_id, Check.message_id == message_id))


def list_checks(session: Session, user_id: int, limit: int = 50, offset: int = 0) -> list[Check]:
    query = select(Check).where(Check.user_id == user_id).order_by(Check.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(query))


def vote(session: Session, check_id: int, user_id: int, value: int) -> tuple[int, int]:
    """Голос за карточку. Повторный голос того же человека заменяет прежний."""
    existing = session.scalar(select(Vote).where(Vote.check_id == check_id, Vote.user_id == user_id))
    if existing:
        existing.value = value
    else:
        session.add(Vote(check_id=check_id, user_id=user_id, value=value))
    session.commit()
    return vote_counts(session, check_id)


def vote_counts(session: Session, check_id: int) -> tuple[int, int]:
    agree = session.scalar(select(func.count()).where(Vote.check_id == check_id, Vote.value > 0)) or 0
    disagree = session.scalar(select(func.count()).where(Vote.check_id == check_id, Vote.value < 0)) or 0
    return agree, disagree


def add_user_source(session: Session, check_id: int, user_id: int, url: str, note: str = "") -> UserSource:
    item = UserSource(check_id=check_id, user_id=user_id, url=url[:1024], note=note[:512])
    session.add(item)
    session.commit()
    return item


def list_user_sources(session: Session, check_id: int) -> list[UserSource]:
    return list(session.scalars(select(UserSource).where(UserSource.check_id == check_id)))


# ------------------------------------------------------------------ репутация источников


def bad_origins(session: Session, keys: list[str], min_total: int = 2, ratio: float = 0.5) -> list[str]:
    """Какие из источников (каналы/домены) часто распространяли недостоверное."""
    if not keys:
        return []
    rows = session.scalars(select(SourceReputation).where(SourceReputation.key.in_(keys)))
    return [r.key for r in rows if r.total >= min_total and r.bad / r.total >= ratio]


def update_reputation(session: Session, keys: list[str], status: str) -> None:
    for key in dict.fromkeys(k for k in keys if k):
        row = session.scalar(select(SourceReputation).where(SourceReputation.key == key))
        if row is None:
            row = SourceReputation(key=key[:256], total=0, bad=0, good=0)
            session.add(row)
        row.total += 1
        row.bad += status == "red"
        row.good += status == "green"
        row.updated_at = utcnow()
    session.commit()


def list_reputation(session: Session, limit: int = 50) -> list[SourceReputation]:
    return list(session.scalars(select(SourceReputation).order_by(SourceReputation.bad.desc(), SourceReputation.total.desc()).limit(limit)))


# ------------------------------------------------------------------ токены


def add_usage(session: Session, user_id: int | None, provider: str, model: str, task: str, tokens_in: int, tokens_out: int) -> None:
    session.add(LLMUsage(user_id=user_id, provider=provider, model=model, task=task, input_tokens=tokens_in, output_tokens=tokens_out))
    session.commit()


def tokens_today(session: Session, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    total = session.scalar(
        select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
            LLMUsage.user_id == user_id, LLMUsage.created_at >= since
        )
    )
    return int(total or 0)


# ------------------------------------------------------------------ чаты


def upsert_chat(session: Session, chat_id: int, title: str, chat_type: str, active: bool = True) -> Chat:
    chat = session.get(Chat, chat_id)
    if chat is None:
        chat = Chat(id=chat_id)
        session.add(chat)
    chat.title = (title or "")[:256]
    chat.type = chat_type
    chat.active = active
    session.commit()
    return chat


def chat_stats(session: Session, chat_id: int, since: datetime) -> dict[str, int]:
    rows = session.execute(
        select(Check.status, func.count()).where(Check.chat_id == chat_id, Check.created_at >= since).group_by(Check.status)
    ).all()
    stats = {"total": 0, "red": 0, "yellow": 0, "green": 0, "unknown": 0}
    for status, count in rows:
        stats[status] = count
        stats["total"] += count
    stats["votes"] = session.scalar(
        select(func.count()).select_from(Vote).join(Check, Check.id == Vote.check_id).where(Check.chat_id == chat_id, Vote.created_at >= since)
    ) or 0
    return stats


def active_group_chats(session: Session) -> list[Chat]:
    return list(session.scalars(select(Chat).where(Chat.active.is_(True), Chat.type.in_(["group", "supergroup"]))))


def user_chats(session: Session, user_id: int) -> list[Chat]:
    """Группы, в которых пользователь делал проверки (так мы узнаём «его» чаты без списка участников)."""
    chat_ids = select(Check.chat_id).where(Check.user_id == user_id, Check.chat_id.is_not(None), Check.chat_id < 0).distinct()
    return list(session.scalars(select(Chat).where(Chat.id.in_(chat_ids))))


# ------------------------------------------------------------------ договорённости


def create_agreement(session: Session, **fields) -> Agreement:
    agreement = Agreement(code=secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")[:8], **fields)
    session.add(agreement)
    session.commit()
    return agreement


def get_agreement(session: Session, code: str) -> Agreement | None:
    return session.scalar(select(Agreement).where(Agreement.code == code))


def list_agreements(session: Session, user_id: int) -> list[Agreement]:
    joined = select(AgreementParticipant.agreement_id).where(AgreementParticipant.user_id == user_id)
    query = (
        select(Agreement)
        .where((Agreement.creator_id == user_id) | (Agreement.counterparty_id == user_id) | Agreement.id.in_(joined))
        .order_by(Agreement.created_at.desc())
    )
    return list(session.scalars(query))


def agreement_participants(session: Session, agreement_id: int) -> list[AgreementParticipant]:
    query = select(AgreementParticipant).where(AgreementParticipant.agreement_id == agreement_id).order_by(AgreementParticipant.created_at)
    return list(session.scalars(query))


def get_participant(session: Session, agreement_id: int, user_id: int) -> AgreementParticipant | None:
    return session.scalar(select(AgreementParticipant).where(
        AgreementParticipant.agreement_id == agreement_id, AgreementParticipant.user_id == user_id))


def due_agreements(session: Session) -> list[Agreement]:
    return list(session.scalars(select(Agreement).where(Agreement.status == "confirmed", Agreement.deadline_iso != "")))


# ------------------------------------------------------------------ проверка дня


# ------------------------------------------------------------------ тренажёр


def create_trainer_session(session: Session, user_id: int, scenario: str, messages: list[dict]) -> TrainerSession:
    item = TrainerSession(user_id=user_id, scenario=scenario, messages_json=json.dumps(messages, ensure_ascii=False))
    session.add(item)
    session.commit()
    return item


def immunity_score(session: Session, user_id: int) -> int | None:
    """Средний «иммунитет» по последним 5 завершённым тренировкам."""
    rows = list(
        session.scalars(
            select(TrainerSession.immunity)
            .where(TrainerSession.user_id == user_id, TrainerSession.finished.is_(True), TrainerSession.immunity.is_not(None))
            .order_by(TrainerSession.created_at.desc())
            .limit(5)
        )
    )
    return round(sum(rows) / len(rows)) if rows else None


# ------------------------------------------------------------------ семья


def create_family(session: Session, owner: User) -> Family:
    family = Family(owner_id=owner.telegram_id, invite_token=secrets.token_urlsafe(9))
    session.add(family)
    session.commit()
    owner.family_id = family.id
    session.commit()
    return family


def get_family(session: Session, family_id: int | None) -> Family | None:
    return session.get(Family, family_id) if family_id else None


def family_by_token(session: Session, token: str) -> Family | None:
    return session.scalar(select(Family).where(Family.invite_token == token))


def family_members(session: Session, family_id: int) -> list[User]:
    return list(session.scalars(select(User).where(User.family_id == family_id)))


def leave_family(session: Session, user: User) -> None:
    family = get_family(session, user.family_id)
    user.family_id = None
    session.commit()
    if family and not family_members(session, family.id):
        session.delete(family)
        session.commit()


# ------------------------------------------------------------------ служебное


def get_meta(session: Session, key: str) -> str:
    row = session.get(Meta, key)
    return row.value if row else ""


def set_meta(session: Session, key: str, value: str) -> None:
    row = session.get(Meta, key) or Meta(key=key)
    row.value = value
    session.add(row)
    session.commit()


# ------------------------------------------------------------------ приватность


def export_user_data(session: Session, telegram_id: int) -> dict:
    """Все данные пользователя одним словарём (для кнопки «Экспорт данных»)."""
    user = get_user(session, telegram_id)

    def rows(model, *conditions):
        return [
            {c.name: (iso_utc(v) if isinstance(v := getattr(r, c.name), datetime) else v) for c in model.__table__.columns}
            for r in session.scalars(select(model).where(*conditions))
        ]

    return {
        "exported_at": utcnow().isoformat(),
        "user": rows(User, User.telegram_id == telegram_id)[0] if user else None,
        "checks": rows(Check, Check.user_id == telegram_id),
        "votes": rows(Vote, Vote.user_id == telegram_id),
        "user_sources": rows(UserSource, UserSource.user_id == telegram_id),
        "agreements": rows(Agreement, (Agreement.creator_id == telegram_id) | (Agreement.counterparty_id == telegram_id)),
        "agreement_answers": rows(AgreementParticipant, AgreementParticipant.user_id == telegram_id),
        "groups": __import__("backend.app.services.circles", fromlist=["export_user"]).export_user(session, telegram_id),
        "daily_answers": rows(DailyAnswer, DailyAnswer.user_id == telegram_id),
        "trainer_sessions": rows(TrainerSession, TrainerSession.user_id == telegram_id),
        **_export_campus(session, telegram_id, rows),
        **_export_mod(session, telegram_id, rows),
    }


def _export_campus(session: Session, telegram_id: int, rows) -> dict:
    """Новые функции (миграция 003): планер, подписки, капсулы, чек-лист, неанонимные публикации, слоты, заявки.

    Анонимные ответы в опросах не выгружаются: связи «человек — ответ» в базе нет.
    """
    from backend.app.db import models_campus as mc
    from backend.app.services.anon import author_hash

    h = author_hash(telegram_id)
    return {
        "tasks": rows(mc.Task, mc.Task.user_id == telegram_id),
        "task_suggestions": rows(mc.TaskSuggestion, mc.TaskSuggestion.user_id == telegram_id),
        "prefs": rows(mc.Pref, mc.Pref.user_id == telegram_id),
        "boards": rows(mc.BoardMember, mc.BoardMember.user_id == telegram_id),
        "focus_sessions": rows(mc.FocusSession, mc.FocusSession.user_id == telegram_id),
        "capsules": rows(mc.Capsule, mc.Capsule.user_id == telegram_id),
        "checklist": rows(mc.ChecklistMark, mc.ChecklistMark.user_id == telegram_id),
        "posts": rows(mc.Post, (mc.Post.author_id == telegram_id) | (mc.Post.author_hash == h)),
        "read_confirmations": rows(mc.PostAck, mc.PostAck.user_id == telegram_id),
        "events_going": rows(mc.EventGoing, mc.EventGoing.user_id == telegram_id),
        "consultation_slots": rows(mc.Slot, (mc.Slot.owner_id == telegram_id) | (mc.Slot.taken_by == telegram_id)),
        "hubs": rows(mc.HubMember, mc.HubMember.user_id == telegram_id),
        "applications": rows(mc.Application, mc.Application.user_id == telegram_id),
    }


def _export_mod(session: Session, telegram_id: int, rows) -> dict:
    """Миграция 004: разобранные силлабусы, служебные данные загруженных файлов (без содержимого), роль модератора."""
    from backend.app.db import models_mod as mm

    files = [{k: v for k, v in r.items() if k != "storage_name"} for r in rows(mm.StoredFile, mm.StoredFile.owner_id == telegram_id)]
    return {"syllabi": rows(mm.Syllabus, mm.Syllabus.user_id == telegram_id), "files": files,
            "staff_role": rows(mm.Staff, mm.Staff.user_id == telegram_id)}


def _delete_campus(session: Session, telegram_id: int) -> None:
    """Данные новых функций: планер, подписки, публикации, хабы, капсулы, чек-лист."""
    from backend.app.services import campus, community, hubs, planner, prefs

    planner.delete_all(telegram_id, session=session)
    prefs.delete_user(session, telegram_id)
    community.delete_user(session, telegram_id)
    hubs.delete_user(session, telegram_id)
    campus.delete_user(session, telegram_id)
    from backend.app.db.models_mod import ModLog
    from backend.app.services import files, roles, syllabus

    files.delete_user(session, telegram_id)      # файлы на диске и записи о них
    syllabus.delete_user(session, telegram_id)
    roles.delete_user(session, telegram_id)
    # Журнал модерации остаётся (кто и что решал), но без имени удалившегося модератора.
    for row in session.scalars(select(ModLog).where(ModLog.moderator_id == telegram_id)):
        row.moderator_name = "(данные удалены)"


def delete_user_data(session: Session, telegram_id: int) -> None:
    """Удаляет всё о пользователе (команда /delete).

    Договорённости, где он был второй стороной, остаются у первой стороны,
    но без его имени и id — это её запись.
    """
    user = get_user(session, telegram_id)
    if user and user.family_id:
        leave_family(session, user)
    from backend.app.services.circles import delete_user as leave_circles

    leave_circles(session, telegram_id)
    _delete_campus(session, telegram_id)
    own_agreements = select(Agreement.id).where(Agreement.creator_id == telegram_id)
    session.execute(delete(AgreementParticipant).where(AgreementParticipant.agreement_id.in_(own_agreements)))
    for model, column in (
        (Check, Check.user_id), (Vote, Vote.user_id), (UserSource, UserSource.user_id),
        (DailyAnswer, DailyAnswer.user_id), (TrainerSession, TrainerSession.user_id), (LLMUsage, LLMUsage.user_id),
        (Agreement, Agreement.creator_id), (AgreementParticipant, AgreementParticipant.user_id),
    ):
        session.execute(delete(model).where(column == telegram_id))
    for agreement in session.scalars(select(Agreement).where(Agreement.counterparty_id == telegram_id)):
        agreement.counterparty_id = None
        agreement.counterparty_name = "(данные удалены)"
    # Пустая семья уже удалена в leave_family; если в семье остались другие — она остаётся им.
    if user:
        session.delete(user)
    session.commit()

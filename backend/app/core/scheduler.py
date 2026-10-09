"""Планировщик: индекс чата, напоминания о договорённостях,
а для МУИТ — напоминания академического календаря и вопрос «ты теперь
на N курсе?» в начале учебного года. Лента новостей обновляется отдельным
циклом (university/news.py → run_news_loop), чтобы работать и без бота.

Устроен максимально просто: фоновая задача раз в минуту смотрит на часы
(в часовом поясе Алматы) и решает, пора ли что-то отправить. Чтобы не
отправить дважды (например, после перезапуска сервера), дата последней
рассылки хранится в базе (таблица meta).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.core.config import get_settings
from backend.app.core.notify import Notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.services.chat_index import render_index, week_stats

log = logging.getLogger("verdikt.scheduler")


async def send_weekly_index(notifier: Notifier) -> int:
    with get_sessionmaker()() as session:
        chats = [c.id for c in repo.active_group_chats(session)]
    sent = 0
    for chat_id in chats:
        stats = week_stats(chat_id)
        if stats["total"]:  # пустую статистику не шлём, чтобы не спамить
            sent += await notifier.send(chat_id, html.escape(render_index(stats)))
    return sent


async def send_reminders(notifier: Notifier, today: date) -> int:
    sent = 0
    with get_sessionmaker()() as session:
        for agreement in repo.due_agreements(session):
            try:
                deadline = date.fromisoformat(agreement.deadline_iso)
            except ValueError:
                continue
            what = json.loads(agreement.draft_json or "{}").get("what") or agreement.text
            key = None
            if deadline - today == timedelta(days=1) and not agreement.reminded_before:
                key, agreement.reminded_before = "agr.remind.before", True
            elif deadline == today and not agreement.reminded_due:
                key, agreement.reminded_due = "agr.remind.due", True
            if key is None:
                continue
            text = html.escape(t(key, what=what[:200]))
            recipients = {agreement.creator_id, agreement.counterparty_id} - {None}
            if agreement.multi:
                recipients |= {p.user_id for p in repo.agreement_participants(session, agreement.id) if p.accepted}
            if agreement.chat_id and agreement.chat_id < 0:
                # Договорённость из группы — напоминаем в группе (в ту же тему) и, с функцией
                # «Дедлайны из Договорились», ещё и каждому участнику в личку.
                from backend.app.core.features import enabled

                people = recipients if enabled("agreement_deadlines") else set()
                kwargs = {"thread_id": agreement.thread_id} if agreement.thread_id else {}
                sent += await notifier.send(agreement.chat_id, text, None, **kwargs)
                recipients = {uid for uid in people if (u := repo.get_user(session, uid)) and u.bot_started}
            for chat_id in recipients:
                sent += await notifier.send(chat_id, text)
        session.commit()
    return sent


async def send_calendar_reminders(notifier: Notifier, today: date) -> int:
    """Напоминания о событиях академкалендаря: за N дней и в день начала. Каждое событие — один раз."""
    from backend.app.university import calendar as acal

    sent = 0
    remind_days = get_settings().calendar_remind_days
    for event in acal.reminders_due(today, remind_days):
        kind = "today" if event.start == today else "before"
        key = f"cal:{event.id}:{kind}"
        with get_sessionmaker()() as session:
            if repo.get_meta(session, key):
                continue
            repo.set_meta(session, key, today.isoformat())
            users = [u.telegram_id for u in repo.students_for_courses(session, event.courses)]
        template = "cal.remind_today" if kind == "today" else "cal.remind"
        text = t(template, title=html.escape(event.title), dates=html.escape(event.dates_label()), url=html.escape(event.source_url))
        for user_id in users:
            sent += await notifier.send(user_id, text)
            await asyncio.sleep(0.05)
    return sent


async def send_course_up(notifier: Notifier, today: date) -> int:
    """В начале учебного года спрашивает студентов 1–3 курса: «Ты теперь на N+1 курсе?»."""
    from backend.app.telegram.handlers.onboarding import course_up_keyboard
    from backend.app.university import calendar as acal

    cal = acal.calendar_for(today)
    if cal is None or today < cal.year_start:
        return 0
    year = acal.academic_year_start(today)
    key = f"course_up:{year}"
    with get_sessionmaker()() as session:
        if repo.get_meta(session, key):
            return 0
        repo.set_meta(session, key, today.isoformat())
        users = [(u.telegram_id, u.course) for u in repo.students_to_promote(session, year)]
    sent = 0
    for user_id, course in users:
        nxt = str(int(course) + 1)
        sent += await notifier.send(user_id, html.escape(t("course.ask_up", course=nxt)), course_up_keyboard(nxt))
        await asyncio.sleep(0.05)
    return sent


async def tick(notifier: Notifier, now: datetime) -> None:
    """Один «тик» планировщика. Отдельная функция — чтобы её можно было тестировать с любым временем."""
    settings = get_settings()
    today = now.date()
    with get_sessionmaker()() as session:
        week_key = f"{today.isocalendar().year}-W{today.isocalendar().week}"
        index_done = repo.get_meta(session, "index_sent") == week_key

    if today.weekday() == settings.weekly_index_weekday and now.hour >= settings.weekly_index_hour and not index_done:
        count = await send_weekly_index(notifier)
        with get_sessionmaker()() as session:
            repo.set_meta(session, "index_sent", week_key)
        log.info("Индекс чата отправлен в %d групп", count)

    if now.hour >= 10:  # напоминания — не раньше 10 утра
        count = await send_reminders(notifier, today)
        if count:
            log.info("Напоминаний о договорённостях: %d", count)
        count = await send_calendar_reminders(notifier, today)
        if count:
            log.info("Напоминаний академкалендаря: %d", count)
        count = await send_course_up(notifier, today)
        if count:
            log.info("Вопросов «ты теперь на новом курсе?»: %d", count)

    # Срок хранения загруженных файлов: раз в день удаляем просроченные.
    with get_sessionmaker()() as session:
        purge_done = repo.get_meta(session, "files_purged") == today.isoformat()
    if not purge_done:
        from backend.app.services.files import purge_expired

        purge_expired()
        with get_sessionmaker()() as session:
            repo.set_meta(session, "files_purged", today.isoformat())

    # Новые функции: планер, опросы, сообщество, хабы (каждая выключается флагом FEATURE_*).
    from backend.app.core.scheduler_campus import tick_campus

    await tick_campus(notifier, now)


async def run_scheduler(notifier: Notifier) -> None:
    zone = ZoneInfo(get_settings().timezone)
    log.info("Планировщик запущен (часовой пояс %s).", zone)
    while True:
        try:
            await tick(notifier, datetime.now(zone))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(60)

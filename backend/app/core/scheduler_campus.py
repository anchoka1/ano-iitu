"""Фоновые задачи новых функций (вызываются из core/scheduler.py → tick раз в минуту).

Всё, что рассылается людям, — только подписчикам (по умолчанию подписки выключены),
кроме напоминаний о договорённостях, в которых человек участвует сам.
Каждая рассылка отмечается в таблице meta, чтобы после перезапуска не отправить дважды.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.core.config import get_settings
from backend.app.core.features import enabled
from backend.app.core.notify import Notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t

log = logging.getLogger("verdikt.scheduler")


def _once(key: str) -> bool:
    """True — ещё не делали (и сразу отмечаем, что сделали)."""
    with get_sessionmaker()() as session:
        if repo.get_meta(session, key):
            return False
        repo.set_meta(session, key, datetime.now().isoformat(timespec="minutes"))
        return True


def _week(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


async def _send_all(notifier: Notifier, user_ids: list[int], text: str, buttons=None) -> int:
    sent = 0
    for uid in user_ids:
        sent += await notifier.send(uid, text, buttons)
        await asyncio.sleep(0.05)
    return sent


def _course(user_id: int) -> str:
    from backend.app.services.campus import get_user_course

    return get_user_course(user_id)


# ------------------------------------------------------------------ договорённости: «за час»


async def agreement_hour_reminders(notifier: Notifier, now: datetime) -> int:
    """Срок договорённости с временем: напоминание всем участникам за час (в группе — в ту же тему)."""
    from backend.app.services import agreements as agreements_service

    sent = 0
    zone = ZoneInfo(get_settings().timezone)
    with get_sessionmaker()() as session:
        for agreement in repo.due_agreements(session):
            if not agreement.deadline_time or agreement.reminded_hour:
                continue
            try:
                d = date.fromisoformat(agreement.deadline_iso)
                hh, mm = (int(x) for x in agreement.deadline_time.split(":"))
            except ValueError:
                continue
            due = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone)
            if not (due - timedelta(minutes=60) <= now < due):
                continue
            agreement.reminded_hour = True
            what = json.loads(agreement.draft_json or "{}").get("what") or agreement.text
            text = html.escape(t("agr.remind.hour", what=what[:200], time=agreement.deadline_time))
            people = {agreement.creator_id, agreement.counterparty_id} - {None}
            people |= {p["user_id"] for p in agreements_service.participants(agreement) if p["accepted"]}
            if agreement.chat_id and agreement.chat_id < 0:
                kwargs = {"thread_id": agreement.thread_id} if agreement.thread_id else {}
                sent += await notifier.send(agreement.chat_id, text, None, **kwargs)
            started = {u.telegram_id for u in (repo.get_user(session, uid) for uid in people) if u and u.bot_started}
            for uid in started:
                sent += await notifier.send(uid, text)
        session.commit()
    return sent


# ------------------------------------------------------------------ планер


async def planner_jobs(notifier: Notifier, now: datetime) -> None:
    from backend.app.services import planner, prefs

    if not enabled("planner"):
        return
    for user_id, text, task_id in planner.due_reminders(now):
        await notifier.send(user_id, text, [[(t("pl.btn.done"), f"pl:done:{task_id}"), (t("pl.btn.tomorrow"), f"pl:move:{task_id}:1")]])
    if enabled("planner_promise") and now.hour >= 10 and _once(f"promises:{now.date().isoformat()}"):
        planner.overdue_promises(now.date())
    await planner.flush_witness_queue()

    today = now.date()
    if now.hour >= get_settings().morning_hour:
        if enabled("morning_digest") and _once(f"morning:{today.isoformat()}"):
            for uid in prefs.subscribers("sub.morning"):
                await notifier.send(uid, planner.morning_text(uid, _course(uid)))
                await asyncio.sleep(0.05)
        if enabled("planner_frog") and _once(f"frog:{today.isoformat()}"):
            for uid in prefs.subscribers("sub.frog"):
                data = planner.view(uid, "today", today)
                items = (data["overdue"] + data["today"])[:5]
                if len(items) < 2:
                    continue
                buttons = [[(f"🐸 {x['title'][:40]}", f"pl:frog:{x['id']}")] for x in items]
                await notifier.send(uid, html.escape(t("pl.frog.ask")), buttons)
                await asyncio.sleep(0.05)

    s = get_settings()
    week = _week(today)
    if enabled("planner_traffic") and today.weekday() == s.traffic_weekday and now.hour >= s.traffic_hour and _once(f"traffic:{week}"):
        for uid in prefs.subscribers("sub.traffic"):
            light = planner.traffic_light(uid, _course(uid), today)
            if light["level"] != "green":
                early = "\n".join(f"• {html.escape(x['title'])} — {x['due_date']}" for x in light["start_early"])
                await notifier.send(uid, f"{'🔴' if light['level'] == 'red' else '🟡'} <b>{html.escape(t('pl.traffic.title'))}</b>\n"
                                         f"{html.escape(light['text'])}\n\n{html.escape(t('pl.traffic.start_early'))}\n{early}")
            await asyncio.sleep(0.05)
    if enabled("planner_week_verdict") and today.weekday() == s.week_verdict_weekday and now.hour >= s.week_verdict_hour and _once(f"wverdict:{week}"):
        from backend.app.cards.render import render_card_html

        for uid in prefs.subscribers("sub.week_verdict"):
            await notifier.send(uid, render_card_html(planner.week_verdict_card(uid, today), ""), [[(t("pl.btn.verdict_image"), "pl:vimg")]])
            await asyncio.sleep(0.05)


# ------------------------------------------------------------------ сообщество и опросы


async def community_jobs(notifier: Notifier, now: datetime) -> None:
    from backend.app.services import campus, community, polls, prefs

    s = get_settings()
    today = now.date()
    week = _week(today)
    if enabled("rumor_week") and today.weekday() == s.rumor_weekday and now.hour >= s.rumor_hour and _once(f"rumor_sent:{week}"):
        rumor = campus.rumor_of_week(today)
        if rumor:
            text = rumor_html(rumor)
            sent = await _send_all(notifier, prefs.subscribers("sub.rumor"), text, [[(t("btn.share_image"), f"img:{rumor['check_id']}")]])
            log.info("Слух недели отправлен: %d", sent)
    if enabled("pulse") and today.weekday() == s.pulse_weekday and now.hour >= s.pulse_hour and _once(f"pulse_sent:{week}"):
        pulse = polls.current_pulse(today)
        buttons = [[(opt, f"poll:a:{pulse['id']}:{i}")] for i, opt in enumerate(pulse["options"])]
        await _send_all(notifier, prefs.subscribers("sub.pulse"), f"📊 <b>{html.escape(t('cm.pulse.title'))}</b>\n\n{html.escape(pulse['question'])}", buttons)
    if now.hour >= 10 and _once(f"daily_campus:{today.isoformat()}"):
        if enabled("events"):
            for uid, text in community.events_tomorrow(today):
                await notifier.send(uid, text)
        if enabled("time_capsule"):
            for uid, text in campus.capsules_due(today):
                await notifier.send(uid, text)


def rumor_html(rumor: dict) -> str:
    e = html.escape
    emoji = {"green": "🟢", "red": "🔴", "yellow": "🟡"}.get(rumor["status"], "⚪")
    lines = [f"🔎 <b>{e(t('cm.rumor.title'))}</b>", "", f"«{e(rumor['text'][:300])}»", "",
             f"{emoji} <b>{e(rumor['verdict_label'].upper())}</b> — {e(rumor['title'])}"]
    for src in rumor["sources"][:2]:
        lines.append(f"📎 <a href=\"{e(src['url'])}\">{e(src['title'])}</a>" + (f" ({e(src['date'])})" if src.get("date") else ""))
    lines += ["", f"<i>{e(t('cm.rumor.share_hint'))}</i>"]
    return "\n".join(lines)


# ------------------------------------------------------------------ хабы


async def hub_jobs(notifier: Notifier, now: datetime) -> None:
    from backend.app.services import hubs

    s = get_settings()
    today = now.date()
    if enabled("hub_digest") and today.weekday() == s.weekly_index_weekday and now.hour >= s.weekly_index_hour and _once(f"hub_digest:{_week(today)}"):
        for chat_id, thread_id in hubs.digest_targets():
            kwargs = {"thread_id": thread_id} if thread_id else {}
            await notifier.send(chat_id, hubs.digest_text(chat_id, thread_id), None, **kwargs)
            await asyncio.sleep(0.1)
    if enabled("mentors") and today.month == 9 and today.day >= 1 and now.hour >= 12 and _once(f"mentor_call:{today.year}"):
        # Раз в год — объявление о наборе менторов: в привязанные к хабам группы (не в личку — без спама).
        from sqlalchemy import select

        from backend.app.db.models_campus import HubChat

        with get_sessionmaker()() as session:
            chats = {c.chat_id for c in session.scalars(select(HubChat).where(HubChat.thread_id == 0))}
        for chat_id in chats:
            await notifier.send(chat_id, html.escape(t("hub.mentor_call")))


async def tick_campus(notifier: Notifier, now: datetime) -> None:
    for job in (planner_jobs, community_jobs, hub_jobs):
        try:
            await job(notifier, now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — сбой одной функции не должен останавливать остальные
            log.exception("Ошибка фоновой задачи %s", job.__name__)
    if enabled("agreement_deadlines"):
        try:
            count = await agreement_hour_reminders(notifier, now)
            if count:
                log.info("Напоминаний «за час» о договорённостях: %d", count)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка напоминаний «за час»")

"""Университетские команды: /news, /navigator, /kalendar, /services, /adal.

Все тексты — из data/iitu/*.json и кэша новостей; даты — только из
академического календаря, никаких «зашитых» в код сроков.
"""

from __future__ import annotations

import asyncio
import html
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.i18n import t
from backend.app.university import calendar, catalog, news, reference
from backend.app.university.navigator import course_ids, navigator, render_course_html
from backend.app.telegram.handlers.onboarding import get_profile, set_flag

router = Router(name="university")


# ------------------------------------------------------------------ новости


def _news_keyboard(subscribed: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("news.btn.refresh"), callback_data="news:refresh"),
        InlineKeyboardButton(text=t("news.btn.unsubscribe") if subscribed else t("news.btn.subscribe"),
                             callback_data=f"news:sub:{0 if subscribed else 1}"),
    ]])


@router.message(Command("news"))
async def cmd_news(message: Message) -> None:
    text = await asyncio.to_thread(news.render_news_html, 6)
    profile = await asyncio.to_thread(get_profile, message.from_user.id)
    subscribed = bool(profile and profile["news_subscribed"])
    markup = _news_keyboard(subscribed) if message.chat.type == "private" else None
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("news:"))
async def cb_news(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    user = callback.from_user
    if parts[1] == "sub":
        on = parts[2] == "1"
        await asyncio.to_thread(set_flag, user.id, user.first_name or "", "news_subscribed", on)
        await callback.answer(t("news.subscribed") if on else t("news.unsubscribed"), show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=_news_keyboard(on))
        except Exception:  # noqa: BLE001 — «сообщение не изменилось» не ошибка
            pass
        return
    if parts[1] == "refresh":
        await callback.answer("⏳")
        # Обновление берёт кэш; сеть трогаем, только если давно не обновлялись — защита от «долбления» сайта.
        from datetime import datetime, timezone

        if await asyncio.to_thread(news.is_due, datetime.now(timezone.utc)):
            await news.refresh()
        profile = await asyncio.to_thread(get_profile, user.id)
        text = await asyncio.to_thread(news.render_news_html, 6)
        try:
            await callback.message.edit_text(text, reply_markup=_news_keyboard(bool(profile and profile["news_subscribed"])),
                                             disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            pass
        return
    await callback.answer()


# ------------------------------------------------------------------ навигатор


def _course_picker() -> InlineKeyboardMarkup:
    data = navigator()["courses"]
    buttons = [InlineKeyboardButton(text=data[c]["title"], callback_data=f"nav:{c}") for c in course_ids()]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 3] for i in range(0, len(buttons), 3)])


@router.message(Command("navigator"))
async def cmd_navigator(message: Message) -> None:
    profile = await asyncio.to_thread(get_profile, message.from_user.id)
    course = (profile or {}).get("course", "")
    from backend.app.university.reference import nav_course

    if course and nav_course(course) in course_ids():
        await message.answer(render_course_html(course, date.today()), reply_markup=_course_picker(), disable_web_page_preview=True)
    else:
        await message.answer(html.escape(t("nav.pick_course")), reply_markup=_course_picker())


@router.callback_query(F.data.startswith("nav:"))
async def cb_navigator(callback: CallbackQuery) -> None:
    course = callback.data.split(":", 1)[1]
    await callback.answer()
    if course in course_ids():
        try:
            await callback.message.edit_text(render_course_html(course, date.today()), reply_markup=_course_picker(), disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 — тот же курс: текст не изменился
            pass


# ------------------------------------------------------------------ календарь


def render_calendar_html(course: str, today: date, days: int = 14) -> str:
    e = html.escape
    cal = calendar.calendar_for(today)
    if cal is None:
        return e(t("cal.not_loaded")) + f"\n{calendar.CALENDAR_PAGE}"
    course_label = reference.course_title(course) if course else "все курсы"
    # Без курса — общее для большинства (1–3 курс и праздники), без узких событий выпускников.
    events = [ev for ev in calendar.events_for(course, today, days)
              if ev.kind not in ("study",) and not ev.id.startswith("g_ext") and (course or calendar.is_general(ev))]
    lines = [t("cal.title", course=e(course_label)), ""]
    if not events:
        lines.append(e(t("cal.empty")))
        events = [ev for ev in calendar.next_events(course, today, limit=10, kinds={"midterm", "session", "fx", "registration", "thesis", "final", "practice"})
                  if course or calendar.is_general(ev)][:3]
    for ev in events[:10]:
        lines.append(f"{ev.emoji} <b>{e(ev.dates_label())}</b> — {e(ev.title)}")
    lines += ["", f'<i>{e(t("cal.source", year=cal.academic_year, checked=cal.checked))}</i>',
              f'<a href="{e(cal.page_url)}">Академический календарь на сайте МУИТ</a>']
    return "\n".join(lines)


@router.message(Command("kalendar"))
async def cmd_calendar(message: Message) -> None:
    profile = await asyncio.to_thread(get_profile, message.from_user.id)
    course = (profile or {}).get("course", "") if (profile or {}).get("role") == "student" else ""
    await message.answer(render_calendar_html(course, date.today()), disable_web_page_preview=True)


# ------------------------------------------------------------------ сервисы и контакты


def _services_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=f"{s['emoji']} {s['title']}"[:40], callback_data=f"svc:{s['id']}") for s in catalog.services()]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)])


@router.message(Command("services"))
async def cmd_services(message: Message) -> None:
    await message.answer(catalog.render_catalog_html(), reply_markup=_services_keyboard(), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("svc:"))
async def cb_service(callback: CallbackQuery) -> None:
    service = catalog.get_service(callback.data.split(":", 1)[1])
    await callback.answer()
    if service:
        back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("svc.btn.all"), callback_data="svc_all")]])
        await callback.message.edit_text(catalog.render_service_html(service), reply_markup=back, disable_web_page_preview=True)


@router.callback_query(F.data == "svc_all")
async def cb_services_all(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(catalog.render_catalog_html(), reply_markup=_services_keyboard(), disable_web_page_preview=True)


# ------------------------------------------------------------------ Адал


@router.message(Command("adal"))
async def cmd_adal(message: Message) -> None:
    await message.answer(t("adal.text"), disable_web_page_preview=True)

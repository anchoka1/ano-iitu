"""Онбординг и настройки: роль → курс/факультет/программа или кафедра → язык; /settings.

Принцип минимума данных: храним только роль, курс, факультет/программу или
кафедру и язык. ИИН, документы и оценки не спрашиваем.

Роль преподавателя — по самозаявлению, без проверки. Поэтому она НЕ даёт
никаких прав над студентами: меняются только приветствие, подсказки и
набор инструментов (которые и так доступны всем).

callback_data (до 64 байт):
  onb:role:student|teacher   onb:course:1..4|master|phd   onb:fac:fctc|fbmu|skip
  onb:prog:<код>|skip        onb:dep:<id>                 onb:lang:ru|kk|en
  set:news:1|0  set:cal:1|0  set:edit  course:yes:<курс>  course:change
"""

from __future__ import annotations

import asyncio
import html
from datetime import date

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.university import reference
from backend.app.university.calendar import academic_year_start
from backend.app.university.navigator import render_changes_html

router = Router(name="onboarding")


def _rows(buttons: list[InlineKeyboardButton], per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("onb.btn.student"), callback_data="onb:role:student"),
        InlineKeyboardButton(text=t("onb.btn.teacher"), callback_data="onb:role:teacher"),
    ]])


def course_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=c["title"], callback_data=f"onb:course:{c['id']}") for c in reference.courses()]
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 2))


def faculty_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{f['short']} — {f['title']}", callback_data=f"onb:fac:{f['id']}")] for f in reference.faculties()]
    rows.append([InlineKeyboardButton(text=t("onb.btn.skip"), callback_data="onb:fac:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def program_keyboard(faculty_id: str, course: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{p['code']} {p['title']}"[:60], callback_data=f"onb:prog:{p['code']}")]
            for p in reference.programs(faculty_id, course)]
    rows.append([InlineKeyboardButton(text=t("onb.btn.skip"), callback_data="onb:prog:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def department_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=d["title"][:40], callback_data=f"onb:dep:{d['id']}") for d in reference.departments()]
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 1))


def lang_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=lang["title"] + ("" if lang["ready"] else " · скоро"), callback_data=f"onb:lang:{lang['id']}")
               for lang in reference.languages()]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _update(user_id: int, first_name: str, **fields: str) -> dict:
    """Сохраняет поля профиля и возвращает его копию (простые данные — удобно в async-коде)."""
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, first_name)
        year = academic_year_start(date.today()) if "course" in fields else None
        repo.set_profile(session, user, academic_year=year, **fields)
        return {k: getattr(user, k) for k in ("role", "course", "faculty", "program", "department", "ui_lang", "onboarded",
                                               "news_subscribed", "calendar_reminders", "first_name")}


def get_profile(user_id: int) -> dict | None:
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        if user is None:
            return None
        return {k: getattr(user, k) for k in ("role", "course", "faculty", "program", "department", "ui_lang", "onboarded",
                                               "news_subscribed", "calendar_reminders", "first_name", "course_year")}


def profile_label(profile: dict) -> str:
    return reference.profile_label(profile["role"], profile["course"], profile["faculty"], profile["program"], profile["department"])


def greeting(profile: dict, name: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Приветствие по роли (студенту — на «ты», преподавателю — на «вы») + кнопка Mini App."""
    key = "bot.start.teacher" if profile.get("role") == "teacher" else "bot.start.student"
    text = html.escape(t(key, name=name or "друг", profile=profile_label(profile) or "—"))
    url = get_settings().webapp_url
    if url.lower().startswith("https://"):
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("bot.start.open_app"), web_app=WebAppInfo(url=url))]])
        return text, markup
    return text + "\n\n" + html.escape(t("bot.start.no_webapp")), None


async def start_onboarding(message: Message, name: str) -> None:
    await message.answer(html.escape(t("onb.role_q", name=name or "друг")) + "\n\n" + html.escape(t("onb.privacy")), reply_markup=role_keyboard())


@router.callback_query(F.data.startswith("onb:"))
async def cb_onboarding(callback: CallbackQuery) -> None:
    _, step, value = callback.data.split(":", 2)
    user = callback.from_user
    name = user.first_name or ""
    if step == "role" and reference.is_valid("role", value):
        profile = await asyncio.to_thread(_update, user.id, name, role=value)
        await callback.answer()
        if value == "student":
            await callback.message.edit_text(html.escape(t("onb.course_q")), reply_markup=course_keyboard())
        else:
            await callback.message.edit_text(html.escape(t("onb.department_q") + "\n\n" + t("onb.teacher_note")), reply_markup=department_keyboard())
        return
    if step == "course" and reference.is_valid("course", value):
        before = await asyncio.to_thread(get_profile, user.id)
        await asyncio.to_thread(_update, user.id, name, course=value)
        await callback.answer()
        if before and before.get("onboarded") and before.get("course") and before["course"] != value and before.get("faculty"):
            # Смена курса из настроек: сразу сводка «что меняется».
            await callback.message.edit_text(t("course.updated", course=html.escape(reference.course_title(value))))
            changes = render_changes_html(value)
            if changes:
                await callback.message.answer(changes, disable_web_page_preview=True)
            return
        await callback.message.edit_text(html.escape(t("onb.faculty_q")), reply_markup=faculty_keyboard())
        return
    if step == "fac":
        if value != "skip" and reference.is_valid("faculty", value):
            profile = await asyncio.to_thread(_update, user.id, name, faculty=value)
            await callback.answer()
            if reference.programs(value, profile["course"]):
                await callback.message.edit_text(html.escape(t("onb.program_q")), reply_markup=program_keyboard(value, profile["course"]))
                return
        else:
            await callback.answer()
        await callback.message.edit_text(html.escape(t("onb.lang_q")), reply_markup=lang_keyboard())
        return
    if step == "prog":
        if value != "skip" and reference.is_valid("program", value):
            await asyncio.to_thread(_update, user.id, name, program=value)
        await callback.answer()
        await callback.message.edit_text(html.escape(t("onb.lang_q")), reply_markup=lang_keyboard())
        return
    if step == "dep" and reference.is_valid("department", value):
        await asyncio.to_thread(_update, user.id, name, department=value)
        await callback.answer()
        await callback.message.edit_text(html.escape(t("onb.lang_q")), reply_markup=lang_keyboard())
        return
    if step == "lang" and reference.is_valid("lang", value):
        ready = next((lang["ready"] for lang in reference.languages() if lang["id"] == value), False)
        profile = await asyncio.to_thread(_update, user.id, name, ui_lang=value if ready else "ru")
        await callback.answer(t("onb.lang_soon") if not ready else t("set.saved"), show_alert=not ready)
        text, markup = greeting(profile, name)
        await callback.message.edit_text(text, reply_markup=markup)
        if profile["role"] == "student" and profile["course"]:
            changes = render_changes_html(profile["course"])
            if changes:
                await callback.message.answer(changes, disable_web_page_preview=True)
        return
    await callback.answer()


# ------------------------------------------------------------------ /settings


def settings_view(user_id: int, first_name: str) -> tuple[str, InlineKeyboardMarkup]:
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, first_name)
        label = reference.profile_label(user.role, user.course, user.faculty, user.program, user.department)
        news_on, cal_on = user.news_subscribed, user.calendar_reminders
    e = html.escape
    lines = [t("set.title"), "", e(t("set.profile", profile=label) if label else t("set.no_profile")),
             e(t("set.news", state=t("set.on") if news_on else t("set.off"))),
             e(t("set.calendar", state=t("set.on") if cal_on else t("set.off"))), "", e(t("onb.privacy"))]
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("set.btn.role"), callback_data="set:edit")],
        [InlineKeyboardButton(text=t("set.btn.news_off") if news_on else t("set.btn.news_on"), callback_data=f"set:news:{0 if news_on else 1}")],
        [InlineKeyboardButton(text=t("set.btn.cal_off") if cal_on else t("set.btn.cal_on"), callback_data=f"set:cal:{0 if cal_on else 1}")],
    ])
    return "\n".join(lines), markup


def set_flag(user_id: int, first_name: str, field: str, value: bool) -> None:
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, first_name)
        setattr(user, field, value)
        session.commit()


@router.message(Command("settings"), F.chat.type == ChatType.PRIVATE)
async def cmd_settings(message: Message) -> None:
    text, markup = await asyncio.to_thread(settings_view, message.from_user.id, message.from_user.first_name or "")
    await message.answer(text, reply_markup=markup)


@router.message(Command("settings"))
async def cmd_settings_group(message: Message) -> None:
    await message.reply(t("set.private_only"))


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    user = callback.from_user
    if parts[1] == "edit":
        await callback.answer()
        await callback.message.edit_text(html.escape(t("onb.role_q", name=user.first_name or "")), reply_markup=role_keyboard())
        return
    field = {"news": "news_subscribed", "cal": "calendar_reminders"}.get(parts[1])
    if field:
        await asyncio.to_thread(set_flag, user.id, user.first_name or "", field, parts[2] == "1")
        if field == "news_subscribed":
            await callback.answer(t("news.subscribed") if parts[2] == "1" else t("news.unsubscribed"), show_alert=True)
        else:
            await callback.answer(t("set.saved"))
        text, markup = await asyncio.to_thread(settings_view, user.id, user.first_name or "")
        await callback.message.edit_text(text, reply_markup=markup)
        return
    await callback.answer()


# ------------------------------------------------------------------ новый учебный год: «ты теперь на N курсе?»


@router.callback_query(F.data.startswith("course:"))
async def cb_course_up(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    user = callback.from_user
    if parts[1] == "yes" and len(parts) > 2 and reference.is_valid("course", parts[2]):
        await asyncio.to_thread(_update, user.id, user.first_name or "", course=parts[2])
        await callback.answer(t("course.updated", course=reference.course_title(parts[2])))
        await callback.message.edit_text(render_changes_html(parts[2]), disable_web_page_preview=True)
        return
    await callback.answer()
    await callback.message.edit_text(html.escape(t("onb.course_q")), reply_markup=course_keyboard())


def course_up_keyboard(next_course: str) -> list[list[tuple[str, str]]]:
    """Кнопки для рассылки планировщика (формат Notifier: [(текст, callback_data)])."""
    return [[(t("course.btn.yes"), f"course:yes:{next_course}"), (t("course.btn.change"), "course:change")]]

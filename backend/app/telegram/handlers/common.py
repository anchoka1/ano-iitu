"""/start, /help, /delete.

/start может прийти с параметром (deep link): t.me/бот?start=ПАРАМЕТР.
  fam_ТОКЕН — приглашение в семью;
  agr_КОД   — подтвердить договорённость (в т. ч. групповую);
  grp_ТОКЕН — приглашение в «Группы и потоки».
Без параметра в личке: новый человек проходит онбординг (handlers/onboarding.py),
знакомый получает приветствие по роли.
"""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.services import agreements as agreements_service
from backend.app.telegram.keyboards import agreement_keyboard, yes_no
from backend.app.telegram.texts import build_start_reply

router = Router(name="common")


def _save_user(telegram_id: int, first_name: str, lang: str, started: bool) -> None:
    with get_sessionmaker()() as session:
        repo.upsert_user(session, telegram_id, first_name, lang, bot_started=started)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    user = message.from_user
    is_private = message.chat.type == ChatType.PRIVATE
    if user and is_private:
        # База синхронная, поэтому запись выполняем в отдельном потоке (asyncio.to_thread),
        # чтобы не «замораживать» бота.
        await asyncio.to_thread(_save_user, user.id, user.first_name or "", user.language_code or "ru", True)

    payload = (command.args or "").strip()
    if is_private and payload.startswith("fam_"):
        token = payload[4:]
        await message.answer(t("family.invite"), reply_markup=yes_no(f"fam:join:{token}", "fam:no", t("family.btn.join"), t("family.btn.no")))
        return
    if is_private and payload.startswith("grp_"):
        from backend.app.telegram.handlers.circles import show_invite

        await show_invite(message, payload[4:])
        return
    if is_private and await _campus_deep_link(message, payload, state):
        return
    if is_private and payload.startswith("agr_"):
        agreement = agreements_service.get(payload[4:])
        if agreement is None:
            await message.answer(t("agr.err.not_found"))
            return
        await message.answer(agreements_service.render_html(agreement), reply_markup=agreement_keyboard(agreement.code, agreement.status, agreement.multi))
        return

    if is_private and user:
        # Университетская версия: сначала знакомство (роль → курс/кафедра → язык), потом приветствие по роли.
        from backend.app.telegram.handlers.onboarding import get_profile, greeting, start_onboarding

        profile = await asyncio.to_thread(get_profile, user.id)
        if not profile or not profile["onboarded"]:
            await start_onboarding(message, user.first_name or "")
            return
        text, markup = greeting(profile, user.first_name or "")
        from backend.app.telegram.handlers.flows import today_html

        summary = await asyncio.to_thread(today_html, user.id, user.first_name or "")
        await message.answer(text + ("\n\n" + summary if summary else ""), reply_markup=markup)
        return

    reply = build_start_reply(first_name=user.first_name if user else "", is_private=is_private, webapp_url=get_settings().webapp_url)
    markup = None
    if reply.webapp_url:
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("bot.start.open_app"), web_app=WebAppInfo(url=reply.webapp_url))]])
    await message.answer(reply.text, reply_markup=markup)


async def _campus_deep_link(message: Message, payload: str, state: FSMContext) -> bool:
    """Ссылки новых функций: wit_ (свидетель обещания), poll_ (опрос после пары), ack_ (правила ИИ / объявление),
    slot_ (консультации преподавателя), hub_ (учебный хаб)."""
    prefix, _, value = payload.partition("_")
    if not value or prefix not in ("wit", "poll", "ack", "slot", "hub"):
        return False
    if prefix == "wit":
        from backend.app.telegram.handlers.planner import show_witness_invite

        await show_witness_invite(message, value)
        return True
    if not value.isdigit():
        return False
    from backend.app.telegram.handlers import campus

    if prefix == "poll":
        await campus.show_poll(message, int(value), message.from_user.id, state)
    elif prefix == "ack":
        await campus.show_ack_post(message, int(value))
    elif prefix == "slot":
        await campus.show_slots(message, int(value))
    elif prefix == "hub":
        await campus.show_hub(message, int(value))
    return True


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(t("bot.help"))


@router.message(Command("delete"), F.chat.type == ChatType.PRIVATE)
async def cmd_delete(message: Message) -> None:
    await message.answer(t("bot.delete.confirm"), reply_markup=yes_no("del:yes", "del:no", t("bot.delete.yes"), t("bot.delete.no")))


@router.message(Command("delete"))
async def cmd_delete_group(message: Message) -> None:
    await message.reply(t("bot.delete.private_only"))


@router.callback_query(F.data == "del:yes")
async def cb_delete_yes(callback: CallbackQuery) -> None:
    def _delete(uid: int) -> None:
        with get_sessionmaker()() as session:
            repo.delete_user_data(session, uid)

    await asyncio.to_thread(_delete, callback.from_user.id)
    await callback.message.edit_text(t("bot.delete.done"))
    await callback.answer()


@router.callback_query(F.data == "del:no")
async def cb_delete_no(callback: CallbackQuery) -> None:
    await callback.message.edit_text(t("bot.delete.cancelled"))
    await callback.answer()

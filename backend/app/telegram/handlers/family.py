"""/family и вступление в семью по приглашению."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.i18n import t
from backend.app.services import family as service

router = Router(name="family")


async def _family_text(user_id: int, bot_username: str) -> tuple[str, InlineKeyboardMarkup | None]:
    info = service.info(user_id)
    if not info["has_family"]:
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("family.btn.create"), callback_data="fam:create")]])
        return t("family.info_none"), markup
    members = ", ".join(m["name"] + ("" if m["can_receive"] else " ⚠️") for m in info["members"])
    link = f"https://t.me/{bot_username}?start=fam_{info['invite_token']}"
    return html.escape(t("family.info", members=members, link=link)), None


@router.message(Command("family"), F.chat.type == ChatType.PRIVATE)
async def cmd_family(message: Message) -> None:
    me = await message.bot.me()
    text, markup = await _family_text(message.from_user.id, me.username or "")
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("family"))
async def cmd_family_group(message: Message) -> None:
    await message.reply(t("family.private_only"))


@router.callback_query(F.data == "fam:create")
async def cb_create(callback: CallbackQuery) -> None:
    service.create(callback.from_user.id, callback.from_user.first_name or "")
    me = await callback.bot.me()
    text, markup = await _family_text(callback.from_user.id, me.username or "")
    await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith("fam:join:"))
async def cb_join(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 2)[2]
    try:
        service.join(callback.from_user.id, callback.from_user.first_name or "", token)
    except service.FamilyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.edit_text(t("family.joined"))
    await callback.answer()


@router.callback_query(F.data == "fam:no")
async def cb_no(callback: CallbackQuery) -> None:
    await callback.message.edit_text(t("family.declined"))
    await callback.answer()

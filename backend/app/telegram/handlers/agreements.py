"""/dogovorilis — фиксация договорённости и подтверждение второй стороной.

/dogovor_gruppa — договорённость с группой (например, преподаватель: дедлайн,
критерии, формат сдачи): каждый участник подтверждает сам.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, User

from backend.app.core.engine import EngineError
from backend.app.core.fonts import FontNotFound
from backend.app.core.pdf import agreement_pdf
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.services import agreements as service
from backend.app.telegram.keyboards import agreement_keyboard

log = logging.getLogger("verdikt.bot")
router = Router(name="agreements")


class WaitAgreement(StatesGroup):
    text = State()
    group_text = State()


async def create_and_send(message: Message, text: str, user: User, multi: bool = False) -> None:
    """Создаёт договорённость из текста и публикует протокол с кнопками."""
    try:
        agreement = await service.create_agreement(
            text, user.id, user.full_name or user.first_name or "",
            chat_id=message.chat.id if message.chat.type != ChatType.PRIVATE else None, multi=multi,
            thread_id=message.message_thread_id if message.is_topic_message else None,
        )
    except EngineError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    sent = await message.answer(service.render_html(agreement), reply_markup=agreement_keyboard(agreement.code, agreement.status, agreement.multi))
    with get_sessionmaker()() as session:
        row = repo.get_agreement(session, agreement.code)
        row.message_id = sent.message_id
        row.chat_id = message.chat.id
        session.commit()
    if message.chat.type == ChatType.PRIVATE:
        me = await message.bot.me()
        key = "agr.multi_share" if multi else "agr.share"
        await message.answer(t(key, link=f"https://t.me/{me.username}?start=agr_{agreement.code}"), disable_web_page_preview=True)


@router.message(Command("dogovorilis"))
async def cmd_agreement(message: Message, command: CommandObject, state: FSMContext) -> None:
    text = (command.args or "").strip()
    if message.reply_to_message is not None:
        replied = message.reply_to_message.text or message.reply_to_message.caption or ""
        text = f"{replied}\n{text}".strip()
    if not text:
        if message.chat.type == ChatType.PRIVATE:
            await state.set_state(WaitAgreement.text)
            await message.answer(t("agr.ask"))
        else:
            await message.reply(t("agr.ask_group"))
        return
    await create_and_send(message, text, message.from_user)


@router.message(Command("dogovor_gruppa"))
async def cmd_group_agreement(message: Message, command: CommandObject, state: FSMContext) -> None:
    text = (command.args or "").strip()
    if message.reply_to_message is not None:
        replied = message.reply_to_message.text or message.reply_to_message.caption or ""
        text = f"{replied}\n{text}".strip()
    if not text:
        if message.chat.type == ChatType.PRIVATE:
            await state.set_state(WaitAgreement.group_text)
            await message.answer(t("agr.multi_ask"))
        else:
            await message.reply(t("agr.multi_ask_group"))
        return
    await create_and_send(message, text, message.from_user, multi=True)


@router.message(WaitAgreement.text, F.chat.type == ChatType.PRIVATE, F.text)
@router.message(WaitAgreement.group_text, F.chat.type == ChatType.PRIVATE, F.text)
async def on_agreement_text(message: Message, state: FSMContext) -> None:
    multi = await state.get_state() == WaitAgreement.group_text.state
    await state.clear()
    if message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    await create_and_send(message, message.text, message.from_user, multi=multi)


@router.callback_query(F.data.startswith("agr:"))
async def cb_agreement(callback: CallbackQuery) -> None:
    _, action, code = callback.data.split(":", 2)
    user = callback.from_user
    if action == "pdf":
        agreement = service.get(code)
        if agreement is None:
            await callback.answer(t("agr.err.not_found"), show_alert=True)
            return
        await callback.answer()
        try:
            data = await asyncio.to_thread(agreement_pdf, agreement)
        except FontNotFound as exc:
            await callback.message.answer(f"⚠️ {exc}")
            return
        await callback.message.answer_document(BufferedInputFile(data, f"dogovorennost_{code}.pdf"), caption=t("agr.pdf_caption"))
        return
    try:
        if action in ("ok", "no"):
            agreement = service.respond(code, user.id, user.full_name or user.first_name or "", user.username or "", accept=action == "ok")
            await service.notify_creator(agreement, accepted=action == "ok")
        elif action == "done":
            agreement = service.set_status(code, user.id, "done")
        else:
            await callback.answer()
            return
    except service.AgreementError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(f"agr.status.{agreement.status}"))
    await callback.message.edit_text(service.render_html(agreement), reply_markup=agreement_keyboard(agreement.code, agreement.status, agreement.multi))

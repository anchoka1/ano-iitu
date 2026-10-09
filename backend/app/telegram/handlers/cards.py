"""Кнопки под карточкой и «народная проверка».

👍/👎        — голос «согласен / не согласен» (один голос на человека, можно передумать);
➕ источник  — ответить на карточку сообщением со ссылкой;
📤 картинкой — карточка в PNG для пересылки;
🧒 проще     — пересказ простыми словами;
реакции 👍/👎 на сообщение-карточку тоже считаются голосами (если бот админ в группе).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import BufferedInputFile, CallbackQuery, Message, MessageReactionUpdated, ReactionTypeEmoji

from backend.app.cards.image import render_card_png
from backend.app.core.engine import EngineError, card_from_json, get_verdict_engine
from backend.app.core.fonts import FontNotFound
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.telegram.keyboards import card_keyboard

log = logging.getLogger("verdikt.bot")
router = Router(name="cards")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _vote(check_id: int, user_id: int, value: int) -> tuple[int, int] | None:
    with get_sessionmaker()() as session:
        if repo.get_check(session, check_id) is None:
            return None
        return repo.vote(session, check_id, user_id, value)


@router.callback_query(F.data.startswith("v:"))
async def cb_vote(callback: CallbackQuery, bot: Bot) -> None:
    _, check_id, value = callback.data.split(":")
    counts = await asyncio.to_thread(_vote, int(check_id), callback.from_user.id, int(value))
    if counts is None:
        await callback.answer(t("bot.card_missing"), show_alert=True)
        return
    await callback.answer(t("bot.vote_ok"))
    me = await bot.me()
    private = callback.message.chat.type == ChatType.PRIVATE
    try:
        await callback.message.edit_reply_markup(reply_markup=card_keyboard(int(check_id), *counts, private=private, bot_username=me.username or ""))
    except Exception:  # noqa: BLE001 — «сообщение не изменилось» не ошибка
        pass


@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
    """Реакция на карточку. Telegram присылает их, только если бот — админ группы (или в личке)."""
    if event.user is None:
        return
    emojis = [r.emoji for r in event.new_reaction if isinstance(r, ReactionTypeEmoji)]
    value = 1 if "👍" in emojis else -1 if "👎" in emojis else 0
    if not value:
        return
    with get_sessionmaker()() as session:
        check = repo.find_check_by_message(session, event.chat.id, event.message_id)
        if check is None:
            return
        counts = repo.vote(session, check.id, event.user.id, value)
        check_id = check.id
    me = await bot.me()
    try:
        await bot.edit_message_reply_markup(
            chat_id=event.chat.id, message_id=event.message_id,
            reply_markup=card_keyboard(check_id, *counts, private=event.chat.type == ChatType.PRIVATE, bot_username=me.username or ""),
        )
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("src:"))
async def cb_source_hint(callback: CallbackQuery) -> None:
    await callback.answer(t("bot.src_hint"), show_alert=True)


@router.message(F.reply_to_message.from_user.is_bot.is_(True), F.text.regexp(URL_RE))
async def on_source_reply(message: Message, bot: Bot) -> None:
    """Ответ на карточку бота сообщением со ссылкой = добавить источник."""
    me = await bot.me()
    if message.reply_to_message.from_user.id != me.id:
        return
    url = URL_RE.search(message.text).group(0)
    with get_sessionmaker()() as session:
        check = repo.find_check_by_message(session, message.chat.id, message.reply_to_message.message_id)
        if check is None:
            await message.reply(t("bot.card_missing"))
            return
        repo.add_user_source(session, check.id, message.from_user.id, url, URL_RE.sub("", message.text).strip())
    await message.reply(t("bot.src_added"))


@router.callback_query(F.data.startswith("img:"))
async def cb_image(callback: CallbackQuery) -> None:
    check_id = int(callback.data.split(":")[1])
    with get_sessionmaker()() as session:
        check = repo.get_check(session, check_id)
    if check is None:
        await callback.answer(t("bot.card_missing"), show_alert=True)
        return
    await callback.answer()
    try:
        png = await asyncio.to_thread(render_card_png, card_from_json(check.card_json))
    except FontNotFound as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return
    await callback.message.answer_photo(BufferedInputFile(png, f"verdikt_{check_id}.png"), caption=t("bot.image_caption"))


@router.callback_query(F.data.startswith("simple:"))
async def cb_simple(callback: CallbackQuery) -> None:
    check_id = int(callback.data.split(":")[1])
    with get_sessionmaker()() as session:
        check = repo.get_check(session, check_id)
    if check is None:
        await callback.answer(t("bot.card_missing"), show_alert=True)
        return
    await callback.answer()
    try:
        text = await get_verdict_engine().simplify(card_from_json(check.card_json), callback.from_user.id)
    except EngineError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return
    await callback.message.reply(f"{t('bot.simple_title')}\n{html.escape(text)}")

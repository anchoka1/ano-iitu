"""Группы: добавление бота в чат и «Индекс чата»."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.services.chat_index import render_index, week_stats

router = Router(name="groups")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def on_bot_membership(event: ChatMemberUpdated) -> None:
    """Бота добавили в группу или удалили из неё."""
    status = event.new_chat_member.status
    active = status in ("member", "administrator")
    with get_sessionmaker()() as session:
        repo.upsert_chat(session, event.chat.id, event.chat.title or "", event.chat.type, active=active)
    if active and event.old_chat_member.status in ("left", "kicked"):
        await event.answer(t("bot.group_welcome"))


@router.message(Command("index"), F.chat.type.in_(GROUP_TYPES))
async def cmd_index(message: Message) -> None:
    with get_sessionmaker()() as session:
        repo.upsert_chat(session, message.chat.id, message.chat.title or "", message.chat.type)
    await message.answer(render_index(week_stats(message.chat.id)))


@router.message(Command("index"))
async def cmd_index_private(message: Message) -> None:
    await message.answer(t("index.group_only"))

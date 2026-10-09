"""Учебные хабы в Telegram: группы с темами (топиками), «Сохрани ответ», повторные вопросы, дайджест, заявки.

В группе с темами бот отвечает в той же теме, где его вызвали (message.answer делает это сам),
и учитывает название темы как контекст. Название темы бот берёт из служебного сообщения
о создании темы — переписку при этом не читает и не хранит.

Команды в группе (привязка и дайджест — только администраторы чата):
  /hub_link <хаб>   — связать группу или текущую тему с хабом каталога
  /hub_unlink       — отвязать
  /digest_on|off    — включить или выключить еженедельный дайджест темы
  /digest           — дайджест прямо сейчас
  /save [вопрос]    — в ответ на удачное сообщение: сохранить в частые вопросы (после подтверждения ментора)
  /faq <вопрос>     — найти сохранённый ответ
В личке: /hubs — каталог, /mentor — заявка ментора, /teacher_verify — подтвердить статус преподавателя.
"""

from __future__ import annotations

import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.core.features import enabled
from backend.app.i18n import t
from backend.app.services import hubs as service
from backend.app.telegram import pending

router = Router(name="hubs")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


def thread_topic(message: Message) -> str:
    """Название темы, где написано сообщение (если бот его знает)."""
    if not enabled("hub_topics") or not message.is_topic_message:
        return ""
    replied = message.reply_to_message
    if replied is not None and replied.forum_topic_created is not None:
        service.remember_topic(message.chat.id, message.message_thread_id, replied.forum_topic_created.name)
        return replied.forum_topic_created.name
    return service.topic_name(message.chat.id, message.message_thread_id)


def _thread(message: Message) -> int | None:
    return message.message_thread_id if message.is_topic_message else None


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:  # noqa: BLE001 — нет прав узнать — считаем, что не админ
        return False
    return member.status in ("creator", "administrator")


def _app_button(text: str, hash_: str) -> InlineKeyboardMarkup | None:
    url = get_settings().webapp_url
    if url.lower().startswith("https://"):
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{url.rstrip('/')}/{hash_}"))]])
    return None


# ------------------------------------------------------------------ темы


@router.message(F.forum_topic_created)
async def on_topic_created(message: Message) -> None:
    service.remember_topic(message.chat.id, message.message_thread_id or 0, message.forum_topic_created.name)


@router.message(F.forum_topic_edited)
async def on_topic_edited(message: Message) -> None:
    if message.forum_topic_edited.name:
        service.remember_topic(message.chat.id, message.message_thread_id or 0, message.forum_topic_edited.name)


# ------------------------------------------------------------------ привязка группы/темы к хабу


@router.message(Command("hub_link"), F.chat.type.in_(GROUP_TYPES))
async def cmd_hub_link(message: Message, command: CommandObject, bot: Bot) -> None:
    if not enabled("hubs"):
        await message.reply(t("cm.feature_off"))
        return
    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply(t("hub.err.chat_admin"))
        return
    query = (command.args or "").strip()
    if not query:
        await message.reply(t("hub.link.usage"))
        return
    found = await asyncio.to_thread(service.find_hub, query)
    if len(found) != 1:
        names = ", ".join(f"{h['title']} ({h['slug']})" for h in found) or "—"
        await message.reply(html.escape(t("hub.link.ambiguous", names=names)))
        return
    thread_id = _thread(message)
    title = message.chat.title or ""
    if thread_id:
        title = f"{title} · {thread_topic(message) or t('hub.topic')}"
    data = await asyncio.to_thread(service.link_chat, message.chat.id, thread_id, found[0]["id"], title, message.from_user.id)
    await message.answer(html.escape(t("hub.link.done_topic" if thread_id else "hub.link.done", hub=data["title"])))


@router.message(Command("hub_unlink"), F.chat.type.in_(GROUP_TYPES))
async def cmd_hub_unlink(message: Message, bot: Bot) -> None:
    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply(t("hub.err.chat_admin"))
        return
    ok = await asyncio.to_thread(service.unlink_chat, message.chat.id, _thread(message))
    await message.answer(t("hub.unlink.done") if ok else t("hub.err.not_linked"))


@router.message(Command("digest_on", "digest_off"), F.chat.type.in_(GROUP_TYPES))
async def cmd_digest_toggle(message: Message, command: CommandObject, bot: Bot) -> None:
    if not enabled("hub_digest"):
        await message.reply(t("cm.feature_off"))
        return
    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply(t("hub.err.chat_admin"))
        return
    on = command.command == "digest_on"
    try:
        await asyncio.to_thread(service.set_digest, message.chat.id, _thread(message), on)
    except service.HubError as exc:
        await message.reply(str(exc))
        return
    # Бот сообщает участникам, что включён дайджест и что именно он сохраняет.
    await message.answer(t("hub.digest.on_notice") if on else t("hub.digest.off_notice"))


@router.message(Command("digest"), F.chat.type.in_(GROUP_TYPES))
async def cmd_digest(message: Message) -> None:
    if not enabled("hub_digest"):
        await message.reply(t("cm.feature_off"))
        return
    text = await asyncio.to_thread(service.digest_text, message.chat.id, _thread(message) or 0)
    await message.answer(text, disable_web_page_preview=True)


# ------------------------------------------------------------------ «Сохрани ответ» и повторные вопросы


@router.message(Command("save"), F.chat.type.in_(GROUP_TYPES))
async def cmd_save(message: Message, command: CommandObject) -> None:
    if not enabled("hub_faq"):
        await message.reply(t("cm.feature_off"))
        return
    target = message.reply_to_message
    if target is None or target.forum_topic_created is not None or not (target.text or target.caption):
        await message.reply(t("hub.save.usage"))
        return
    thread_id = _thread(message)
    link = service.message_link(message.chat.id, target.message_id, thread_id, message.chat.username or "")
    try:
        item = await asyncio.to_thread(service.save_answer, message.chat.id, thread_id, target.message_id, (command.args or "").strip(),
                                       target.text or target.caption or "", message.from_user.id,
                                       target.from_user.id if target.from_user else None, link)
    except service.HubError as exc:
        await message.reply(str(exc))
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("hub.btn.faq_ok"), callback_data=f"faq:ok:{item['id']}"),
                                                    InlineKeyboardButton(text=t("hub.btn.faq_no"), callback_data=f"faq:no:{item['id']}")]])
    await message.reply(html.escape(t("hub.save.pending", hub=item["hub"])), reply_markup=markup)


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq(callback: CallbackQuery, bot: Bot) -> None:
    _, action, post_id = callback.data.split(":", 2)
    chat = callback.message.chat if callback.message else None
    admin = bool(chat and chat.type in GROUP_TYPES and await _is_chat_admin(bot, chat.id, callback.from_user.id))
    try:
        item = await asyncio.to_thread(service.confirm_faq, int(post_id), callback.from_user.id, admin, action == "ok")
    except service.HubError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t("hub.save.confirmed") if item["status"] == "approved" else t("hub.save.rejected"))
    try:
        await callback.message.edit_text(html.escape(t("hub.save.done" if item["status"] == "approved" else "hub.save.declined", title=item["title"][:100])))
    except Exception:  # noqa: BLE001
        pass


async def offer_saved_answer(message: Message, question: str) -> bool:
    """Если вопрос уже разбирали в этом хабе — показать сохранённый ответ со ссылкой. True — показали."""
    if not enabled("hub_faq"):
        return False
    hub = await asyncio.to_thread(service.hub_for_chat, message.chat.id, _thread(message))
    if hub is None:
        return False
    found = await asyncio.to_thread(service.find_similar, hub["hub_id"], question)
    if found is None:
        return False
    e = html.escape
    label = t("hub.faq.teacher_answer") if found["is_teacher"] else t("hub.faq.saved_answer")
    text = f"💡 <b>{e(t('hub.faq.already'))}</b>\n<b>{e(label)}:</b> {e(found['answer'][:1500])}"
    if found["link"]:
        text += f"\n\n<a href=\"{e(found['link'])}\">{e(t('hub.faq.original'))}</a>"
    token = pending.put(pending.Pending(message.from_user.id, question, reply_to=message.message_id))
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("hub.btn.ask_anyway"), callback_data=f"pick:vopros:{token}")]])
    await message.reply(text, reply_markup=markup, disable_web_page_preview=True)
    return True


@router.message(Command("faq"))
async def cmd_faq(message: Message, command: CommandObject) -> None:
    question = (command.args or "").strip()
    if not question:
        await message.reply(t("hub.faq.usage"))
        return
    if message.chat.type in GROUP_TYPES and await offer_saved_answer(message, question):
        return
    await message.reply(t("hub.faq.none"))


# ------------------------------------------------------------------ каталог и заявки в личке


@router.message(Command("hubs"))
async def cmd_hubs(message: Message) -> None:
    if not enabled("hubs"):
        await message.answer(t("cm.feature_off"))
        return
    data = await asyncio.to_thread(service.catalog, message.from_user.id)
    e = html.escape
    lines = [f"📚 <b>{e(t('hub.catalog.title'))}</b>", ""]
    for h in data["cross"]:
        lines.append(f"{h['emoji']} {e(h['title'])}")
    for group in data["courses"]:
        lines += ["", f"<b>{e(group['title'])}</b>"] + [f"{h['emoji']} {e(h['title'])}" for h in group["hubs"]]
    if not data["courses"]:
        lines += ["", e(t("hub.catalog.no_disciplines"))]
    lines += ["", e(t("hub.catalog.hint"))]
    await message.answer("\n".join(lines), reply_markup=_app_button(t("hub.btn.open_catalog"), "#hubs") if message.chat.type == ChatType.PRIVATE else None)


@router.message(Command("mentor"), F.chat.type == ChatType.PRIVATE)
async def cmd_mentor(message: Message, command: CommandObject) -> None:
    if not enabled("mentors"):
        await message.answer(t("cm.feature_off"))
        return
    about = (command.args or "").strip()
    if len(about) < 10:
        await message.answer(t("hub.mentor.usage"), reply_markup=_app_button(t("hub.btn.open_catalog"), "#hubs"))
        return
    try:
        await service.apply("mentor", message.from_user.id, message.from_user.full_name or "", {"about": about})
    except service.HubError as exc:
        await message.answer(str(exc))
        return
    await message.answer(t("hub.mentor.sent"))


@router.message(Command("teacher_verify"), F.chat.type == ChatType.PRIVATE)
async def cmd_teacher_verify(message: Message, command: CommandObject) -> None:
    if not enabled("teacher_cabinet"):
        await message.answer(t("cm.feature_off"))
        return
    about = (command.args or "").strip()
    if len(about) < 5:
        await message.answer(t("hub.teacher.usage"))
        return
    try:
        await service.apply("teacher", message.from_user.id, message.from_user.full_name or "", {"about": about})
    except service.HubError as exc:
        await message.answer(str(exc))
        return
    await message.answer(t("hub.teacher.sent"))

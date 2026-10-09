"""«Мой план» в боте: /plan, /task, «Сделать задачей», задача из группы, свидетель обещания.

/plan              — план на сегодня и неделю, предложенные задачи, напоминания вкл/выкл
/task <текст>      — быстро добавить: «сдать лабу по Python в пятницу до 18:00» → карточка → «Добавить»
/task в группе     — ответом на сообщение: задача уйдёт в ЛИЧНЫЙ план (подтверждение — в личке),
                     в общий чат бот ничего не пишет
/plan_ics          — файл календаря .ics;   /plan_delete — удалить все задачи и данные планера

callback_data: mktask:<токен>  pl:add:<токен>  pl:cancel  pl:done:<id>  pl:move:<id>:<дни>  pl:frog:<id>
               pl:sug:<id>:ok|no  pl:sugs  pl:remind:1|0  pl:vimg  pl:del:yes|no  wit:ok|no:<токен>
"""

from __future__ import annotations

import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.core.features import enabled
from backend.app.i18n import t
from backend.app.services import planner, prefs
from backend.app.telegram import pending

router = Router(name="planner")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


class WaitTask(StatesGroup):
    text = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _course(user_id: int) -> str:
    from backend.app.services.campus import get_user_course

    return get_user_course(user_id)


def draft_html(draft: dict) -> str:
    e = html.escape
    lines = [f"📌 <b>{e(t('pl.draft.title'))}</b>", "", f"<b>{e(draft['title'])}</b>"]
    if draft["due_date"]:
        lines.append(f"🗓 {e(draft['due_date'])}" + (f" {e(draft['due_time'])}" if draft["due_time"] else ""))
    else:
        lines.append(f"🗓 {e(t('pl.draft.no_date'))}")
    if draft["subject"]:
        lines.append(f"📚 {e(draft['subject'])}")
    if draft["priority"] >= 2:
        lines.append(f"❗ {e(t('pl.draft.urgent'))}")
    lines += ["", f"<i>{e(t('pl.draft.hint'))}</i>"]
    return "\n".join(lines)


def _subjects(user_id: int) -> list[str]:
    from sqlalchemy import select

    from backend.app.db.base import get_sessionmaker
    from backend.app.db.models_campus import Hub, HubMember

    with get_sessionmaker()() as session:
        ids = select(HubMember.hub_id).where(HubMember.user_id == user_id)
        return [h.title for h in session.scalars(select(Hub).where(Hub.id.in_(ids), Hub.kind == "discipline"))]


async def show_draft(message: Message, user_id: int, text: str, reply_to: int | None = None) -> None:
    draft = await asyncio.to_thread(planner.parse_task_text, text, None, await asyncio.to_thread(_subjects, user_id))
    token = pending.put(pending.Pending(user_id, text))
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("pl.btn.add"), f"pl:add:{token}"), _btn(t("pl.btn.cancel"), "pl:cancel")]])
    await message.answer(draft_html(draft), reply_markup=markup)


def plan_html(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    e = html.escape
    today = planner.view(user_id, "today")
    week = planner.view(user_id, "week")
    suggestions = planner.list_suggestions(user_id, _course(user_id))
    lines = [f"🗓 <b>{e(t('pl.plan.title'))}</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    if today["frog"]:
        lines.append(f"🐸 <b>{e(t('pl.frog.label'))}:</b> {e(today['frog']['title'])}")
    if today["overdue"]:
        lines.append(f"<b>{e(t('pl.plan.overdue'))}</b>")
        for x in today["overdue"][:5]:
            lines.append(f"• {e(x['title'])} ({e(x['due_date'])})")
            rows.append([_btn(f"✅ {x['title'][:24]}", f"pl:done:{x['id']}"), _btn(t("pl.btn.tomorrow"), f"pl:move:{x['id']}:1")])
    lines.append(f"<b>{e(t('pl.plan.today'))}</b>")
    if today["today"]:
        for x in today["today"][:8]:
            lines.append(f"• {e(x['title'])}" + (f" — {e(x['due_time'])}" if x["due_time"] else ""))
            rows.append([_btn(f"✅ {x['title'][:24]}", f"pl:done:{x['id']}"), _btn(t("pl.btn.tomorrow"), f"pl:move:{x['id']}:1")])
    else:
        lines.append(e(t("pl.plan.today_empty")))
    upcoming = [(d["date"], x) for d in week["days"][1:] for x in d["tasks"] if x["status"] != "done"]
    if upcoming:
        lines += ["", f"<b>{e(t('pl.plan.week'))}</b>"] + [f"• {e(d)} — {e(x['title'])}" for d, x in upcoming[:8]]
    if today["done"]:
        lines += ["", e(t("pl.plan.done_today", n=len(today["done"])))]
    if suggestions:
        lines += ["", e(t("pl.plan.suggested", n=len(suggestions)))]
        rows.append([_btn(t("pl.btn.suggestions", n=len(suggestions)), "pl:sugs")])
    remind = prefs.get_bool(user_id, "plan.remind")
    rows.append([_btn(t("pl.btn.remind_off") if remind else t("pl.btn.remind_on"), f"pl:remind:{0 if remind else 1}")])
    lines += ["", f"<i>{e(t('pl.plan.hint'))}</i>"]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------------------------------------------------------ команды


@router.message(Command("plan"), F.chat.type == ChatType.PRIVATE)
async def cmd_plan(message: Message, state: FSMContext) -> None:
    if not enabled("planner"):
        await message.answer(t("cm.feature_off"))
        return
    await state.clear()
    text, markup = await asyncio.to_thread(plan_html, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(Command("plan"))
async def cmd_plan_group(message: Message) -> None:
    await message.reply(t("pl.private_only"))


@router.message(Command("task"), F.chat.type == ChatType.PRIVATE)
async def cmd_task(message: Message, command: CommandObject, state: FSMContext) -> None:
    if not enabled("planner"):
        await message.answer(t("cm.feature_off"))
        return
    text = (command.args or "").strip()
    if message.reply_to_message is not None and message.reply_to_message.forum_topic_created is None:
        replied = message.reply_to_message.text or message.reply_to_message.caption or ""
        text = f"{replied} {text}".strip()
    if not text:
        await state.set_state(WaitTask.text)
        await message.answer(t("pl.ask_text"))
        return
    await show_draft(message, message.from_user.id, text)


@router.message(Command("task"), F.chat.type.in_(GROUP_TYPES))
async def cmd_task_group(message: Message, command: CommandObject, bot: Bot) -> None:
    """Из группы: ответ на сообщение командой → задача в личный план. В общий чат — ничего лишнего."""
    if not enabled("planner"):
        return
    target = message.reply_to_message
    text = (command.args or "").strip()
    if target is not None and target.forum_topic_created is None:
        text = f"{target.text or target.caption or ''} {text}".strip()
    if not text:
        return
    user = message.from_user
    draft = planner.parse_task_text(text)
    token = pending.put(pending.Pending(user.id, text))
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("pl.btn.add"), f"pl:add:{token}"), _btn(t("pl.btn.cancel"), "pl:cancel")]])
    try:
        await bot.send_message(user.id, draft_html(draft) + "\n\n" + html.escape(t("pl.from_group", chat=message.chat.title or "")), reply_markup=markup)
    except TelegramAPIError:
        # Человек не нажимал «Старт» у бота — без этого написать ему в личку нельзя.
        me = await bot.me()
        await message.reply(t("pl.need_start", bot=me.username or ""))


@router.message(WaitTask.text, F.chat.type == ChatType.PRIVATE, F.text)
async def on_task_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    await show_draft(message, message.from_user.id, message.text)


@router.message(Command("plan_ics"), F.chat.type == ChatType.PRIVATE)
async def cmd_plan_ics(message: Message) -> None:
    if not enabled("planner"):
        return
    data = await asyncio.to_thread(planner.export_ics, message.from_user.id)
    await message.answer_document(BufferedInputFile(data.encode("utf-8"), "moi_plan.ics"), caption=t("pl.ics_caption"))


@router.message(Command("plan_delete"), F.chat.type == ChatType.PRIVATE)
async def cmd_plan_delete(message: Message) -> None:
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("pl.btn.delete_yes"), "pl:del:yes"), _btn(t("pl.btn.delete_no"), "pl:del:no")]])
    await message.answer(t("pl.delete_confirm"), reply_markup=markup)


# ------------------------------------------------------------------ кнопки


@router.callback_query(F.data.startswith("mktask:"))
async def cb_make_task(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]
    item = pending.pop(token, callback.from_user.id)
    if item is None:
        await callback.answer(t("bot.pick_expired"), show_alert=True)
        return
    await callback.answer()
    await show_draft(callback.message, callback.from_user.id, item.text)


@router.callback_query(F.data.startswith("pl:"))
async def cb_planner(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    action = parts[1]
    user = callback.from_user
    try:
        if action == "add":
            item = pending.pop(parts[2], user.id)
            if item is None:
                await callback.answer(t("bot.pick_expired"), show_alert=True)
                return
            draft = planner.parse_task_text(item.text, None, await asyncio.to_thread(_subjects, user.id))
            task = await asyncio.to_thread(planner.create_task, user.id, draft.pop("title"), user.first_name or "", source="chat", **draft)
            await callback.answer(t("pl.added"))
            await callback.message.edit_text(html.escape(t("pl.added_full", title=task["title"], when=task["due_date"] or t("pl.draft.no_date"))))
        elif action == "cancel":
            await callback.answer()
            await callback.message.edit_text(t("bot.cancelled"))
        elif action == "done":
            task = await asyncio.to_thread(planner.complete_task, user.id, int(parts[2]))
            await planner.flush_witness_queue()
            await callback.answer(t("pl.done_toast", title=task["title"][:40]))
            await _refresh(callback)
        elif action == "move":
            task = await asyncio.to_thread(planner.move_task, user.id, int(parts[2]), int(parts[3]) if len(parts) > 3 else 1)
            await callback.answer(t("pl.moved_toast", date=task["due_date"]))
            await _refresh(callback)
        elif action == "frog":
            task = await asyncio.to_thread(planner.set_frog, user.id, int(parts[2]))
            await callback.answer()
            await callback.message.edit_text(html.escape(t("pl.frog.set", title=task["title"])))
        elif action == "sugs":
            await callback.answer()
            items = await asyncio.to_thread(planner.list_suggestions, user.id, _course(user.id))
            if not items:
                await callback.message.answer(t("pl.sug.empty"))
            for s in items[:10]:
                text = f"💡 <b>{html.escape(s['title'])}</b>\n{html.escape(s['source_label'])}" + (f" · {s['due_date']}" if s["due_date"] else "")
                await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    _btn(t("pl.btn.accept"), f"pl:sug:{s['id']}:ok"), _btn(t("pl.btn.dismiss"), f"pl:sug:{s['id']}:no")]]))
        elif action == "sug":
            if parts[3] == "ok":
                task = await asyncio.to_thread(planner.accept_suggestion, user.id, int(parts[2]))
                await callback.answer(t("pl.added"))
                await callback.message.edit_text(html.escape(t("pl.added_full", title=task["title"], when=task["due_date"] or t("pl.draft.no_date"))))
            else:
                await asyncio.to_thread(planner.dismiss_suggestion, user.id, int(parts[2]))
                await callback.answer()
                await callback.message.edit_text(t("pl.sug.dismissed"))
        elif action == "remind":
            await asyncio.to_thread(prefs.set_value, user.id, "plan.remind", parts[2])
            await callback.answer(t("pl.remind_on_toast") if parts[2] == "1" else t("pl.remind_off_toast"), show_alert=True)
            await _refresh(callback)
        elif action == "vimg":
            from backend.app.cards.image import render_card_png

            await callback.answer()
            card = await asyncio.to_thread(planner.week_verdict_card, user.id)
            png = await asyncio.to_thread(render_card_png, card)
            await callback.message.answer_photo(BufferedInputFile(png, "vedikt_nedeli.png"), caption=t("pl.verdict.share_caption"))
        elif action == "del":
            await callback.answer()
            if parts[2] == "yes":
                await asyncio.to_thread(planner.delete_all, user.id)
                await callback.message.edit_text(t("pl.deleted"))
            else:
                await callback.message.edit_text(t("bot.cancelled"))
        else:
            await callback.answer()
    except planner.PlannerError as exc:
        await callback.answer(str(exc), show_alert=True)


async def _refresh(callback: CallbackQuery) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        return
    text, markup = await asyncio.to_thread(plan_html, callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 — напоминание (не карточка плана) или текст не изменился
        await callback.message.answer(text, reply_markup=markup)


# ------------------------------------------------------------------ свидетель обещания (deep link wit_ТОКЕН)


async def show_witness_invite(message: Message, token: str) -> None:
    info = await asyncio.to_thread(planner.witness_info, token)
    if info is None or info["state"] != "invited":
        await message.answer(t("pl.err.not_found"))
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("pl.btn.witness_ok"), f"wit:ok:{token}"), _btn(t("pl.btn.witness_no"), f"wit:no:{token}")]])
    await message.answer(html.escape(t("pl.witness.invite", name=info["owner_name"] or "Друг", title=info["title"],
                                       due=info["due_date"] or t("pl.draft.no_date"))), reply_markup=markup)


@router.callback_query(F.data.startswith("wit:"))
async def cb_witness(callback: CallbackQuery, bot: Bot) -> None:
    _, action, token = callback.data.split(":", 2)
    user = callback.from_user
    try:
        result = await asyncio.to_thread(planner.witness_answer, token, user.id, user.first_name or "", action == "ok")
    except planner.PlannerError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(html.escape(t("pl.witness.accepted" if result["state"] == "accepted" else "pl.witness.declined",
                                                   title=result["title"])))
    try:
        await bot.send_message(result["owner_id"], html.escape(t(f"pl.witness.owner_{result['state']}", name=user.first_name or "", title=result["title"])))
    except TelegramAPIError:
        pass

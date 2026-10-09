"""Функции волн 1–3 в боте.

/rumor            — «Слух недели» (карточку удобно переслать в чат группы)
/gpa 72 75        — сколько нужно на финале: рейтинг допуска 72%, цель 75%
/syllabus         — разбор силлабуса: PDF, DOCX, фото или текст → дедлайны в план, привязка к хабу
/opros [вопрос]   — «Опрос после пары»: анонимный вопрос группе (ответы — в личке у бота)
/opros_itogi      — итоги моих опросов (видны, когда ответов не меньше пяти)
/pulse            — «Пульс МУИТ»: вопрос недели в один тап
/reiting          — в группе: участвовать в «Рейтинге групп» (вкл/выкл), в личке — таблица
/myid             — мой Telegram id (владельцу бота — для OWNER_IDS)
Deep link: poll_ID, ack_ID, slot_ID (консультации преподавателя), hub_ID.
"""

from __future__ import annotations

import asyncio
import html
import json

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend.app.core.engine import EngineError, card_from_json, get_verdict_engine
from backend.app.core.config import get_settings
from backend.app.core.features import enabled
from backend.app.i18n import t
from backend.app.services import campus, community, polls
from backend.app.telegram.media import MediaError, consent_given, consent_keyboard, has_file, read_file

router = Router(name="campus")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


class WaitCampus(StatesGroup):
    syllabus = State()
    poll_answer = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _off(message: Message, key: str) -> bool:
    return not enabled(key)


# ------------------------------------------------------------------ Слух недели


@router.message(Command("rumor"))
async def cmd_rumor(message: Message) -> None:
    if _off(message, "rumor_week"):
        await message.answer(t("cm.feature_off"))
        return
    from backend.app.core.scheduler_campus import rumor_html

    rumor = await asyncio.to_thread(campus.rumor_of_week)
    if rumor is None:
        await message.answer(t("cm.rumor.none"))
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("btn.share_image"), f"img:{rumor['check_id']}")]])
    await message.answer(rumor_html(rumor), reply_markup=markup, disable_web_page_preview=True)


# ------------------------------------------------------------------ GPA


@router.message(Command("gpa"))
async def cmd_gpa(message: Message, command: CommandObject) -> None:
    if _off(message, "gpa"):
        await message.answer(t("cm.feature_off"))
        return
    args = (command.args or "").replace(",", ".").split()
    try:
        if len(args) >= 3:  # /gpa РК1 РК2 цель
            r = campus.final_needed(0, float(args[2]), float(args[0]), float(args[1]))
        else:               # /gpa допуск [цель]
            r = campus.final_needed(float(args[0]), float(args[1]) if len(args) > 1 else 50.0)
    except (IndexError, ValueError):
        await message.answer(t("cm.gpa.usage"))
        return
    except campus.CampusError as exc:
        await message.answer(str(exc))
        return
    if not r["admitted"]:
        verdict = t("cm.gpa.not_admitted", adm=r["admission"], min=r["admission_min"])
    elif r["already"]:
        verdict = t("cm.gpa.already", target=r["target"])
    elif not r["reachable"]:
        verdict = t("cm.gpa.unreachable", target=r["target"], max=r["max_total"])
    else:
        verdict = t("cm.gpa.need", need=r["need"], target=r["target"])
    lines = [f"🧮 <b>{html.escape(t('cm.gpa.title'))}</b>", "", html.escape(verdict),
             html.escape(t("cm.gpa.pass", need=r["need_for_pass"], pass_total=r["pass_total"])),
             html.escape(t("cm.gpa.formula", w=r["final_weight"]))]
    if r["target_letter"]:
        lines.append(html.escape(t("cm.gpa.target_letter", letter=r["target_letter"]["letter"], points=r["target_letter"]["points"])))
    if r.get("letters") and r["admitted"]:
        lines += ["", f"<b>{html.escape(t('cm.gpa.letters'))}</b>"]
        lines += [f"{html.escape(x['letter'])} ({x['points']}) — {x['need']}%" for x in r["letters"]]
    lines.append(html.escape(r["stipend_note"]))
    lines += ["", f'<a href="{html.escape(r["source"]["url"])}">{html.escape(r["source"]["title"])}</a> · {html.escape(r["source"]["checked"])}',
              f"<i>{html.escape(t('cm.gpa.privacy'))}</i>"]
    await message.answer("\n".join(lines), disable_web_page_preview=True)


# ------------------------------------------------------------------ Разбор силлабуса (расширение «Чека»)


async def run_syllabus(message: Message, user_id: int, text: str, attachments=None) -> None:
    """Разбор → сохраняем только структуру → кнопка «В план» (дедлайны с датами, напоминание за день)."""
    from backend.app.services import syllabus as syllabus_service

    progress = await message.answer(t("cm.syllabus.progress"))
    try:
        card = await get_verdict_engine().parse_syllabus(text, user_id, attachments or [])
    except EngineError as exc:
        await progress.edit_text(html.escape(t("file.err.scan_model") if attachments and not text else str(exc)))
        return
    if not (card.deadlines or card.grading or card.retake_rules or card.absence_rules):
        await progress.edit_text(t("sy.err.nothing"))
        return
    saved = await asyncio.to_thread(syllabus_service.save, user_id, card)
    rows = []
    body = campus.syllabus_html(card)
    if saved["dated"] and enabled("planner"):
        # Дедлайны с датами сразу попадают в «План» — как и при загрузке в приложении.
        result = await asyncio.to_thread(syllabus_service.to_plan, user_id, saved["id"])
        key = "cm.syllabus.added_remind" if result["reminders_on"] else "cm.syllabus.added"
        body += "\n\n" + html.escape(t(key, n=result["added"]))
    settings = get_settings()
    if settings.webapp_is_https:
        rows.append([InlineKeyboardButton(text=t("cm.syllabus.btn_open"),
                                          web_app=WebAppInfo(url=f"{settings.webapp_url.rstrip('/')}/?subject=s{saved['id']}"))])
    if saved["hub_title"]:
        body += "\n\n" + html.escape(t("cm.syllabus.hub", hub=saved["hub_title"]))
    await progress.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


async def offer_syllabus(message: Message, user_id: int, text: str) -> None:
    """Из «Чека»: текст похож на силлабус — предложить разбор."""
    from backend.app.telegram import pending

    token = pending.put(pending.Pending(user_id, text))
    await message.answer(t("cm.syllabus.offer"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn(t("cm.syllabus.btn_parse"), f"syl:from:{token}")]]))


@router.message(Command("syllabus", "sillabus"), F.chat.type == ChatType.PRIVATE)
async def cmd_syllabus(message: Message, command: CommandObject, state: FSMContext, bot: Bot) -> None:
    if _off(message, "syllabus"):
        await message.answer(t("cm.feature_off"))
        return
    text = (command.args or "").strip()
    if not text:
        await state.set_state(WaitCampus.syllabus)
        await message.answer(t("cm.syllabus.ask"))
        return
    await run_syllabus(message, message.from_user.id, text)


@router.message(WaitCampus.syllabus, F.chat.type == ChatType.PRIVATE)
async def on_syllabus(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    if message.text and message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    if has_file(message) and not consent_given(message.from_user.id):
        await message.answer(t("consent.text"), reply_markup=consent_keyboard("-"))
        return
    try:
        attachments, doc_text = await read_file(bot, message)
    except MediaError as exc:
        await message.answer(t(f"bot.file.{exc}"))
        return
    await run_syllabus(message, message.from_user.id, f"{message.text or message.caption or ''}\n{doc_text}".strip(), attachments)


@router.callback_query(F.data.startswith("syl:"))
async def cb_syllabus(callback: CallbackQuery) -> None:
    from backend.app.services import syllabus as syllabus_service
    from backend.app.telegram import pending

    _, action, token = callback.data.split(":", 2)
    if action == "from":
        item = pending.pop(token, callback.from_user.id)
        if item is None:
            await callback.answer(t("bot.pick_expired"), show_alert=True)
            return
        await callback.answer()
        await run_syllabus(callback.message, callback.from_user.id, item.text, item.attachments)
        return
    try:
        result = await asyncio.to_thread(syllabus_service.to_plan, callback.from_user.id, int(token))
    except (syllabus_service.SyllabusError, ValueError):
        await callback.answer(t("bot.pick_expired"), show_alert=True)
        return
    key = "cm.syllabus.added_remind" if result["reminders_on"] else "cm.syllabus.added"
    await callback.answer(t(key, n=result["added"]), show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ Опрос после пары


def _poll_link(username: str, poll_id: int) -> str:
    return f"https://t.me/{username}?start=poll_{poll_id}"


@router.message(Command("opros"))
async def cmd_poll(message: Message, command: CommandObject, bot: Bot) -> None:
    if _off(message, "class_poll"):
        await message.answer(t("cm.feature_off"))
        return
    from backend.app.telegram.handlers.onboarding import get_profile

    profile = await asyncio.to_thread(get_profile, message.from_user.id)
    if not profile or profile.get("role") != "teacher":
        await message.reply(t("cm.poll.teacher_only"))
        return
    question = (command.args or "").strip() or t("cm.poll.default_question")
    chat_id = message.chat.id if message.chat.type in GROUP_TYPES else None
    poll = await asyncio.to_thread(polls.create, "class", question, message.from_user.id, None, None, None, chat_id)
    me = await bot.me()
    link = _poll_link(me.username or "", poll["id"])
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("cm.poll.btn_answer"), url=link)]])
    await message.answer(html.escape(t("cm.poll.posted", question=question, n=poll["min_answers"])), reply_markup=markup)
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(html.escape(t("cm.poll.share", link=link)), disable_web_page_preview=True)


@router.message(Command("opros_itogi"), F.chat.type == ChatType.PRIVATE)
async def cmd_poll_results(message: Message) -> None:
    items = await asyncio.to_thread(polls.my_polls, message.from_user.id)
    if not items:
        await message.answer(t("cm.poll.none"))
        return
    rows = [[_btn(f"{p['question'][:40]} · {p['answers']}", f"poll:res:{p['id']}")] for p in items[:10]]
    await message.answer(t("cm.poll.mine"), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def results_html(data: dict) -> str:
    e = html.escape
    lines = [f"📊 <b>{e(data['question'])}</b>", e(t("cm.poll.answers", n=data["answers"]))]
    if not data.get("results"):
        lines.append(e(t("cm.poll.hidden", n=data["min_answers"])))
        return "\n".join(lines)
    res = data["results"]
    if "options" in res:
        lines += [f"• {e(o['text'])} — {o['percent']}% ({o['count']})" for o in res["options"]]
    else:
        if res["topics"]:
            lines += ["", f"<b>{e(t('cm.poll.topics'))}</b> " + e(", ".join(f"{x['word']} ({x['count']})" for x in res["topics"]))]
        lines += ["", f"<b>{e(t('cm.poll.texts'))}</b>"] + [f"— {e(x[:200])}" for x in res["texts"][:20]]
    return "\n".join(lines)


async def show_poll(message: Message, poll_id: int, user_id: int, state: FSMContext) -> None:
    try:
        data = await asyncio.to_thread(polls.get, poll_id, user_id)
    except polls.PollError as exc:
        await message.answer(str(exc))
        return
    if data["answered"]:
        await message.answer(t("cm.poll.err.already"))
        return
    if not data["open"]:
        await message.answer(t("cm.poll.err.closed"))
        return
    intro = html.escape(t("cm.poll.anon_note", n=data["min_answers"]))
    if data["options"]:
        rows = [[_btn(o, f"poll:a:{poll_id}:{i}")] for i, o in enumerate(data["options"])]
        await message.answer(f"📊 <b>{html.escape(data['question'])}</b>\n\n{intro}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    await state.set_state(WaitCampus.poll_answer)
    await state.update_data(poll_id=poll_id)
    await message.answer(f"📊 <b>{html.escape(data['question'])}</b>\n\n{intro}\n\n{html.escape(t('cm.poll.write_answer'))}")


@router.message(WaitCampus.poll_answer, F.chat.type == ChatType.PRIVATE, F.text)
async def on_poll_answer(message: Message, state: FSMContext) -> None:
    from backend.app.university import crisis

    st = await state.get_data()
    await state.clear()
    if message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    try:
        result = await asyncio.to_thread(polls.answer, st.get("poll_id", 0), message.from_user.id, None, message.text)
    except polls.PollError as exc:
        await message.answer(str(exc))
        return
    await message.answer(t("cm.poll.thanks"))
    if result.get("crisis"):
        await message.answer(crisis.support_html(result["crisis"]), disable_web_page_preview=True)


@router.message(Command("pulse"))
async def cmd_pulse(message: Message) -> None:
    if _off(message, "pulse"):
        await message.answer(t("cm.feature_off"))
        return
    data = await asyncio.to_thread(polls.current_pulse, None, message.from_user.id)
    e = html.escape
    text = f"📊 <b>{e(t('cm.pulse.title'))}</b> · {e(data['subject'])}\n\n{e(data['question'])}"
    prev = data.get("previous")
    if prev and prev.get("results"):
        text += "\n\n" + e(t("cm.pulse.last_week")) + "\n" + results_html(prev)
    if data["answered"]:
        await message.answer(text + "\n\n" + e(t("cm.poll.err.already")))
        return
    rows = [[_btn(o, f"poll:a:{data['id']}:{i}")] for i, o in enumerate(data["options"])]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("poll:"))
async def cb_poll(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    try:
        if parts[1] == "a":
            data = await asyncio.to_thread(polls.answer, int(parts[2]), callback.from_user.id, int(parts[3]))
            await callback.answer(t("cm.poll.thanks"))
            text = results_html(data) if data.get("results") else html.escape(t("cm.poll.thanks_hidden", n=data["answers"], need=data["min_answers"]))
            await callback.message.edit_text(text)
        elif parts[1] == "res":
            data = await asyncio.to_thread(polls.results, int(parts[2]), callback.from_user.id)
            await callback.answer()
            await callback.message.answer(results_html(data))
        else:
            await callback.answer()
    except polls.PollError as exc:
        await callback.answer(str(exc), show_alert=True)


# ------------------------------------------------------------------ Радар разводов (из карточки «Развод?»)


@router.callback_query(F.data.startswith("radar:"))
async def cb_radar(callback: CallbackQuery) -> None:
    from backend.app.db import repo
    from backend.app.db.base import get_sessionmaker
    from backend.app.security.masking import mask_sensitive

    check_id = int(callback.data.split(":")[1])
    with get_sessionmaker()() as session:
        check = repo.get_check(session, check_id)
    if check is None or check.user_id != callback.from_user.id:
        await callback.answer(t("bot.card_missing"), show_alert=True)
        return
    card = card_from_json(check.card_json)
    try:
        post = await asyncio.to_thread(community.create_post, "radar", callback.from_user.id, callback.from_user.first_name or "",
                                       card.title, mask_sensitive(check.input_text[:800]), {"check_id": check_id})
    except community.CommunityError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await community.announce_pending(post["id"])
    await callback.answer(t("cm.radar.sent"), show_alert=True)


# ------------------------------------------------------------------ Рейтинг групп


@router.message(Command("reiting"), F.chat.type.in_(GROUP_TYPES))
async def cmd_rating_group(message: Message, command: CommandObject, bot: Bot) -> None:
    if _off(message, "group_rating"):
        await message.reply(t("cm.feature_off"))
        return
    from backend.app.telegram.handlers.hubs import _is_chat_admin

    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply(t("hub.err.chat_admin"))
        return
    on = (command.args or "").strip().lower() not in ("off", "выкл", "0", "нет")
    await asyncio.to_thread(campus.set_rating_opt_in, message.chat.id, message.chat.title or "", message.chat.type, on)
    await message.answer(t("cm.rating.on") if on else t("cm.rating.off"))


@router.message(Command("reiting"))
async def cmd_rating(message: Message) -> None:
    if _off(message, "group_rating"):
        await message.answer(t("cm.feature_off"))
        return
    rows = await asyncio.to_thread(campus.group_rating)
    if not rows:
        await message.answer(t("cm.rating.empty"))
        return
    lines = [f"🏆 <b>{html.escape(t('cm.rating.title'))}</b>", ""]
    lines += [f"{r['place']}. {html.escape(r['title'])} — {r['score']} ({html.escape(t('cm.rating.row', checks=r['checks'], kept=r['kept']))})" for r in rows[:15]]
    lines += ["", f"<i>{html.escape(t('cm.rating.note'))}</i>"]
    await message.answer("\n".join(lines))


# ------------------------------------------------------------------ «Прочитал(а)»: правила ИИ и объявления


@router.callback_query(F.data.startswith("ack:"))
async def cb_ack(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    try:
        await asyncio.to_thread(community.ack, post_id, callback.from_user.id, callback.from_user.full_name or "")
    except community.CommunityError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t("cm.ack.done"), show_alert=True)


def post_html(post: dict) -> str:
    e = html.escape
    lines = [f"📌 <b>{e(post['title'])}</b>"]
    data = post.get("data", {})
    if post["kind"] == "ai_rules":
        if data.get("allowed"):
            lines += ["", f"✅ <b>{e(t('cm.ai.allowed'))}</b>"] + [f"• {e(x)}" for x in data["allowed"]]
        if data.get("forbidden"):
            lines += ["", f"⛔ <b>{e(t('cm.ai.forbidden'))}</b>"] + [f"• {e(x)}" for x in data["forbidden"]]
    if post.get("body"):
        lines += ["", e(post["body"][:2000])]
    if post.get("author_name"):
        lines += ["", f"<i>{e(post['author_name'])}</i>"]
    return "\n".join(lines)


async def show_ack_post(message: Message, post_id: int) -> None:
    try:
        post = await asyncio.to_thread(community.get_post, post_id, message.from_user.id)
    except community.CommunityError as exc:
        await message.answer(str(exc))
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("hub.btn.ack"), f"ack:{post_id}")]]) if not post.get("acked") else None
    await message.answer(post_html(post), reply_markup=markup)


# ------------------------------------------------------------------ Консультации (deep link slot_<id преподавателя>)


async def show_slots(message: Message, owner_id: int) -> None:
    slots = [s for s in await asyncio.to_thread(community.list_slots, message.from_user.id, owner_id) if not s["taken"] or s["mine"]]
    if not slots:
        await message.answer(t("cm.slot.none"))
        return
    rows = [[_btn(("✅ " if s["mine"] else "") + s["start"].replace("T", " ") + (f" · {s['place']}" if s["place"] else ""), f"slot:b:{s['id']}")]
            for s in slots[:20]]
    await message.answer(html.escape(t("cm.slot.pick", name=slots[0]["owner_name"])), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("slot:b:"))
async def cb_slot(callback: CallbackQuery) -> None:
    slot_id = int(callback.data.split(":")[2])
    try:
        slot = await community.book_slot(slot_id, callback.from_user.id, callback.from_user.full_name or "")
    except community.CommunityError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t("cm.slot.booked", when=slot["start"].replace("T", " ")), show_alert=True)


async def show_hub(message: Message, hub_id: int) -> None:
    from backend.app.services import hubs

    try:
        data = await asyncio.to_thread(hubs.page, hub_id, message.from_user.id)
    except hubs.HubError as exc:
        await message.answer(str(exc))
        return
    e = html.escape
    lines = [f"{data['emoji']} <b>{e(data['title'])}</b> · {e(data['course_title'])}", e(data["description"])]
    if data["chat_url"]:
        lines.append(f'<a href="{e(data["chat_url"])}">{e(t("hub.chat_link"))}</a>')
    for d in data["deadlines"][:5]:
        lines.append(f"🗓 {e(d['data'].get('due_date', ''))} — {e(d['title'])}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


def parse_int(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def dump(data) -> str:
    return json.dumps(data, ensure_ascii=False)

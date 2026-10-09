"""/gruppa — «Группы и потоки» (университетская версия «Семьи»).

В личке: список моих групп, создание (тип → название), карточка группы с настройками,
объявление (с предпросмотром и проверкой ясности) и договорённость с группой.
В чате Telegram: /gruppa привязывает к чату группу (создаёт, если её нет) и показывает
кнопку «Вступить» — так одногруппники вступают в один клик.
Приглашение по ссылке: t.me/бот?start=grp_ТОКЕН (обрабатывается в common.py).

callback_data:
  grp:list  grp:new  grp:kind:<kind>  grp:open:<id>  grp:join:<token>  grp:no
  grp:alerts:<id>:0|1  grp:posts:<id>:0|1  grp:leave:<id>
  grp:post:<id>  grp:agr:<id>  grp:send  grp:check  grp:cancel
"""

from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.core.engine import CheckInput, EngineError, get_verdict_engine
from backend.app.cards.render import render_card_html
from backend.app.i18n import t
from backend.app.services import circles as service

router = Router(name="circles")
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


class WaitCircle(StatesGroup):
    title = State()
    post = State()
    agreement = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _invite_link(message_or_cb, token: str) -> str:
    me = await message_or_cb.bot.me()
    return f"https://t.me/{me.username}?start=grp_{token}"


def list_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    items = service.list_for_user(user_id)
    lines = [t("grp.title"), "", html.escape(t("grp.intro")), ""]
    if not items:
        lines.append(html.escape(t("grp.none")))
    rows = [[_btn(f"{c['kind_label'].split(' ')[0]} {c['title']} · {c['members_count']}"[:60], f"grp:open:{c['id']}")] for c in items]
    rows.append([_btn(t("grp.btn.create"), "grp:new")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def card_view(data: dict, link: str) -> tuple[str, InlineKeyboardMarkup]:
    e = html.escape
    role = t(f"grp.role.{data['my_role']}")
    lines = [t("grp.info", kind=e(data["kind_label"]), title=e(data["title"]), n=data["members_count"], role=e(role))]
    members = ", ".join(e(m["name"]) + ("" if m["can_receive"] else " ⚠️") for m in data.get("members", [])[:30])
    if members:
        lines += ["", members]
    for ev in data.get("upcoming", [])[:3]:
        lines.append(f"{ev['emoji']} {e(ev['dates'])} — {e(ev['title'])}")
    lines += ["", e(t("grp.link", link=link))]
    rows = []
    if data["my_role"] in ("owner", "admin"):
        rows.append([_btn(t("grp.btn.post"), f"grp:post:{data['id']}"), _btn(t("grp.btn.agreement"), f"grp:agr:{data['id']}")])
    rows.append([_btn(t("grp.btn.alerts_on") if data["alerts"] else t("grp.btn.alerts_off"), f"grp:alerts:{data['id']}:{0 if data['alerts'] else 1}"),
                 _btn(t("grp.btn.posts_on") if data["posts"] else t("grp.btn.posts_off"), f"grp:posts:{data['id']}:{0 if data['posts'] else 1}")])
    rows.append([_btn(t("grp.btn.leave"), f"grp:leave:{data['id']}"), _btn(t("grp.btn.back"), "grp:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def invite_view(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = html.escape(t("grp.invite", title=data["title"], kind=data["kind_label"], n=data["members_count"]))
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("grp.btn.join"), f"grp:join:{data['invite_token_public']}"), _btn(t("grp.btn.no"), "grp:no")]])
    return text, markup


async def show_invite(message: Message, token: str) -> None:
    """Deep link grp_ТОКЕН из /start."""
    data = await asyncio.to_thread(service.by_token, token)
    if data is None:
        await message.answer(t("grp.err.not_found"))
        return
    data["invite_token_public"] = token
    text, markup = invite_view(data)
    await message.answer(text, reply_markup=markup)


# ------------------------------------------------------------------ команды


@router.message(Command("gruppa"), F.chat.type == ChatType.PRIVATE)
async def cmd_groups(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await asyncio.to_thread(list_view, message.from_user.id)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("gruppa"), F.chat.type.in_(GROUP_TYPES))
async def cmd_groups_in_chat(message: Message, command: CommandObject) -> None:
    """В чате: привязать группу к чату и показать кнопку «Вступить»."""
    kind = (command.args or "").strip().lower()
    kind = kind if kind in service.KINDS else "group"
    user = message.from_user
    data = await asyncio.to_thread(service.link_chat, message.chat.id, message.chat.title or "", user.id, user.first_name or "", kind)
    token = await asyncio.to_thread(service.invite_token, data["id"])
    text = t("grp.chat_card", title=html.escape(data["title"])) + "\n\n" + html.escape(t("grp.chat_hint"))
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("grp.btn.join"), f"grp:join:{token}")]])
    await message.answer(text, reply_markup=markup)


# ------------------------------------------------------------------ кнопки


@router.callback_query(F.data.startswith("grp:"))
async def cb_groups(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    action = parts[1]
    user = callback.from_user
    name = user.first_name or ""
    try:
        if action == "list":
            await callback.answer()
            text, markup = await asyncio.to_thread(list_view, user.id)
            await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        elif action == "new":
            await callback.answer()
            rows = [[_btn(service.kind_label(k), f"grp:kind:{k}")] for k in service.KINDS]
            await callback.message.edit_text(html.escape(t("grp.ask_kind")), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        elif action == "kind":
            await callback.answer()
            await state.set_state(WaitCircle.title)
            await state.update_data(kind=parts[2])
            await callback.message.edit_text(html.escape(t("grp.ask_title")))
        elif action == "join":
            data = await asyncio.to_thread(service.join, user.id, name, parts[2])
            await callback.answer(t("grp.joined", title=data["title"]), show_alert=True)
            if callback.message.chat.type == ChatType.PRIVATE:
                await callback.message.edit_text(html.escape(t("grp.joined", title=data["title"])))
        elif action == "no":
            await callback.answer()
            await callback.message.edit_text(html.escape(t("grp.declined")))
        elif action == "open":
            await callback.answer()
            await _open(callback, int(parts[2]))
        elif action in ("alerts", "posts"):
            kwargs = {action: parts[3] == "1"}
            await asyncio.to_thread(service.update_settings, int(parts[2]), user.id, **kwargs)
            await callback.answer(t("grp.saved"))
            await _open(callback, int(parts[2]))
        elif action == "leave":
            await asyncio.to_thread(service.leave, int(parts[2]), user.id)
            await callback.answer(t("grp.left"), show_alert=True)
            text, markup = await asyncio.to_thread(list_view, user.id)
            await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        elif action in ("post", "agr"):
            data = await asyncio.to_thread(service.info, int(parts[2]), user.id)
            if data["my_role"] not in ("owner", "admin"):
                raise service.CircleError(t("grp.err.admin_only"))
            await callback.answer()
            await state.set_state(WaitCircle.post if action == "post" else WaitCircle.agreement)
            await state.update_data(circle_id=data["id"], title=data["title"])
            key = "grp.ask_post" if action == "post" else "grp.ask_agreement"
            await callback.message.answer(html.escape(t(key, title=data["title"])))
        elif action == "send":
            st = await state.get_data()
            await state.clear()
            if not st.get("post_text"):
                await callback.answer()
                return
            result = await service.post_announcement(st["circle_id"], user.id, user.full_name or name, st["post_text"])
            await callback.answer()
            await callback.message.edit_text(html.escape(t("grp.posted", sent=result["sent"], total=result["recipients"])))
        elif action == "check":
            st = await state.get_data()
            await callback.answer("⏳")
            try:
                outcome = await get_verdict_engine().check(CheckInput(mode="obyavlenie", text=st.get("post_text", ""), user_id=user.id, save=False))
                await callback.message.answer(render_card_html(outcome.card, st.get("post_text", "")), disable_web_page_preview=True)
            except EngineError as exc:
                await callback.message.answer(f"⚠️ {html.escape(str(exc))}")
        elif action == "cancel":
            await state.clear()
            await callback.answer()
            await callback.message.edit_text(t("bot.cancelled"))
        else:
            await callback.answer()
    except service.CircleError as exc:
        await callback.answer(str(exc), show_alert=True)


async def _open(callback: CallbackQuery, circle_id: int) -> None:
    data = await asyncio.to_thread(service.info, circle_id, callback.from_user.id)
    link = await _invite_link(callback, data["invite_token"])
    text, markup = card_view(data, link)
    try:
        await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001 — «сообщение не изменилось»
        pass


# ------------------------------------------------------------------ ввод текста


@router.message(WaitCircle.title, F.chat.type == ChatType.PRIVATE, F.text)
async def on_title(message: Message, state: FSMContext) -> None:
    st = await state.get_data()
    await state.clear()
    if message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    from backend.app.telegram.handlers.onboarding import get_profile

    profile = await asyncio.to_thread(get_profile, message.from_user.id)
    course = profile["course"] if profile and profile["role"] == "student" else ""
    try:
        data = await asyncio.to_thread(service.create, message.from_user.id, message.from_user.first_name or "", message.text,
                                       st.get("kind", "group"), course)
    except service.CircleError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    link = await _invite_link(message, data["invite_token"])
    await message.answer(html.escape(t("grp.created", title=data["title"], link=link)), disable_web_page_preview=True)


@router.message(WaitCircle.post, F.chat.type == ChatType.PRIVATE, F.text)
async def on_post_text(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        await state.clear()
        await message.answer(t("bot.cancelled"))
        return
    st = await state.get_data()
    await state.update_data(post_text=message.text)
    data = await asyncio.to_thread(service.info, st["circle_id"], message.from_user.id)
    preview = t("grp.post_msg", title=html.escape(data["title"]), author=html.escape(message.from_user.full_name or ""), text=html.escape(message.text))
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn(t("grp.btn.send"), "grp:send"), _btn(t("grp.btn.check"), "grp:check")],
                                                   [_btn(t("grp.btn.cancel"), "grp:cancel")]])
    await message.answer(t("grp.preview", message=preview, n=max(0, data["members_count"] - 1)), reply_markup=markup)


@router.message(WaitCircle.agreement, F.chat.type == ChatType.PRIVATE, F.text)
async def on_agreement_text(message: Message, state: FSMContext) -> None:
    st = await state.get_data()
    await state.clear()
    if message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    try:
        result = await service.create_agreement(st["circle_id"], message.from_user.id, message.from_user.full_name or "", message.text)
    except (service.CircleError, EngineError) as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    await message.answer(html.escape(t("grp.agreement_sent", sent=result["sent"])))

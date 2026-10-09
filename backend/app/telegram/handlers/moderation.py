"""Модерация в Telegram: кнопки под заявками и команды. Та же очередь — в Mini App (Профиль → Модерация).

Кто модерирует: ADMIN_IDS / MODERATOR_IDS в .env и модераторы, назначенные админом (/grant).
Права проверяются при КАЖДОМ нажатии (services/roles.py): в общем чате модераторов кнопку
может увидеть кто угодно, но решение примут только от модератора.

/mod                       — что ждёт проверки (каждая заявка — с кнопками)
/reject post:12 причина    — отклонить со своей причиной
/edit post:12 новый текст  — исправить текст и опубликовать (первая строка — заголовок)
/modlog                    — последние решения: кто, что, когда
/mod_best                  — лучшие ответы старшекурсников → «В навигатор»
/grant ID  /revoke ID      — назначить или снять модератора (только админ)
/hub_new Название | курс | программа | описание — создать хаб дисциплины
/myid                      — узнать свой Telegram id (для ADMIN_IDS в .env)
"""

from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.app.i18n import t
from backend.app.services import community, hubs, moderation, roles

router = Router(name="moderation")


def _markup(rows) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=a, callback_data=b) for a, b in row] for row in rows])


async def _moderator_only(message: Message) -> bool:
    if not await asyncio.to_thread(roles.is_moderator, message.from_user.id):
        await message.answer(t("mod.err.moderator_only"))
        return False
    return True


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(t("mod.myid", id=message.from_user.id, chat=message.chat.id))


@router.message(Command("mod"))
async def cmd_mod(message: Message) -> None:
    if not await _moderator_only(message):
        return
    items = await asyncio.to_thread(moderation.queue, message.from_user.id, 15)
    if not items:
        await message.answer(t("mod.empty"))
        return
    await message.answer(html.escape(t("mod.queue_head", n=len(items))))
    for item in items:
        await message.answer(moderation.item_html(item), reply_markup=_markup(moderation.item_buttons(item)), disable_web_page_preview=True)


async def _decide_reply(message: Message, key: str, action: str, **kwargs) -> None:
    try:
        result = await moderation.decide(message.from_user.id, message.from_user.first_name or "", key, action, **kwargs)
    except (moderation.ModerationError, roles.RoleError) as exc:
        await message.answer(html.escape(str(exc)))
        return
    await message.answer(html.escape(t("mod.done", target=key, status=t(f"mod.status.{result['status']}"))))


@router.message(Command("reject"))
async def cmd_reject(message: Message, command: CommandObject) -> None:
    if not await _moderator_only(message):
        return
    key, _, reason = (command.args or "").strip().partition(" ")
    if ":" not in key or len(reason.strip()) < 3:
        await message.answer(t("mod.reject.usage"))
        return
    await _decide_reply(message, key, "reject", reason=reason.strip())


@router.message(Command("edit"))
async def cmd_edit(message: Message, command: CommandObject) -> None:
    if not await _moderator_only(message):
        return
    key, _, text = (command.args or "").strip().partition(" ")
    if not key.startswith("post:") or len(text.strip()) < 3:
        await message.answer(t("mod.edit.usage"))
        return
    title, _, body = text.strip().partition("\n")
    await _decide_reply(message, key, "edit", title=title, body=body.strip() or None)


@router.message(Command("modlog"))
async def cmd_modlog(message: Message) -> None:
    if not await _moderator_only(message):
        return
    rows = await asyncio.to_thread(moderation.journal, message.from_user.id, 20)
    if not rows:
        await message.answer(t("mod.log_empty"))
        return
    e = html.escape
    lines = [f"<b>{e(t('mod.log_title'))}</b>"]
    for r in rows:
        when = (r["created_at"] or "")[:16].replace("T", " ")
        lines.append(f"{e(when)} · {e(r['moderator'])}: {e(r['action_label'])} {e(r['kind_label'])} <code>{e(r['target'])}</code>"
                     + (f" — {e(r['reason'])}" if r["reason"] else ""))
    await message.answer("\n".join(lines))


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().split()
    if not arg or not arg[0].isdigit():
        await message.answer(t("mod.grant.usage"))
        return
    role = "admin" if len(arg) > 1 and arg[1] == "admin" else "moderator"
    try:
        await asyncio.to_thread(roles.grant, message.from_user.id, message.from_user.first_name or "", int(arg[0]), role)
    except roles.RoleError as exc:
        await message.answer(html.escape(str(exc)))
        return
    await message.answer(html.escape(t("mod.grant.done", id=arg[0], role=t(f"role.{role}"))))


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer(t("mod.revoke.usage"))
        return
    try:
        await asyncio.to_thread(roles.revoke, message.from_user.id, message.from_user.first_name or "", int(arg))
    except roles.RoleError as exc:
        await message.answer(html.escape(str(exc)))
        return
    await message.answer(html.escape(t("mod.revoke.done", id=arg)))


@router.message(Command("mod_best"))
async def cmd_mod_best(message: Message) -> None:
    if not await _moderator_only(message):
        return
    items = await asyncio.to_thread(community.best_answers)
    if not items:
        await message.answer(t("mod.best_empty"))
        return
    e = html.escape
    for a in items:
        label = t("mod.btn.unpromote") if a["promoted"] else t("mod.btn.promote")
        await message.answer(f"{e(t('mod.best.q'))} {e(a['question'][:300])}\n{e(t('mod.best.a'))} {e(a['answer'][:800])}\n+{a['score']}",
                             reply_markup=_markup([[(label, f"mod:promote:{a['id']}")]]))


@router.message(Command("hub_new"))
async def cmd_hub_new(message: Message, command: CommandObject) -> None:
    if not await _moderator_only(message):
        return
    parts = [p.strip() for p in (command.args or "").split("|")]
    if not parts or len(parts[0]) < 3:
        await message.answer(t("mod.hub_new.usage"))
        return
    while len(parts) < 4:
        parts.append("")
    try:
        hub = await asyncio.to_thread(hubs.create_hub_direct, message.from_user.id, parts[0], parts[1], parts[2], parts[3])
    except hubs.HubError as exc:
        await message.answer(str(exc))
        return
    await message.answer(html.escape(t("mod.hub_new.done", title=hub["title"], slug=hub["slug"])))


# ------------------------------------------------------------------ кнопки


@router.callback_query(F.data.startswith("mq:"))
async def cb_queue(callback: CallbackQuery) -> None:
    """mq:a|n|r|b|v|h|k:<post|report|app>:<id>[:<параметр>]."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    action, key, param = parts[1], f"{parts[2]}:{parts[3]}", (parts[4] if len(parts) > 4 else "")
    uid, name = callback.from_user.id, callback.from_user.first_name or ""
    if not await asyncio.to_thread(roles.is_moderator, uid):
        await callback.answer(t("mod.err.moderator_only"), show_alert=True)
        return
    try:
        if action in ("n", "b"):
            # «Отклонить» → выбрать причину; «Назад» → обычные кнопки.
            item = await asyncio.to_thread(moderation.get_item, uid, key)
            if item["status"] != "pending" and item["type"] != "report":
                raise moderation.ModerationError(t("mod.err.done"))
            await callback.message.edit_reply_markup(reply_markup=_markup(moderation.item_buttons(item, "reasons" if action == "n" else "main")))
            await callback.answer(t("mod.pick_reason") if action == "n" else "")
            return
        act = {"a": "approve", "r": "reject", "v": "approve", "h": "hide", "k": "keep"}.get(action)
        if act is None:
            raise moderation.ModerationError(t("mod.err.action"))
        result = await moderation.decide(uid, name, key, act, reason=param if action == "r" else "", verdict=param if action == "v" else "")
    except (moderation.ModerationError, roles.RoleError) as exc:
        await callback.answer(str(exc), show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 — сообщение могли уже изменить
            pass
        return
    label = t(f"mod.status.{result['status']}")
    if action == "v":
        label = t(f"truth.{param}")
    elif action == "r":
        label += " · " + moderation.reason_text(param)
    await callback.answer(label)
    try:
        await callback.message.edit_text(callback.message.html_text + "\n\n" + html.escape(t("mod.decided_by", who=name, what=label)),
                                         reply_markup=None, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("mod:"))
async def cb_mod(callback: CallbackQuery) -> None:
    """Старые кнопки из сообщений до обновления и «лучший ответ → в навигатор»."""
    _, action, post_id = callback.data.split(":", 2)
    try:
        status = await community.moderate(int(post_id), callback.from_user.id, action, callback.from_user.first_name or "")
    except community.CommunityError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(f"mod.status.{status}") if action != "promote" else t("mod.promoted"))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("app:"))
async def cb_app(callback: CallbackQuery) -> None:
    """Старые кнопки заявок (ментор, преподаватель, хаб)."""
    _, action, app_id = callback.data.split(":", 2)
    try:
        result = await moderation.decide(callback.from_user.id, callback.from_user.first_name or "", f"app:{app_id}",
                                         "approve" if action == "ok" else "reject", reason="rules")
    except (moderation.ModerationError, roles.RoleError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t(f"mod.app_status.{result['status']}"))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass

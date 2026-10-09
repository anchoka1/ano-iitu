"""Команды бота по новой структуре (те же разделы, что в Mini App).

/moi     — мои обращения: что отправлял и что с этим стало (статус, причина отказа)
/radar   — свежие предупреждения Радара разводов + подписка; сообщить о схеме — в приложении или ответом /razvod
/fakty   — «Слухи и факты»: слухи, по которым модератор вынес вердикт
/put     — «Мой путь»: квиз, чек-лист курса, вопросы старшекурсникам, письмо себе
/start   — для знакомых: короткая сводка «Сегодня» (today_html)
"""

from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.core.features import enabled
from backend.app.i18n import t
from backend.app.services import facts, prefs, submissions, today

router = Router(name="flows")


def app_button(text: str, screen: str = "") -> list[InlineKeyboardButton] | None:
    """Кнопка «Открыть в приложении» на нужный экран (только в личке и только по HTTPS)."""
    url = get_settings().webapp_url.strip()
    if not url.lower().startswith("https://"):
        return None
    return [InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url.rstrip("/") + "/" + (f"#{screen}" if screen else "")))]


def today_html(user_id: int, first_name: str = "") -> str:
    """Сводка «Сегодня» для /start: только непустые блоки."""
    data = today.build(user_id, first_name)
    e = html.escape
    lines: list[str] = []
    if data.get("deadlines"):
        lines.append(f"<b>{e(t('today.deadlines'))}</b>")
        for task in data["deadlines"][:4]:
            when = task.get("due_date") or ""
            lines.append(f"• {e(task['title'])}" + (f" — {e(when)}" if when else ""))
    if data.get("radar"):
        lines.append(f"<b>{e(t('today.radar'))}</b>")
        lines += [f"• {e(r['title'])}" for r in data["radar"][:2]]
    if data.get("answers"):
        lines.append(f"<b>{e(t('today.answers'))}</b>")
        lines += [f"• {e(a['question'][:80])}: {e(a['answer'][:120])}" for a in data["answers"][:2]]
    if data.get("decisions"):
        lines.append(f"<b>{e(t('today.decisions'))}</b>")
        lines += [f"• {e(d['kind_label'])}: {e(d['status_label'])}" + (f" — {e(d['reason'])}" if d["reason"] else "") for d in data["decisions"][:3]]
    if data.get("mod_queue"):
        lines.append(e(t("today.mod_queue", n=data["mod_queue"])))
    return "\n".join(lines)


@router.message(Command("moi"), F.chat.type == ChatType.PRIVATE)
async def cmd_moi(message: Message) -> None:
    items = await asyncio.to_thread(submissions.list_mine, message.from_user.id)
    if not items:
        await message.answer(t("sub.empty_bot"))
        return
    e = html.escape
    lines = [f"<b>{e(t('sub.title'))}</b>"]
    for x in items[:15]:
        line = f"• {e(x['kind_label'])}: «{e(x['title'][:80])}» — <b>{e(x['status_label'])}</b>"
        if x["reason"]:
            line += f"\n  {e(t('mod.notify.reason', reason=x['reason']))}"
        lines.append(line)
    rows = [b] if (b := app_button(t("sub.open_app"), "mine")) else []
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def _radar_keyboard(subscribed: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t("radar.btn.unsub") if subscribed else t("radar.btn.sub"), callback_data=f"rsub:{0 if subscribed else 1}")]]
    if b := app_button(t("radar.btn.report"), "radar"):
        rows.append(b)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("radar"), F.chat.type == ChatType.PRIVATE)
async def cmd_radar(message: Message, command: CommandObject) -> None:
    if not enabled("scam_radar"):
        await message.answer(t("cm.feature_off"))
        return
    text = (command.args or "").strip()
    if text:
        # «/radar описание схемы» — сразу заявка модератору; решение придёт сообщением, статус — в /moi.
        from backend.app.services import community

        title, _, body = text.partition("\n")
        try:
            post = await asyncio.to_thread(community.create_post, "radar", message.from_user.id, message.from_user.first_name or "",
                                           title[:200], body or title)
        except community.CommunityError as exc:
            await message.answer(html.escape(str(exc)))
            return
        if post.get("pending"):
            await community.announce_pending(post["id"])
        await message.answer(t("cm.radar.sent"))
        return
    items = await asyncio.to_thread(today.radar_fresh, 5, 60)
    e = html.escape
    lines = [f"<b>{e(t('radar.title'))}</b>", e(t("radar.how"))]
    if items:
        lines.append("")
        lines += [f"• <b>{e(r['title'])}</b>\n  {e(r['body'][:160])}" for r in items]
    else:
        lines += ["", e(t("radar.empty"))]
    subscribed = prefs.get_bool(message.from_user.id, "sub.radar")
    await message.answer("\n".join(lines), reply_markup=_radar_keyboard(subscribed))


@router.callback_query(F.data.startswith("rsub:"))
async def cb_radar_sub(callback: CallbackQuery) -> None:
    on = callback.data.endswith(":1")
    await asyncio.to_thread(prefs.set_value, callback.from_user.id, "sub.radar", "1" if on else "0")
    await callback.answer(t("radar.subscribed") if on else t("radar.unsubscribed"), show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=_radar_keyboard(on))
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("fakty"), F.chat.type == ChatType.PRIVATE)
async def cmd_fakty(message: Message) -> None:
    items = await asyncio.to_thread(facts.feed, message.from_user.id, 8)
    e = html.escape
    if not items:
        await message.answer(t("facts.empty"))
        return
    lines = [f"<b>{e(t('facts.title'))}</b>"]
    for f in items:
        lines.append(f"• «{e(f['claim'][:140])}» — <b>{e(f['label'])}</b>" + (f"\n  {e(f['comment'][:200])}" if f["comment"] else ""))
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("put"), F.chat.type == ChatType.PRIVATE)
async def cmd_put(message: Message) -> None:
    from backend.app.services import path

    data = await asyncio.to_thread(path.overview, message.from_user.id)
    done = sum(1 for v in data["steps"].values() if v)
    text = html.escape(t("path.bot", done=done, next=t(f"path.step.{data['next']}") if data["next"] else t("path.all_done")))
    rows = [b] if (b := app_button(t("path.open"), "path")) else []
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

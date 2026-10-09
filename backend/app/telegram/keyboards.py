"""Inline-кнопки бота.

callback_data — короткая строка (до 64 байт), которую Telegram вернёт
боту при нажатии. Формат у нас «действие:параметры», например
"v:15:1" — голос «согласен» по карточке 15.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from backend.app.core.config import get_settings
from backend.app.i18n import t
from backend.app.modes.registry import MODES


def app_link(start_param: str, bot_username: str) -> str:
    """Прямая ссылка на Mini App (работает и в группах)."""
    short = get_settings().webapp_short_name
    if short and bot_username:
        return f"https://t.me/{bot_username}/{short}?startapp={start_param}"
    return ""


def card_keyboard(check_id: int, agree: int = 0, disagree: int = 0, private: bool = True, bot_username: str = "",
                  radar: bool = False, escalate: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=f"{t('btn.agree')} {agree}" if agree else t("btn.agree"), callback_data=f"v:{check_id}:1"),
            InlineKeyboardButton(text=f"{t('btn.disagree')} {disagree}" if disagree else t("btn.disagree"), callback_data=f"v:{check_id}:-1"),
        ],
        [
            InlineKeyboardButton(text=t("btn.add_source"), callback_data=f"src:{check_id}"),
            InlineKeyboardButton(text=t("btn.share_image"), callback_data=f"img:{check_id}"),
        ],
        [InlineKeyboardButton(text=t("btn.simpler"), callback_data=f"simple:{check_id}")],
    ]
    if escalate:
        # «Правда» не нашла подтверждений — отдать слух модератору (вердикт придёт сообщением).
        rows.insert(0, [InlineKeyboardButton(text=t("truth.escalate"), callback_data=f"esc:{check_id}")])
    if radar:
        # «Радар разводов»: предупредить других (после модерации владельцем бота).
        rows.append([InlineKeyboardButton(text=t("btn.radar"), callback_data=f"radar:{check_id}")])
    settings = get_settings()
    # Кнопка web_app работает только в личных чатах; в группах — прямая ссылка t.me/...
    # Номер проверки передаём через ?check=, потому что часть после # Telegram занимает своими данными.
    if private and settings.webapp_is_https:
        rows.append([InlineKeyboardButton(text=t("btn.open_app"), web_app=WebAppInfo(url=f"{settings.webapp_url.rstrip('/')}/?check={check_id}"))])
    elif not private and (link := app_link(f"check_{check_id}", bot_username)):
        rows.append([InlineKeyboardButton(text=t("btn.open_app"), url=link)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode_picker(token: str, files_only: bool = False, task: bool = False, syllabus: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{m.emoji} {m.title()}", callback_data=f"pick:{m.key}:{token}")
        for m in MODES
        if not files_only or m.accepts_files
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]  # по две кнопки в ряд
    if syllabus:
        # Документ (PDF, DOCX): чаще всего это силлабус — разбор первой кнопкой.
        rows.insert(0, [InlineKeyboardButton(text=t("btn.parse_syllabus"), callback_data=f"syl:from:{token}")])
    if task:
        # «Мой план»: любое пересланное сообщение можно превратить в задачу.
        rows.append([InlineKeyboardButton(text=t("btn.make_task"), callback_data=f"mktask:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agreement_keyboard(code: str, status: str, multi: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    # Групповая договорённость: подтверждать можно, пока она не закрыта, — каждый за себя.
    if status == "pending" or (multi and status == "confirmed"):
        rows.append([
            InlineKeyboardButton(text=t("agr.btn.confirm"), callback_data=f"agr:ok:{code}"),
            InlineKeyboardButton(text=t("agr.btn.decline"), callback_data=f"agr:no:{code}"),
        ])
    if status == "confirmed":
        rows.append([InlineKeyboardButton(text=t("agr.btn.done"), callback_data=f"agr:done:{code}")])
    rows.append([InlineKeyboardButton(text=t("agr.btn.pdf"), callback_data=f"agr:pdf:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no(yes_data: str, no_data: str, yes_text: str, no_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=yes_text, callback_data=yes_data),
        InlineKeyboardButton(text=no_text, callback_data=no_data),
    ]])

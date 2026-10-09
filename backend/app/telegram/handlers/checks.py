"""Проверки: команды режимов, выбор режима, фото, голосовые, пересылки.

Как бот понимает, ЧТО проверять:
  1. /razvod текст          — текст после команды;
  2. ответ командой /razvod на сообщение — проверяем именно то сообщение
     (в группах это основной способ: бот видит только команды и ответы ему);
  3. в личке: команда без текста → бот просит прислать текст (состояние FSM);
  4. в личке: просто переслать сообщение → бот спрашивает «Что проверить?»;
  5. в личке: прислать файл (фото, PDF, DOCX) → после согласия на обработку — «Что сделать?»
     (проверить или разобрать как силлабус). Оригинал файла не сохраняется.

«Правда»: на карточке — шкала вердикта и «проверяли N раз»; если подтверждений нет —
кнопка «Отправить модератору» (services/facts.py).

FSM (Finite State Machine, «конечный автомат») — способ помнить, что бот
ждёт от человека следующий ответ («жду текст для /pravda»).
"""

from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from backend.app.cards.render import render_card_html
from backend.app.core import stt
from backend.app.core.engine import CheckInput, EngineError, get_verdict_engine
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t
from backend.app.llm.base import Attachment
from backend.app.modes.registry import MODES, get_mode, mode_by_command
from backend.app.telegram import pending
from backend.app.telegram.keyboards import card_keyboard, mode_picker
from backend.app.telegram.media import MediaError, consent_given, consent_keyboard, has_file, origin_of, read_file
from backend.app.university import crisis

log = logging.getLogger("verdikt.bot")
router = Router(name="checks")

CHECK_COMMANDS = [m.command for m in MODES if m.is_check]
# «Ты официальный бот?», «бот официальный?» — но не «это официальное решение?».
OFFICIAL_Q = re.compile(r"\b(ты|вы)\s+(\w+\s+){0,3}официальн\w*|официальн\w*\s+(ли\s+)?(это\s+)?бот|\bбот\w*\s+(\w+\s+){0,2}официальн", re.IGNORECASE)


class WaitInput(StatesGroup):
    text = State()


async def collect_input(message: Message, args: str | None, bot: Bot, accepts_files: bool) -> tuple[str, str, list[Attachment]]:
    """Что проверять: текст после команды, сообщение, на которое ответили, и вложения."""
    text = (args or "").strip()
    origin = ""
    attachments: list[Attachment] = []
    target = message.reply_to_message
    if target is not None and target.forum_topic_created is not None:
        target = None  # в группе с темами «ответ» на служебное сообщение темы — это не ответ на сообщение
    if target is not None:
        target_text = target.text or target.caption or ""
        text = f"{target_text}\n{text}".strip() if text else target_text
        origin = origin_of(target)
        if accepts_files and has_file(target):
            attachments, doc_text = await read_file(bot, target)
            text = f"{text}\n{doc_text}".strip()
    elif accepts_files and has_file(message):
        attachments, doc_text = await read_file(bot, message)
        text = f"{text}\n{doc_text}".strip()
    return text, origin, attachments


async def run_check(message: Message, bot: Bot, mode_key: str, text: str, origin: str, attachments: list[Attachment],
                    user_id: int, user_name: str, reply_to: int | None = None) -> None:
    """Проверка и отправка карточки. Сначала «⏳ Проверяю…», потом заменяем на карточку."""
    is_private = message.chat.type == ChatType.PRIVATE
    if not is_private:
        # Запоминаем группу для «Индекса чата». Храним только id и название, не сообщения.
        with get_sessionmaker()() as session:
            repo.upsert_chat(session, message.chat.id, message.chat.title or "", message.chat.type)
    # Группа с темами: отвечаем в той же теме, а её название — контекст вопроса («Math» → математика).
    thread_id = message.message_thread_id if message.is_topic_message else None
    topic = ""
    if thread_id:
        from backend.app.telegram.handlers.hubs import thread_topic

        topic = thread_topic(message)
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING, message_thread_id=thread_id)
    progress = await bot.send_message(message.chat.id, t("bot.progress"), reply_to_message_id=reply_to, message_thread_id=thread_id)
    try:
        outcome = await get_verdict_engine().check(CheckInput(
            mode=mode_key, text=text, user_id=user_id, user_name=user_name,
            chat_id=message.chat.id, origin=origin, attachments=attachments, thread_id=thread_id, topic=topic,
        ))
    except EngineError as exc:
        await progress.edit_text(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("Ошибка проверки")
        await progress.edit_text(t("bot.error_generic"))
        return

    me = await bot.me()
    truth = _truth(outcome.check_id, user_id) if mode_key == "pravda" and outcome.check_id else None
    html_text = render_card_html(outcome.card, text, truth=truth)
    from backend.app.core.features import enabled

    radar = is_private and mode_key == "razvod" and outcome.card.status == "red" and enabled("scam_radar")
    escalate = bool(truth and truth["can_escalate"] and enabled("fact_feed"))
    markup = card_keyboard(outcome.check_id, private=is_private, bot_username=me.username or "", radar=radar,
                           escalate=escalate) if outcome.check_id else None
    await progress.edit_text(html_text, reply_markup=markup, disable_web_page_preview=True)
    if mode_key == "chek" and is_private and enabled("syllabus") and text:
        from backend.app.services.campus import looks_like_syllabus
        from backend.app.telegram.handlers.campus import offer_syllabus

        if looks_like_syllabus(text):
            await offer_syllabus(message, user_id, text)
    if outcome.check_id:
        with get_sessionmaker()() as session:
            check = repo.get_check(session, outcome.check_id)
            if check:
                check.message_id = progress.message_id
                session.commit()


def _truth(check_id: int, user_id: int) -> dict | None:
    from backend.app.services.facts import truth_info

    with get_sessionmaker()() as session:
        check = repo.get_check(session, check_id)
        return truth_info(session, check, user_id) if check else None


@router.callback_query(F.data.startswith("esc:"))
async def cb_escalate(callback: CallbackQuery) -> None:
    """«Отправить модератору»: слух уходит в очередь, автор получит вердикт сообщением."""
    from backend.app.services import facts

    check_id = callback.data.split(":", 1)[1]
    try:
        result = await facts.escalate(int(check_id), callback.from_user.id, callback.from_user.first_name or "")
    except (facts.FactsError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(t("truth.joined" if result["joined"] else "truth.escalated"), show_alert=True)
    try:
        markup = callback.message.reply_markup
        if markup:
            rows = [row for row in markup.inline_keyboard if not any((b.callback_data or "").startswith("esc:") for b in row)]
            await callback.message.edit_reply_markup(reply_markup=type(markup)(inline_keyboard=rows))
    except Exception:  # noqa: BLE001 — сообщение могли изменить
        pass


@router.message(Command(*CHECK_COMMANDS))
async def cmd_mode(message: Message, command: CommandObject, bot: Bot, state: FSMContext) -> None:
    mode = mode_by_command(command.command)
    files_involved = mode.accepts_files and (has_file(message) or (message.reply_to_message is not None and has_file(message.reply_to_message)))
    if files_involved and message.chat.type == ChatType.PRIVATE and not consent_given(message.from_user.id):
        await message.answer(t("consent.text"), reply_markup=consent_keyboard("-"))
        return
    try:
        text, origin, attachments = await collect_input(message, command.args, bot, mode.accepts_files and message.chat.type == ChatType.PRIVATE)
    except MediaError as exc:
        await message.reply(t(f"bot.file.{exc}"))
        return
    if not text and not attachments:
        if message.chat.type == ChatType.PRIVATE:
            await state.set_state(WaitInput.text)
            await state.update_data(mode=mode.key)
            await message.answer(t(f"bot.ask_input.{mode.key}"))
        else:
            await message.reply(t("bot.group_hint", command=mode.command))
        return
    user = message.from_user
    if mode.key == "vopros" and message.chat.type != ChatType.PRIVATE and text:
        # Учебный хаб: такой вопрос уже разбирали — предлагаем сохранённый ответ со ссылкой.
        from backend.app.telegram.handlers.hubs import offer_saved_answer

        if await offer_saved_answer(message, text):
            return
    await run_check(message, bot, mode.key, text, origin, attachments, user.id, user.first_name or "",
                    reply_to=message.message_id)


@router.message(WaitInput.text, F.chat.type == ChatType.PRIVATE)
async def on_waited_input(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    mode = get_mode(data.get("mode", ""))
    if mode is None:
        return
    if message.text and message.text.startswith("/"):
        await message.answer(t("bot.cancelled"))
        return
    if has_file(message) and not consent_given(message.from_user.id):
        await message.answer(t("consent.text"), reply_markup=consent_keyboard("-"))
        return
    try:
        attachments, doc_text = await read_file(bot, message) if mode.accepts_files or message.document else ([], "")
    except MediaError as exc:
        await message.answer(t(f"bot.file.{exc}"))
        return
    text = f"{message.text or message.caption or ''}\n{doc_text}".strip()
    await run_check(message, bot, mode.key, text, origin_of(message), attachments, message.from_user.id,
                    message.from_user.first_name or "", reply_to=message.message_id)


@router.message(F.chat.type == ChatType.PRIVATE, F.voice | F.audio | F.video_note)
async def on_voice(message: Message, bot: Bot) -> None:
    if not stt.is_available():
        await message.answer(t("bot.voice_unavailable"))
        return
    media = message.voice or message.audio or message.video_note
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    data = await bot.download(media.file_id)
    try:
        filename = getattr(media, "file_name", None) or ("video.mp4" if message.video_note else "voice.ogg")
        text = await stt.transcribe(data.read(), filename)
    except Exception:
        log.exception("Ошибка распознавания речи")
        await message.answer(t("bot.voice_failed"))
        return
    if not text:
        await message.answer(t("bot.voice_failed"))
        return
    token = pending.put(pending.Pending(message.from_user.id, text, reply_to=message.message_id))
    await message.answer(t("bot.voice_recognized", text=text[:500]), reply_markup=mode_picker(token))


@router.message(F.chat.type == ChatType.PRIVATE, ~F.text.startswith("/"))
async def on_private_message(message: Message, bot: Bot) -> None:
    """Любое сообщение в личке без команды: спросить, что проверить. Файл — сначала согласие на обработку."""
    if has_file(message) and not consent_given(message.from_user.id):
        token = pending.put(pending.Pending(message.from_user.id, "", origin_of(message), [], message.message_id))
        _waiting_files[token] = message
        await message.answer(t("consent.text"), reply_markup=consent_keyboard(token))
        return
    await _ask_what_to_do(message, bot)


_waiting_files: dict[str, Message] = {}


async def _ask_what_to_do(message: Message, bot: Bot) -> None:
    try:
        attachments, doc_text = await read_file(bot, message)
    except MediaError as exc:
        await message.answer(t(f"bot.file.{exc}"))
        return
    text = f"{message.text or message.caption or ''}\n{doc_text}".strip()
    is_document = bool(doc_text) or bool(message.document)
    if not text and not attachments:
        await message.answer(t("bot.unsupported"))
        return
    if OFFICIAL_Q.search(text) and len(text) < 120:
        # «Ты официальный бот?» — не подтверждаем и не отрицаем, отвечаем по существу.
        await message.answer(t("bot.official_answer"))
        return
    level = crisis.detect(text)
    if level:
        # Человеку плохо: не спрашиваем «что проверить», а сразу даём контакты помощи.
        await message.answer(crisis.support_html(level), disable_web_page_preview=True)
        return
    token = pending.put(pending.Pending(message.from_user.id, text, origin_of(message), attachments, message.message_id))
    from backend.app.core.features import enabled

    await message.reply(t("bot.pick_mode_file") if is_document else t("bot.pick_mode"),
                        reply_markup=mode_picker(token, files_only=bool(attachments) and not text,
                                                 task=bool(text) and enabled("planner") and not is_document,
                                                 syllabus=is_document and enabled("syllabus")))


@router.callback_query(F.data.startswith("cons:"))
async def cb_consent(callback: CallbackQuery, bot: Bot) -> None:
    """Согласие на обработку файлов: «Согласен» → продолжаем с присланным файлом; «Не сейчас» → файл не трогаем."""
    from backend.app.services import prefs
    from backend.app.services.planner import local_today

    parts = callback.data.split(":")
    if parts[1] != "ok":
        await callback.message.edit_text(t("consent.declined"))
        await callback.answer()
        return
    prefs.set_value(callback.from_user.id, "consent.docs", local_today().isoformat())
    await callback.message.edit_text(t("consent.thanks"))
    await callback.answer()
    token = parts[2] if len(parts) > 2 else "-"
    if len(_waiting_files) > 200:  # не копим ссылки на старые сообщения
        _waiting_files.clear()
    original = _waiting_files.pop(token, None)
    if original is not None and pending.pop(token, callback.from_user.id) is not None:
        await _ask_what_to_do(original, bot)


@router.message(F.chat.type == ChatType.PRIVATE)
async def on_unknown_command(message: Message) -> None:
    await message.answer(t("bot.unknown_command"))


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(callback: CallbackQuery, bot: Bot) -> None:
    _, mode_key, token = callback.data.split(":", 2)
    item = pending.pop(token, callback.from_user.id)
    if item is None:
        await callback.answer(t("bot.pick_expired"), show_alert=True)
        return
    mode = get_mode(mode_key)
    await callback.answer()
    await callback.message.edit_text(t("bot.picked", mode=f"{mode.emoji} {mode.title()}"))
    if mode_key == "dogovor":
        from backend.app.telegram.handlers.agreements import create_and_send

        await create_and_send(callback.message, item.text, callback.from_user)
        return
    attachments = item.attachments if mode.accepts_files else []
    if item.attachments and not mode.accepts_files and not item.text:
        await callback.message.answer(t("bot.pick_file_only"))
        return
    await run_check(callback.message, bot, mode_key, item.text, item.origin, attachments, callback.from_user.id,
                    callback.from_user.first_name or "", reply_to=item.reply_to)

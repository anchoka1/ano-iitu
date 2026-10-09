"""Движок проверки — сердце «Вердикта».

Путь сообщения (одинаковый для бота и Mini App):

  текст ─► нормализация ─► защита (кризис → поддержка, лимиты, слежка) ─► анализ
       (признаки мошенничества, ссылки, мнение/манипуляция)
       + сверка реквизитов МУИТ ─► поиск источников в три слоя («Правда»):
         1) своя база: файлы, новости МУИТ, одобренный Радар, слухи с вердиктом модератора
            (тот же слух уже решён модератором — сразу готовый вердикт, без ИИ);
         2) поиск в интернете (search/web.py): официальный сайт и канал МУИТ → крупные СМИ;
         3) ничего не нашлось — честное «Не подтверждено» и кнопка «Отправить модератору»
       ─► репутация источника
       ─► языковая модель (или демо) по схеме VerdictCard
       ─► правила честности (cards/rules.py) ─► сохранение в базу
       ─► уведомление семьи (если 🔴) ─► карточка

Режимы отличаются только настройками в modes/registry.py и промптами.
Движок не знает про Telegram и HTTP: на входе — простые данные, на
выходе — карточка. Поэтому его легко тестировать.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

from backend.app.cards.rules import apply_reputation, enforce, enforce_university
from backend.app.cards.schema import (
    AgreementDraft, Reason, ResumeReview, SimpleExplanation, StepPlan, SyllabusCard, TrainerReply, TrainerReview, VerdictCard,
)
from backend.app.core.config import get_settings
from backend.app.core.guard import RateLimiter, is_surveillance_request
from backend.app.core.links import analyze_links
from backend.app.core.notify import get_notifier
from backend.app.core.signals import MANIPULATION_RULES, SCAM_RULES, find_signals, is_opinion
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.llm.base import Attachment, LLMClient, LLMError, LLMRequest
from backend.app.llm.factory import get_llm_client
from backend.app.llm.prompts import system_prompt
from backend.app.modes.registry import Mode, get_mode
from backend.app.rag.store import Retrieved, search_all
from backend.app.security.masking import mask_sensitive
from backend.app.university import crisis, requisites

log = logging.getLogger("verdikt.engine")

MAX_INPUT_CHARS = 4000


class EngineError(Exception):
    """Понятная пользователю ошибка (лимит, пустой текст, сбой ИИ)."""


@dataclass
class CheckInput:
    mode: str
    text: str
    user_id: int
    user_name: str = ""
    chat_id: int | None = None
    origin: str = ""  # откуда переслано: @канал или название
    attachments: list[Attachment] = field(default_factory=list)
    save: bool = True  # False — не сохранять (inline-режим)
    thread_id: int | None = None  # тема (топик) группы, где вызвали бота
    topic: str = ""  # название темы («Math») — контекст вопроса, не часть проверяемого текста


@dataclass
class CheckOutcome:
    card: VerdictCard
    check_id: int | None
    provider: str
    cached: bool = False


def normalize(text: str) -> str:
    """Нормализация: убираем лишние пробелы и невидимые символы, ограничиваем длину."""
    text = (text or "").replace("​", "").replace("﻿", "").replace("\r\n", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line).strip()
    return text[:MAX_INPUT_CHARS]


def _escape_tag(text: str) -> str:
    """Не даём тексту пользователя «закрыть» наш тег и выдать себя за инструкцию."""
    return text.replace("</user_content", "&lt;/user_content").replace("<user_content", "&lt;user_content")


def build_user_prompt(text: str, retrieved: list[Retrieved], signals: list, links: list, extra: str = "") -> str:
    """Запрос к модели. Текст пользователя маскируется: карты, ИИН, телефоны и номера документов модели не нужны."""
    parts = [f"<today>{date.today().isoformat()}</today>"]
    if retrieved:
        items = [
            f'<source title="{html.escape(r.source.title)}" url="{html.escape(r.source.url)}" date="{r.source.date}" '
            f'publisher="{html.escape(r.source.publisher)}" is_demo="{str(r.source.is_demo).lower()}">\n{_escape_tag(r.snippet)}\n</source>'
            for r in retrieved
        ]
        parts.append("<sources>\n" + "\n".join(items) + "\n</sources>")
    else:
        parts.append("<sources>Подходящих источников в базе не найдено.</sources>")
    if signals:
        parts.append("<signals>\n" + "\n".join(f"- {s.title}: {', '.join(s.quotes)}" for s in signals) + "\n</signals>")
    if links:
        parts.append("<links>\n" + "\n".join(f"- {link.url}: {' '.join(link.findings)}" for link in links) + "\n</links>")
    if extra:
        parts.append(extra)
    parts.append(f"<user_content>\n{_escape_tag(mask_for_llm(text))}\n</user_content>")
    return "\n\n".join(parts)


def mask_for_llm(text: str) -> str:
    """Маскирование перед отправкой во внешнюю модель. Официальные реквизиты МУИТ (БИН, счёт) оставляем:
    по ним модель сверяет, настоящий ли счёт."""
    keep = [n for n in requisites.official_numbers() if n and n in text]
    for i, number in enumerate(keep):
        text = text.replace(number, f"OFFICIALNUM{i}X")
    masked = mask_sensitive(text)
    for i, number in enumerate(keep):
        masked = masked.replace(f"OFFICIALNUM{i}X", number)
    return masked


def refusal_card() -> VerdictCard:
    return VerdictCard(
        status="unknown", kind="insufficient",
        title="С этим не помогу: ANO IITU не используется для слежки за людьми",
        reasons=[Reason(text="Узнавать местоположение человека, читать его переписку или «пробивать» по номеру без его согласия нельзя.")],
        do=["Если вам угрожают или человек пропал — обратитесь в полицию (102)"],
        dont=["Не устанавливайте шпионские программы на чужие телефоны"],
        confidence=100,
    )


class VerdictEngine:
    def __init__(self, llm: LLMClient | None = None, limiter: RateLimiter | None = None) -> None:
        settings = get_settings()
        self.llm = llm or get_llm_client()
        self.limiter = limiter or RateLimiter(settings.checks_per_minute, settings.checks_per_day)
        self._cache: OrderedDict[str, str] = OrderedDict()  # ключ -> JSON карточки

    # ------------------------------------------------------------ служебное

    def _check_budget(self, user_id: int) -> None:
        if self.llm.name == "mock":
            return
        with get_sessionmaker()() as session:
            used = repo.tokens_today(session, user_id)
        if used >= get_settings().llm_daily_tokens_per_user:
            raise EngineError("Дневной лимит ИИ для вас исчерпан. Попробуйте завтра.")

    async def _call(self, request: LLMRequest):
        try:
            result = await self.llm.generate(request)
        except LLMError as exc:
            log.warning("LLM ошибка (%s): %s", request.task, exc)
            raise EngineError(str(exc)) from exc
        if result.input_tokens or result.output_tokens:
            with get_sessionmaker()() as session:
                repo.add_usage(session, request.user_id, result.provider, result.model, request.task, result.input_tokens, result.output_tokens)
        log.info("LLM %s: %s/%s, токены %d+%d", request.task, result.provider, result.model, result.input_tokens, result.output_tokens)
        return result

    def _cache_key(self, mode: str, text: str, attachments: list[Attachment]) -> str:
        digest = hashlib.sha256()
        digest.update(f"{mode}\n{text}".encode())
        for att in attachments:
            digest.update(att.data_b64.encode())
        return digest.hexdigest()

    # ------------------------------------------------------------ проверка

    async def check(self, inp: CheckInput) -> CheckOutcome:
        mode = get_mode(inp.mode)
        if mode is None or not mode.is_check:
            raise EngineError("Неизвестный режим проверки.")
        text = normalize(inp.text)
        if not text and not inp.attachments:
            raise EngineError("Пришлите текст, который нужно проверить.")
        if inp.attachments and not mode.accepts_files:
            raise EngineError("Фото и PDF принимают только режимы «Чек» и «Развод?».")

        log.info("Проверка %s от %s: %s", mode.key, inp.user_id, mask_sensitive(text[:120]))

        if is_surveillance_request(text):
            card = refusal_card()
            return CheckOutcome(card, self._save(inp, mode, text, card, "guard"), "guard")

        # Кризис (мысли о вреде себе, тревога, выгорание): не выносим вердикт, а показываем,
        # куда обратиться. Без ИИ и без лимитов — помощь не должна зависеть от квоты.
        level = crisis.detect(text) if mode.key != "obyavlenie" else ""
        if level:
            card = crisis.support_card(level)
            return CheckOutcome(card, self._save(inp, mode, text, card, "guard"), "guard")

        limit_error = self.limiter.check(inp.user_id)
        if limit_error:
            raise EngineError(limit_error)

        claim = ""
        if mode.key == "pravda" and text:
            from backend.app.services import facts

            claim = facts.claim_key(text)
            ready = facts.ready_verdict(claim) if claim else None
            if ready is not None:
                # Слой 1: этот слух модератор уже проверил — отдаём готовый вердикт.
                return CheckOutcome(ready, self._save(inp, mode, text, ready, "community", claim), "community")

        key = self._cache_key(mode.key, text, inp.attachments)
        if key in self._cache:
            card = VerdictCard.model_validate_json(self._cache[key])
            self._cache.move_to_end(key)
            return CheckOutcome(card, self._save(inp, mode, text, card, "cache", claim), "cache", cached=True)

        self._check_budget(inp.user_id)

        # --- анализ без ИИ ---
        settings = get_settings()
        scam = find_signals(text, SCAM_RULES) + requisites.check(text) if mode.use_signals else []
        scam.sort(key=lambda h: h.weight, reverse=True)
        manipulation = find_signals(text, MANIPULATION_RULES) if mode.key == "pravda" else []
        links = await analyze_links(text, settings.link_check_online) if mode.use_signals else []
        retrieved = search_all(text, mode.key) if mode.use_rag and text else []
        web_note = ""
        if mode.key == "pravda" and text:
            # Слой 2: интернет. Официальные источники МУИТ — первыми, затем крупные СМИ.
            from backend.app.search import web

            if web.enabled():
                results, worked = await web.search_checked(text)
                found = web.to_retrieved(results)
                retrieved = (retrieved + found)[:7]
                web_note = "" if found else ("Поиск в интернете не нашёл публикаций об этом." if worked
                                             else "Поиск в интернете сейчас недоступен — проверено только по своей базе. Попробуй позже.")
            else:
                web_note = "Поиск в интернете не подключён — проверено только по своей базе."

        # --- репутация источника ---
        origin_keys = [k for k in [inp.origin.lower()] + [link.domain for link in links if not link.is_official] if k]
        with get_sessionmaker()() as session:
            bad = repo.bad_origins(session, origin_keys)

        system, prompt_version = system_prompt(mode.prompt)
        signals_for_prompt = scam + manipulation
        extra = f"<origin>{html.escape(inp.origin)}</origin>" if inp.origin else ""
        if inp.topic:
            # Тема группы («Math») — контекст: вопрос в этой теме понимается как вопрос по этому предмету.
            extra += f"\n<chat_topic>{html.escape(inp.topic[:128])}</chat_topic>"
        request = LLMRequest(
            task=f"check:{mode.key}",
            system=system,
            user=build_user_prompt(text, retrieved, signals_for_prompt, links, extra),
            schema=VerdictCard,
            attachments=inp.attachments,
            user_id=inp.user_id,
            context={
                "mode": mode.key, "text": text, "signals": scam, "manipulation": manipulation,
                "opinion": is_opinion(text), "links": links, "sources": retrieved,
                "has_attachments": bool(inp.attachments), "official_requisites": requisites.matches_official(text),
            },
        )
        result = await self._call(request)
        card: VerdictCard = result.data  # type: ignore[assignment]
        if result.provider != "mock":
            # notes — служебные пометки движка; что туда написала модель, не показываем.
            card.notes = []
        card = enforce(card, mode, text, retrieved)
        if web_note and card.status == "unknown":
            card.notes.append(web_note)
        card = enforce_university(card, mode, text, retrieved)
        card = apply_reputation(card, bad)
        log.info("Вердикт %s: %s (%s%%), промпт %s", mode.key, card.status, card.confidence, prompt_version)

        self._cache[key] = card.model_dump_json()
        if len(self._cache) > 500:
            self._cache.popitem(last=False)  # выбрасываем самую старую запись

        check_id = self._save(inp, mode, text, card, result.provider, claim)
        if inp.save and origin_keys:
            with get_sessionmaker()() as session:
                repo.update_reputation(session, origin_keys, card.status)
        if check_id and card.status == "red" and mode.key in ("razvod", "chek"):
            await self._alert_family(inp, card, check_id)
            # «Группы и потоки»: анонимно предупреждаем одногруппников — похожее может прийти и им.
            from backend.app.services.circles import alert_members

            await alert_members(inp.user_id, card.title)
        return CheckOutcome(card, check_id, result.provider)

    def _save(self, inp: CheckInput, mode: Mode, text: str, card: VerdictCard, provider: str, claim: str = "") -> int | None:
        if not inp.save:
            return None
        with get_sessionmaker()() as session:
            repo.upsert_user(session, inp.user_id, inp.user_name)
            # В истории храним текст с замаскированными картами, ИИН, телефонами и номерами документов.
            check = repo.save_check(
                session, user_id=inp.user_id, chat_id=inp.chat_id, mode=mode.key,
                input_text=mask_sensitive(text) or "(файл)", origin=inp.origin[:256], status=card.status,
                card_json=card.model_dump_json(), provider=provider, thread_id=inp.thread_id, claim_key=claim,
            )
            return check.id

    async def _alert_family(self, inp: CheckInput, card: VerdictCard, check_id: int) -> int:
        """Уведомляет близких, если у человека есть «Семья». Возвращает число отправленных."""
        notifier = get_notifier()
        if notifier is None:
            return 0
        with get_sessionmaker()() as session:
            user = repo.get_user(session, inp.user_id)
            if not user or not user.family_id:
                return 0
            members = [m for m in repo.family_members(session, user.family_id)
                       if m.telegram_id != inp.user_id and m.bot_started and m.family_notify]
            name = user.first_name or "Ваш близкий"
        text = (
            f"🔔 <b>Семья:</b> {html.escape(name)} получил(а) подозрительное сообщение.\n"
            f"Бот определил: 🔴 <b>{html.escape(card.title)}</b>\n\n"
            "Позвоните и убедитесь, что всё в порядке и никто не сообщил коды и не перевёл деньги."
        )
        sent = 0
        for member in members:
            if await notifier.send(member.telegram_id, text):
                sent += 1
        if sent:
            log.info("Семья: отправлено уведомлений — %d", sent)
        return sent

    # ------------------------------------------------------------ прочие задачи

    async def simplify(self, card: VerdictCard, user_id: int) -> str:
        system, _ = system_prompt("simplify", with_base=False)
        request = LLMRequest(
            task="simplify", system=system, schema=SimpleExplanation, user_id=user_id, max_tokens=800,
            user=f"<card>\n{card.model_dump_json(exclude={'notes'})}\n</card>", context={"card": card},
        )
        return (await self._call(request)).data.text  # type: ignore[attr-defined]

    async def summarize_news(self, title: str, lead: str) -> str:
        """Пересказ новости МУИТ своими словами (1–2 предложения, только по тексту новости)."""
        system, _ = system_prompt("news_summary", with_base=False)
        request = LLMRequest(
            task="news_summary", system=system, schema=SimpleExplanation, user_id=None, max_tokens=400,
            user=f"<news_title>{_escape_tag(title)}</news_title>\n<user_content>\n{_escape_tag(lead[:1500])}\n</user_content>",
            context={"title": title, "lead": lead},
        )
        return (await self._call(request)).data.text  # type: ignore[attr-defined]

    async def extract_agreement(self, text: str, user_id: int) -> AgreementDraft:
        text = normalize(text)
        if not text:
            raise EngineError("Опишите договорённость: кто, что, сколько и когда.")
        limit_error = self.limiter.check(user_id)
        if limit_error:
            raise EngineError(limit_error)
        self._check_budget(user_id)
        system, _ = system_prompt("dogovorilis", with_base=False)
        request = LLMRequest(
            task="agreement", system=system, schema=AgreementDraft, user_id=user_id, max_tokens=1000,
            user=f"<today>{date.today().isoformat()}</today>\n<user_content>\n{_escape_tag(mask_for_llm(text))}\n</user_content>",
            context={"text": text, "today": date.today()},
        )
        return (await self._call(request)).data  # type: ignore[return-value]

    async def parse_syllabus(self, text: str, user_id: int, attachments: list[Attachment] | None = None) -> SyllabusCard:
        """Разбор силлабуса (расширение режима «Чек»): дедлайны, веса, правила пересдач и пропусков."""
        text = normalize(text)
        if not text and not attachments:
            raise EngineError("Пришлите текст силлабуса или его фото.")
        limit_error = self.limiter.check(user_id)
        if limit_error:
            raise EngineError(limit_error)
        self._check_budget(user_id)
        system, _ = system_prompt("syllabus", with_base=False)
        request = LLMRequest(
            task="syllabus", system=system, schema=SyllabusCard, user_id=user_id, max_tokens=2500, attachments=attachments or [],
            user=f"<today>{date.today().isoformat()}</today>\n<user_content>\n{_escape_tag(mask_for_llm(text))}\n</user_content>",
            context={"text": text, "today": date.today(), "has_attachments": bool(attachments)},
        )
        return (await self._call(request)).data  # type: ignore[return-value]

    async def split_steps(self, title: str, deadline: str, user_id: int) -> list[dict]:
        """«Разбей на шаги»: план работы с датами до дедлайна. Работу за студента не выполняет."""
        self._check_budget(user_id)
        system, _ = system_prompt("steps", with_base=False)
        request = LLMRequest(
            task="steps", system=system, schema=StepPlan, user_id=user_id, max_tokens=800,
            user=f"<today>{date.today().isoformat()}</today>\n<deadline>{html.escape(deadline or '')}</deadline>\n"
                 f"<user_content>\n{_escape_tag(normalize(title))}\n</user_content>",
            context={"title": title, "deadline": deadline, "today": date.today()},
        )
        plan: StepPlan = (await self._call(request)).data  # type: ignore[assignment]
        steps = []
        for step in plan.steps[:8]:
            due = step.due_date if (not deadline or not step.due_date or step.due_date <= deadline) else deadline
            if step.title.strip():
                steps.append({"title": step.title.strip()[:200], "due_date": due})
        return steps

    async def review_resume(self, text: str, user_id: int) -> ResumeReview:
        """Разбор резюме (карьерный трек, расширение режима «Чек»)."""
        text = normalize(text)
        if len(text) < 40:
            raise EngineError("Пришлите текст резюме целиком (хотя бы несколько строк).")
        limit_error = self.limiter.check(user_id)
        if limit_error:
            raise EngineError(limit_error)
        self._check_budget(user_id)
        system, _ = system_prompt("resume", with_base=False)
        request = LLMRequest(
            task="resume", system=system, schema=ResumeReview, user_id=user_id, max_tokens=1200,
            user=f"<user_content>\n{_escape_tag(mask_sensitive(text))}\n</user_content>", context={"text": text},
        )
        return (await self._call(request)).data  # type: ignore[return-value]

    async def trainer_reply(self, scenario, dialog: list[dict], user_id: int) -> TrainerReply:
        self._check_budget(user_id)
        system, _ = system_prompt("trainer_scammer", with_base=False)
        transcript = "\n".join(f"{'Собеседник' if m['role'] == 'bot' else 'Пользователь'}: {_escape_tag(mask_for_llm(m['text']))}" for m in dialog)
        request = LLMRequest(
            task="trainer_reply", system=system, schema=TrainerReply, user_id=user_id, max_tokens=600,
            user=f"<scenario>{scenario.role}</scenario>\n<dialog>\n{transcript}\n</dialog>\nНапиши следующую реплику собеседника.",
            context={"scenario": scenario, "dialog": dialog},
        )
        return (await self._call(request)).data  # type: ignore[return-value]

    async def trainer_review(self, scenario, dialog: list[dict], user_id: int) -> TrainerReview:
        system, _ = system_prompt("trainer_review", with_base=False)
        transcript = "\n".join(f"{'Собеседник' if m['role'] == 'bot' else 'Пользователь'}: {_escape_tag(mask_for_llm(m['text']))}" for m in dialog)
        request = LLMRequest(
            task="trainer_review", system=system, schema=TrainerReview, user_id=user_id, max_tokens=1500,
            user=f"<scenario>{scenario.role}\nПриёмы: {', '.join(scenario.red_flags)}</scenario>\n<dialog>\n{transcript}\n</dialog>",
            context={"scenario": scenario, "dialog": dialog},
        )
        return (await self._call(request)).data  # type: ignore[return-value]


_engine: VerdictEngine | None = None


def get_verdict_engine() -> VerdictEngine:
    """Один движок на всё приложение (создаётся при первом обращении)."""
    global _engine
    if _engine is None:
        _engine = VerdictEngine()
    return _engine


def reset_verdict_engine() -> None:
    """Для тестов и после смены настроек."""
    global _engine
    _engine = None


def card_from_json(raw: str) -> VerdictCard:
    return VerdictCard.model_validate(json.loads(raw))

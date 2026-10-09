"""Карточка вердикта в виде текста для Telegram (HTML-разметка).

Telegram поддерживает упрощённый HTML: <b>, <i>, <u>, <a href>, <code>,
<blockquote>. Весь текст от пользователя и модели обязательно
экранируем (html.escape): иначе «<» в сообщении сломает разметку, а
злоумышленник мог бы вставить свою ссылку.

Подсветка фраз: показываем фрагмент исходного сообщения, где найденные
цитаты выделены жирным с подчёркиванием.
"""

from __future__ import annotations

import html
import re

from backend.app.cards.schema import STATUS_EMOJI, VerdictCard
from backend.app.i18n import t

TELEGRAM_LIMIT = 4096
STATUS_LABEL_KEYS = {"green": "card.status.green", "yellow": "card.status.yellow", "red": "card.status.red", "unknown": "card.status.unknown"}


def confidence_bar(value: int) -> str:
    filled = round(value / 20)
    return "▰" * filled + "▱" * (5 - filled)


def highlight_html(text: str, quotes: list[str], limit: int = 500) -> str:
    """Возвращает экранированный фрагмент текста с выделенными цитатами."""
    spans: list[tuple[int, int]] = []
    lowered = text.lower()
    for quote in quotes:
        q = quote.strip().lower()
        if len(q) < 2:
            continue
        start = lowered.find(q)
        if start >= 0:
            spans.append((start, start + len(q)))
    spans.sort()
    # Окно текста вокруг первой цитаты, если текст длинный
    window_start = 0
    if len(text) > limit and spans:
        window_start = max(0, spans[0][0] - limit // 4)
    window_end = min(len(text), window_start + limit)

    out: list[str] = []
    pos = window_start
    for start, end in spans:
        if start < pos or end > window_end:
            continue  # пересекается с предыдущей подсветкой или за пределами окна
        out.append(html.escape(text[pos:start]))
        out.append(f"<b><u>{html.escape(text[start:end])}</u></b>")
        pos = end
    out.append(html.escape(text[pos:window_end]))
    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_end < len(text) else ""
    return prefix + "".join(out) + suffix


def render_card_html(card: VerdictCard, input_text: str = "", votes: tuple[int, int] | None = None, truth: dict | None = None) -> str:
    """truth — для «Правды»: шкала (Подтверждено / Опровергнуто / Частично / Не подтверждено / На проверке) и счётчик."""
    e = html.escape
    if card.kind == "support":
        # Поддержка вместо вердикта: без цветного статуса и шкалы уверенности.
        head = f"💙 <b>{e(t('card.status.support'))}</b>"
    elif truth:
        head = f"{STATUS_EMOJI[card.status]} <b>{e(truth['label'])}</b>"
        if truth.get("times_checked", 0) > 1:
            head += f" · {e(t('truth.times', n=truth['times_checked']))}"
    else:
        head = f"{STATUS_EMOJI[card.status]} <b>{e(t(STATUS_LABEL_KEYS[card.status]))}</b>"
    lines: list[str] = [head, f"<b>{e(card.title)}</b>"]
    if card.answer:
        lines += ["", f"🎓 <b>{e(t('card.answer'))}</b> {e(card.answer)}"]

    quotes = [r.quote for r in card.reasons if r.quote]
    if input_text and quotes:
        lines += ["", f"<blockquote>{highlight_html(input_text, quotes)}</blockquote>"]

    if card.reasons:
        lines += ["", f"<b>{e(t('card.reasons'))}</b>"]
        for i, reason in enumerate(card.reasons, 1):
            quote = f" — «<i>{e(reason.quote)}</i>»" if reason.quote else ""
            lines.append(f"{i}. {e(reason.text)}{quote}")

    if card.dispute:
        d = card.dispute
        lines += ["", f"<b>{e(t('card.dispute.a'))}</b> {e(d.position_a)}", f"<b>{e(t('card.dispute.b'))}</b> {e(d.position_b)}"]
        for key, items in (("card.dispute.facts", d.confirmed_facts), ("card.dispute.claims", d.unconfirmed_claims),
                           ("card.dispute.prove", d.to_prove), ("card.dispute.options", d.options)):
            if items:
                lines.append(f"<b>{e(t(key))}</b>")
                lines += [f"• {e(x)}" for x in items]
        if d.neutral_message:
            lines += [f"<b>{e(t('card.dispute.message'))}</b>", f"<blockquote>{e(d.neutral_message)}</blockquote>"]

    if card.rewrite:
        calm_key = "card.announce.better" if card.kind == "announcement" else "card.rewrite.calm"
        lines += ["", f"<b>{e(t(calm_key))}</b>", f"<blockquote>{e(card.rewrite.calm_text)}</blockquote>"]
        if card.rewrite.how_it_sounds:
            lines.append(f"<i>{e(t('card.rewrite.sounds'))}</i> {e(card.rewrite.how_it_sounds)}")

    if card.document:
        doc = card.document
        for key, value in (("card.doc.want", doc.what_they_want), ("card.doc.amount", doc.amount), ("card.doc.deadline", doc.deadline),
                           ("card.doc.where", doc.where_to_go), ("card.doc.ignore", doc.if_ignore)):
            if value:
                lines.append(f"<b>{e(t(key))}</b> {e(value)}")

    if card.sources:
        lines += ["", f"<b>{e(t('card.sources'))}</b>"]
        for s in card.sources:
            title = e(s.title)
            link = f'<a href="{e(s.url, quote=True)}">{title}</a>' if s.url.startswith("http") else title
            date = f" ({e(t('card.actual_on'))} {e(s.date)})" if s.date else ""
            lines.append(f"• {link}{date}")

    if card.kind != "support":
        lines += ["", f"<b>{e(t('card.confidence'))}</b> {confidence_bar(card.confidence)} {card.confidence}%"]
    if card.do:
        lines += ["", f"✅ <b>{e(t('card.do'))}</b>"] + [f"• {e(x)}" for x in card.do]
    if card.dont:
        lines += ["", f"⛔ <b>{e(t('card.dont'))}</b>"] + [f"• {e(x)}" for x in card.dont]
    if card.claim_letter:
        lines += ["", f"📝 <b>{e(t('card.claim_letter'))}</b>", f"<pre>{e(card.claim_letter)}</pre>"]
    if votes and (votes[0] or votes[1]):
        lines += ["", e(t("card.votes", agree=votes[0], disagree=votes[1]))]
    if truth and truth.get("code") == "unconfirmed":
        lines += ["", e(t("truth.howto"))]
        if truth.get("can_escalate"):
            lines.append(f"<i>{e(t('truth.escalate_hint'))}</i>")
    for note in card.notes:
        lines.append(f"<i>ℹ️ {e(note)}</i>")
    lines += ["", f"<i>{e(t('card.disclaimer'))}</i>"]

    text = "\n".join(lines)
    if len(text) > TELEGRAM_LIMIT:
        # Обрезаем по целым строкам, чтобы не сломать HTML-теги посередине.
        kept: list[str] = []
        size = 0
        for line in lines:
            if size + len(line) + 1 > TELEGRAM_LIMIT - 40:
                kept.append("<i>… карточка сокращена, полная версия — в приложении</i>")
                break
            kept.append(line)
            size += len(line) + 1
        text = "\n".join(kept)
    return text


def strip_emoji(text: str) -> str:
    """Убирает эмодзи (системный шрифт их не нарисует на картинке)."""
    return re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", text).strip()

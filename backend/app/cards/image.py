"""Карточка вердикта картинкой (PNG) — чтобы переслать в любой чат.

Pillow (PIL) — библиотека для рисования. Создаём «холст» Image, на нём
через ImageDraw рисуем прямоугольники, кружок статуса и текст.
Высота картинки заранее неизвестна, поэтому сначала раскладываем текст
по строкам (перенос по ширине), считаем высоту, потом рисуем.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from backend.app.cards.render import strip_emoji
from backend.app.cards.schema import VerdictCard
from backend.app.core.fonts import find_fonts
from backend.app.i18n import t

WIDTH = 1080
PADDING = 64
COLORS = {"green": (31, 157, 85), "yellow": (230, 168, 23), "red": (214, 69, 69), "unknown": (140, 146, 156)}
SUPPORT = (47, 111, 222)
# Цвета в стиле сайта МУИТ (iitu.edu.kz): малиновый #A42421 и графитовый #232323
BRAND = (164, 36, 33)
BG = (245, 245, 245)
TEXT = (35, 35, 35)
HINT = (107, 114, 128)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Перенос строк по ширине: добавляем слова, пока строка помещается."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_card_png(card: VerdictCard) -> bytes:
    regular_path, bold_path = find_fonts()
    f_title = ImageFont.truetype(bold_path, 46)
    f_head = ImageFont.truetype(bold_path, 32)
    f_body = ImageFont.truetype(regular_path, 30)
    f_small = ImageFont.truetype(regular_path, 24)

    # Блоки: (шрифт, текст, цвет, отступ сверху)
    support = card.kind == "support"
    status_color = SUPPORT if support else COLORS[card.status]
    status_label = t("card.status.support") if support else t(f"card.status.{card.status}")
    blocks: list[tuple] = [(f_small, "ANO IITU · РЕЗУЛЬТАТ ПРОВЕРКИ", BRAND, 0),
                           (f_head, status_label.upper(), status_color, 14), (f_title, strip_emoji(card.title), TEXT, 12)]
    if card.answer:
        blocks += [(f_head, t("card.answer"), TEXT, 30), (f_body, strip_emoji(card.answer), TEXT, 10)]
    if card.reasons:
        blocks.append((f_head, t("card.reasons"), TEXT, 36))
        for i, r in enumerate(card.reasons, 1):
            quote = f" — «{r.quote}»" if r.quote else ""
            blocks.append((f_body, f"{i}. {strip_emoji(r.text)}{quote}", TEXT, 10))
    if card.rewrite and card.rewrite.calm_text:
        blocks += [(f_head, t("card.rewrite.calm"), TEXT, 36), (f_body, strip_emoji(card.rewrite.calm_text), TEXT, 10)]
    if card.document:
        doc = card.document
        for key, value in (("card.doc.want", doc.what_they_want), ("card.doc.amount", doc.amount), ("card.doc.deadline", doc.deadline)):
            if value:
                blocks.append((f_body, f"{t(key)} {value}", TEXT, 10))
    if card.do:
        blocks.append((f_head, t("card.do"), (31, 157, 85), 36))
        blocks += [(f_body, f"• {strip_emoji(x)}", TEXT, 8) for x in card.do]
    if card.dont:
        blocks.append((f_head, t("card.dont"), (214, 69, 69), 36))
        blocks += [(f_body, f"• {strip_emoji(x)}", TEXT, 8) for x in card.dont]
    if card.sources:
        blocks.append((f_head, t("card.sources"), TEXT, 36))
        blocks += [(f_small, f"• {s.title} — {s.url} ({s.date})", HINT, 6) for s in card.sources]
    if not support:
        blocks.append((f_small, f"{t('card.confidence')} {card.confidence}%", HINT, 30))
    blocks.append((f_small, t("card.image_footer"), HINT, 10))

    # 1) раскладка: считаем высоту
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text_width = WIDTH - 2 * PADDING
    laid_out = []
    height = PADDING + 40
    for font, text, color, gap in blocks:
        lines = _wrap(probe, text, font, text_width)
        line_h = int(font.size * 1.35)
        laid_out.append((font, lines, color, gap, line_h))
        height += gap + line_h * len(lines)
    height += PADDING

    # 2) рисование
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, 18], fill=BRAND)  # полоса в цвете МУИТ
    draw.rectangle([0, 18, WIDTH, 26], fill=status_color)  # тонкая полоса статуса
    y = PADDING + 10
    first = True
    status_row = 1  # кружок статуса — у строки со статусом (после строки бренда)
    for index, (font, lines, color, gap, line_h) in enumerate(laid_out):
        y += gap
        x = PADDING
        if first and index == status_row:  # кружок статуса перед строкой статуса
            r = 14
            draw.ellipse([x, y + 6, x + 2 * r, y + 6 + 2 * r], fill=status_color)
            x += 2 * r + 16
            first = False
        for line in lines:
            draw.text((x, y), line, font=font, fill=color)
            y += line_h
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

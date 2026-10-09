"""PDF-протокол договорённости.

fpdf2 — библиотека для создания PDF. add_font подключает TTF-шрифт с
кириллицей, multi_cell печатает текст с переносом строк.

Важно и честно: PDF — это фиксация договорённости и подтверждения в
переписке, а НЕ договор и не нотариальный документ.
"""

from __future__ import annotations

import json

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from backend.app.core.fonts import find_fonts
from backend.app.db.models import Agreement
from backend.app.i18n import t

STATUS_KEYS = {"pending": "agr.status.pending", "confirmed": "agr.status.confirmed", "declined": "agr.status.declined",
               "done": "agr.status.done", "cancelled": "agr.status.cancelled"}


def agreement_pdf(agreement: Agreement) -> bytes:
    regular, bold = find_fonts()
    draft = json.loads(agreement.draft_json or "{}")
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.add_font("Main", "", regular)
    pdf.add_font("Main", "B", bold)

    def line(height: float, text: str) -> None:
        # new_x/new_y: после блока текста вернуться к левому краю и перейти на новую строку
        # (по умолчанию курсор остаётся справа, и следующий блок не помещается).
        pdf.multi_cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Main", "B", 18)
    line(10, t("agr.pdf.title"))
    pdf.set_font("Main", "", 10)
    line(6, t("agr.pdf.code", code=agreement.code))
    pdf.ln(4)

    def row(label: str, value: str) -> None:
        if not value:
            return
        pdf.set_font("Main", "B", 11)
        line(7, label)
        pdf.set_font("Main", "", 11)
        line(7, value)
        pdf.ln(1)

    created = agreement.created_at.strftime("%d.%m.%Y %H:%M") + " UTC"
    confirmed = agreement.confirmed_at.strftime("%d.%m.%Y %H:%M") + " UTC" if agreement.confirmed_at else "—"
    row(t("agr.field.creator"), agreement.creator_name or str(agreement.creator_id))
    row(t("agr.field.counterparty"), agreement.counterparty_name or agreement.counterparty_username or "—")
    row(t("agr.field.who"), draft.get("who", ""))
    row(t("agr.field.what"), draft.get("what", "") or agreement.text)
    row(t("agr.field.amount"), draft.get("amount", ""))
    row(t("agr.field.deadline"), draft.get("deadline", "") + (f" ({agreement.deadline_iso})" if agreement.deadline_iso else ""))
    row(t("agr.field.breach"), draft.get("on_breach", ""))
    row(t("agr.field.status"), t(STATUS_KEYS.get(agreement.status, "agr.status.pending")))
    row(t("agr.field.created"), created)
    row(t("agr.field.confirmed"), confirmed)
    row(t("agr.field.original"), agreement.text)

    pdf.ln(4)
    pdf.set_font("Main", "", 9)
    line(5, t("agr.disclaimer"))
    return bytes(pdf.output())

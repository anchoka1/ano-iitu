"""Договорённости (/dogovorilis): создание, подтверждение второй стороной, статусы.

Поток:
  1. Человек описывает договорённость → движок выделяет поля (кто, что,
     сколько, когда, что при срыве) → запись со статусом «ожидает».
  2. Вторая сторона нажимает «✅ Подтверждаю» (в группе — под сообщением,
     в личке — по ссылке t.me/бот?start=agr_КОД). Подтвердить может только
     НЕ автор; если указан @username — только этот человек.
  3. Планировщик напоминает накануне срока и в день срока.
Честно: это фиксация договорённости и доказательство переписки, а не
замена договора.

Договорённость с группой (multi=True, например преподаватель и студенты):
подтверждает или отклоняет КАЖДЫЙ участник сам; ответы видны всем.
Статус становится «подтверждена» после первого подтверждения.
"""

from __future__ import annotations

import html
import json
import re

from backend.app.core.engine import get_verdict_engine
from backend.app.core.notify import get_notifier
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import Agreement, AgreementParticipant, iso_utc, utcnow
from backend.app.i18n import t

STATUS_EMOJI = {"pending": "⏳", "confirmed": "✅", "declined": "❌", "done": "🏁", "cancelled": "🚫"}


class AgreementError(Exception):
    pass


async def create_agreement(text: str, creator_id: int, creator_name: str, chat_id: int | None = None, multi: bool = False,
                           thread_id: int | None = None) -> Agreement:
    draft = await get_verdict_engine().extract_agreement(text, creator_id)
    mention = None if multi else re.search(r"@([A-Za-z0-9_]{5,32})", text)
    with get_sessionmaker()() as session:
        repo.upsert_user(session, creator_id, creator_name)
        agreement = repo.create_agreement(
            session, chat_id=chat_id, creator_id=creator_id, creator_name=creator_name[:128],
            counterparty_username=mention.group(1).lower() if mention else "",
            text=text.strip()[:2000], draft_json=draft.model_dump_json(), deadline_iso=draft.deadline_iso, multi=multi,
            deadline_time=_deadline_time(text) if draft.deadline_iso else "", thread_id=thread_id,
        )
    _suggest_to_plan(agreement, {creator_id})
    return agreement


def _deadline_time(text: str) -> str:
    """«до 15 октября 18:00» → «18:00»: чтобы напомнить и за день, и за час."""
    from backend.app.services.planner import TIME_RE

    m = TIME_RE.search(text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _suggest_to_plan(agreement: Agreement, user_ids: set[int]) -> None:
    """Срок договорённости → ПРЕДЛОЖЕННАЯ задача в «Моём плане» участника (сама в план не попадает)."""
    from backend.app.core.features import enabled

    if not (enabled("planner") and enabled("agreement_deadlines")) or not agreement.deadline_iso:
        return
    try:
        from backend.app.services.planner import suggest_from_agreement

        suggest_from_agreement(agreement, user_ids)
    except Exception:  # noqa: BLE001 — планер не должен ломать договорённости
        import logging

        logging.getLogger("verdikt").exception("Не удалось предложить задачу из договорённости")


def get(code: str) -> Agreement | None:
    with get_sessionmaker()() as session:
        return repo.get_agreement(session, code)


def respond(code: str, user_id: int, user_name: str, username: str, accept: bool) -> Agreement:
    """Ответ второй стороны: подтвердить или отклонить."""
    with get_sessionmaker()() as session:
        agreement = repo.get_agreement(session, code)
        if agreement is None:
            raise AgreementError(t("agr.err.not_found"))
        if agreement.creator_id == user_id:
            raise AgreementError(t("agr.err.own"))
        if agreement.multi:
            return _respond_multi(session, agreement, user_id, user_name, accept)
        if agreement.status != "pending":
            raise AgreementError(t("agr.err.not_pending"))
        if agreement.counterparty_username and agreement.counterparty_username != (username or "").lower():
            raise AgreementError(t("agr.err.not_you", username=agreement.counterparty_username))
        repo.upsert_user(session, user_id, user_name)
        agreement.counterparty_id = user_id
        agreement.counterparty_name = user_name[:128]
        agreement.status = "confirmed" if accept else "declined"
        agreement.confirmed_at = utcnow() if accept else None
        session.commit()
        if accept:
            _suggest_to_plan(agreement, {user_id})
        return agreement


def _respond_multi(session, agreement: Agreement, user_id: int, user_name: str, accept: bool) -> Agreement:
    """Ответ участника групповой договорённости: каждый за себя, повторно ответить нельзя."""
    if agreement.status in ("done", "cancelled"):
        raise AgreementError(t("agr.err.not_pending"))
    if repo.get_participant(session, agreement.id, user_id) is not None:
        raise AgreementError(t("agr.err.already"))
    repo.upsert_user(session, user_id, user_name)
    session.add(AgreementParticipant(agreement_id=agreement.id, user_id=user_id, name=user_name[:128], accepted=accept))
    if accept and agreement.status == "pending":
        agreement.status = "confirmed"
        agreement.confirmed_at = utcnow()
    session.commit()
    if accept:
        _suggest_to_plan(agreement, {user_id})
    return agreement


def participants(agreement: Agreement) -> list[dict]:
    if not agreement.multi:
        return []
    with get_sessionmaker()() as session:
        return [{"user_id": p.user_id, "name": p.name, "accepted": p.accepted} for p in repo.agreement_participants(session, agreement.id)]


def is_participant(agreement: Agreement, user_id: int) -> bool:
    if user_id in (agreement.creator_id, agreement.counterparty_id):
        return True
    if agreement.circle_id:
        from backend.app.services.circles import is_member

        if is_member(agreement.circle_id, user_id):
            return True
    return any(p["user_id"] == user_id for p in participants(agreement))


async def notify_creator(agreement: Agreement, accepted: bool) -> None:
    """Сообщает автору, что вторая сторона ответила (если автор открывал бота)."""
    notifier = get_notifier()
    if notifier is None:
        return
    with get_sessionmaker()() as session:
        creator = repo.get_user(session, agreement.creator_id)
    if creator is None or not creator.bot_started:
        return
    what = json.loads(agreement.draft_json or "{}").get("what") or agreement.text
    if agreement.multi:
        people = participants(agreement)
        if not people or not people[-1]["accepted"]:
            return
        n = sum(p["accepted"] for p in people)
        await notifier.send(agreement.creator_id, html.escape(t("agr.notify.multi", name=people[-1]["name"], what=what[:200], n=n)))
        return
    key = "agr.notify.confirmed" if accepted else "agr.notify.declined"
    await notifier.send(agreement.creator_id, html.escape(t(key, name=agreement.counterparty_name, what=what[:200])))


def set_status(code: str, user_id: int, status: str) -> Agreement:
    """Отметить выполненной (любой участник) или отменить (только автор, пока не подтверждена)."""
    with get_sessionmaker()() as session:
        agreement = repo.get_agreement(session, code)
        if agreement is None:
            raise AgreementError(t("agr.err.not_found"))
        member = user_id in (agreement.creator_id, agreement.counterparty_id) or (
            agreement.multi and (p := repo.get_participant(session, agreement.id, user_id)) is not None and p.accepted)
        if not member:
            raise AgreementError(t("agr.err.not_participant"))
        if status == "done" and agreement.status != "confirmed":
            raise AgreementError(t("agr.err.not_confirmed"))
        if status == "cancelled" and (agreement.creator_id != user_id or agreement.status != "pending"):
            raise AgreementError(t("agr.err.cancel"))
        agreement.status = status
        session.commit()
        return agreement


def to_dict(agreement: Agreement) -> dict:
    draft = json.loads(agreement.draft_json or "{}")
    return {
        "code": agreement.code, "status": agreement.status, "status_label": t(f"agr.status.{agreement.status}"),
        "creator_id": agreement.creator_id, "creator_name": agreement.creator_name,
        "counterparty_id": agreement.counterparty_id, "counterparty_name": agreement.counterparty_name,
        "counterparty_username": agreement.counterparty_username, "text": agreement.text, "draft": draft,
        "deadline_iso": agreement.deadline_iso, "created_at": iso_utc(agreement.created_at),
        "confirmed_at": iso_utc(agreement.confirmed_at), "multi": agreement.multi, "participants": participants(agreement),
        "circle_id": agreement.circle_id, "deadline_time": agreement.deadline_time or "",
    }


def render_html(agreement: Agreement) -> str:
    e = html.escape
    draft = json.loads(agreement.draft_json or "{}")
    title = t("agr.multi_title") if agreement.multi else t("agr.title")
    lines = [f"🤝 <b>{e(title)}</b> · {STATUS_EMOJI.get(agreement.status, '')} {e(t('agr.status.' + agreement.status))}", ""]
    for key, value in (("agr.field.who", draft.get("who")), ("agr.field.what", draft.get("what")), ("agr.field.amount", draft.get("amount")),
                       ("agr.field.deadline", draft.get("deadline")), ("agr.field.breach", draft.get("on_breach"))):
        if value:
            lines.append(f"<b>{e(t(key))}</b> {e(value)}")
    lines.append(f"<b>{e(t('agr.field.creator'))}</b> {e(agreement.creator_name)}")
    if agreement.multi:
        people = participants(agreement)
        yes = [p["name"] for p in people if p["accepted"]]
        no = [p["name"] for p in people if not p["accepted"]]
        lines.append(f"<b>{e(t('agr.field.confirmed_by', n=len(yes)))}</b> {e(', '.join(yes) or '—')}")
        if no:
            lines.append(f"<b>{e(t('agr.field.declined_by', n=len(no)))}</b> {e(', '.join(no))}")
        lines.append(f"<i>{e(t('agr.multi_hint'))}</i>")
    elif agreement.counterparty_name:
        lines.append(f"<b>{e(t('agr.field.counterparty'))}</b> {e(agreement.counterparty_name)}")
    elif agreement.counterparty_username:
        lines.append(f"<b>{e(t('agr.field.counterparty'))}</b> @{e(agreement.counterparty_username)}")
    if draft.get("missing"):
        lines += ["", f"⚠️ {e(t('agr.missing'))} " + e("; ".join(draft["missing"]))]
    lines += ["", f"<i>{e(t('agr.disclaimer'))}</i>"]
    return "\n".join(lines)

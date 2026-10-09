"""«Индекс чата»: еженедельная статистика группы без имён.

Только положительное подкрепление: хвалим за проверки и распознанные
обманы, никого не называем и не стыдим.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import t


def week_stats(chat_id: int) -> dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    with get_sessionmaker()() as session:
        return repo.chat_stats(session, chat_id, since)


def render_index(stats: dict[str, int]) -> str:
    if not stats["total"]:
        return t("index.empty")
    lines = [t("index.title"), "", t("index.total", n=stats["total"])]
    if stats["red"]:
        lines.append(t("index.red", n=stats["red"]))
    if stats["unknown"]:
        lines.append(t("index.unknown", n=stats["unknown"]))
    if stats["votes"]:
        lines.append(t("index.votes", n=stats["votes"]))
    lines += ["", t("index.thanks")]
    return "\n".join(lines)

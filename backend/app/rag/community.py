"""База знаний из сообщества: одобренные модератором записи Радара и проверенные слухи.

Это «своя база» (первый слой) для режимов «Правда», «Развод?» и «Чек»: как только модератор
одобрил сообщение о схеме или вынес вердикт по слуху, следующая проверка похожего текста
найдёт эту запись и сошлётся на неё. Кэш сбрасывается при каждом решении модератора (invalidate).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import select

from backend.app.db.base import get_sessionmaker
from backend.app.db.models_campus import Post
from backend.app.i18n import t
from backend.app.rag.store import Retrieved, Source, SourceStore, index_source

RADAR_DAYS = 365


def _date(post: Post) -> str:
    when = post.moderated_at or post.created_at
    return when.date().isoformat() if when else ""


def _sources() -> list[Source]:
    since = datetime.now(timezone.utc) - timedelta(days=RADAR_DAYS)
    out: list[Source] = []
    with get_sessionmaker()() as session:
        rows = session.scalars(select(Post).where(Post.kind.in_(("radar", "rumor")), Post.status == "approved", Post.created_at >= since))
        for post in rows:
            data = json.loads(post.data_json or "{}")
            if post.kind == "radar":
                out.append(index_source(Source(
                    id=f"radar:{post.id}", title=t("kb.radar_title", title=post.title[:120]), url="", date=_date(post),
                    publisher=t("kb.radar_publisher"), modes=["pravda", "razvod", "chek"], verdict="red", claim=post.title,
                    keywords=[], text=f"{post.title}\n{post.body}",
                )))
            elif data.get("verdict"):
                verdict = data["verdict"]
                out.append(index_source(Source(
                    id=f"rumor:{post.id}", title=t("kb.rumor_title", verdict=t(f"truth.{verdict}"), title=post.title[:120]),
                    url=data.get("source_url", ""), date=data.get("decided_on") or _date(post), publisher=t("kb.rumor_publisher"),
                    modes=["pravda"], verdict={"confirmed": "green", "refuted": "red", "partly": "yellow"}.get(verdict, ""), claim=post.title,
                    text=f"{t('kb.rumor_text', verdict=t(f'truth.{verdict}'))}\n{post.title}\n{data.get('comment', '')}",
                )))
    return out


@lru_cache
def get_community_store() -> SourceStore:
    return SourceStore(directory=None, extra=_sources())


def invalidate() -> None:
    get_community_store.cache_clear()


def search(query: str, mode: str | None, limit: int = 2) -> list[Retrieved]:
    return get_community_store().search(query, mode, limit=limit, min_score=2.0)

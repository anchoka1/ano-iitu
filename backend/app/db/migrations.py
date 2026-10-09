"""Миграции базы данных — пронумерованные изменения схемы.

Зачем: база с реальными данными уже есть (data/verdikt.db), и её нельзя
пересоздавать. Каждая миграция — маленькая функция, которая меняет схему
так, чтобы старые данные сохранились. Номер последней применённой
миграции хранится в таблице meta (ключ schema_version), поэтому каждая
миграция выполняется ровно один раз.

Правила:
  - миграции только добавляются в конец списка, старые не меняются;
  - миграция должна быть безопасной при повторе (проверяет, есть ли колонка);
  - перед миграцией делается копия файла базы (data/verdikt.db.bak-<номер>).

Alembic здесь не используем, чтобы не добавлять зависимость: схема
небольшая, а изменения — только добавление таблиц и колонок.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from backend.app.db.base import Base, get_engine

log = logging.getLogger(__name__)

SCHEMA_KEY = "schema_version"


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    existing = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in existing:
        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN "{column}" {ddl}'))
        log.info("Миграция: добавлена колонка %s.%s", table, column)


def _m001_university(conn: Connection) -> None:
    """Университет МУИТ: роли и курс пользователя, групповые договорённости, кэш новостей."""
    for column, ddl in (
        ("role", "VARCHAR(16) DEFAULT ''"),
        ("course", "VARCHAR(8) DEFAULT ''"),
        ("faculty", "VARCHAR(16) DEFAULT ''"),
        ("program", "VARCHAR(16) DEFAULT ''"),
        ("department", "VARCHAR(32) DEFAULT ''"),
        ("ui_lang", "VARCHAR(8) DEFAULT 'ru'"),
        ("onboarded", "BOOLEAN DEFAULT 0"),
        ("course_year", "INTEGER DEFAULT 0"),
        ("news_subscribed", "BOOLEAN DEFAULT 0"),
        ("calendar_reminders", "BOOLEAN DEFAULT 1"),
    ):
        _add_column(conn, "users", column, ddl)
    _add_column(conn, "agreements", "multi", "BOOLEAN DEFAULT 0")
    # Новые таблицы (agreement_participants, news_items) создаёт create_all по описанию в models.py.
    from backend.app.db import models

    for table in (models.AgreementParticipant.__table__, models.NewsItem.__table__):
        table.create(conn, checkfirst=True)


def _m002_circles(conn: Connection) -> None:
    """«Группы и потоки»: группы, участники, объявления; связь договорённости с группой."""
    from backend.app.db import models

    for table in (models.Circle.__table__, models.CircleMember.__table__, models.CirclePost.__table__):
        table.create(conn, checkfirst=True)
    _add_column(conn, "agreements", "circle_id", "INTEGER")


def _m003_campus(conn: Connection) -> None:
    """Планер, анонимные опросы, контент сообщества, учебные хабы; темы групп; статус преподавателя."""
    from backend.app.db import models_campus

    _add_column(conn, "users", "teacher_verified", "BOOLEAN DEFAULT 0")
    _add_column(conn, "chats", "rating_opt_in", "BOOLEAN DEFAULT 0")
    _add_column(conn, "checks", "thread_id", "BIGINT")
    _add_column(conn, "agreements", "thread_id", "BIGINT")
    _add_column(conn, "agreements", "deadline_time", "VARCHAR(5) DEFAULT ''")
    _add_column(conn, "agreements", "reminded_hour", "BOOLEAN DEFAULT 0")
    for model in models_campus.CAMPUS_TABLES:
        model.__table__.create(conn, checkfirst=True)


def _m004_moderation(conn: Connection) -> None:
    """Роли и журнал модерации, причины отказа, файлы, силлабусы, «отпечаток» слуха у проверок."""
    from backend.app.db import models_mod

    _add_column(conn, "posts", "reject_reason", "VARCHAR(512) DEFAULT ''")
    _add_column(conn, "posts", "moderated_by", "BIGINT")
    _add_column(conn, "posts", "notify_enc", "VARCHAR(256) DEFAULT ''")
    _add_column(conn, "checks", "claim_key", "VARCHAR(40) DEFAULT ''")
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_checks_claim_key ON checks (claim_key)"))
    for model in models_mod.MOD_TABLES:
        model.__table__.create(conn, checkfirst=True)


# (номер, описание, функция). Добавлять только в конец.
MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "университет: роли, курс, групповые договорённости, новости", _m001_university),
    (2, "группы и потоки (университетская «Семья»)", _m002_circles),
    (3, "планер, опросы, сообщество, учебные хабы", _m003_campus),
    (4, "роли и журнал модерации, файлы, силлабусы", _m004_moderation),
]


def _db_file() -> Path | None:
    url = get_engine().url
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        return Path(url.database)
    return None


def current_version(conn: Connection) -> int:
    if not inspect(conn).has_table("meta"):
        return 0
    row = conn.execute(text("SELECT value FROM meta WHERE key = :k"), {"k": SCHEMA_KEY}).first()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def run_migrations() -> int:
    """Применяет недостающие миграции. Возвращает итоговую версию схемы."""
    engine = get_engine()
    with engine.connect() as conn:
        version = current_version(conn)
        has_users = inspect(conn).has_table("users")
    pending = [m for m in MIGRATIONS if m[0] > version]
    if not pending:
        return version

    db_file = _db_file()
    if has_users and db_file and db_file.exists() and db_file.stat().st_size > 0:
        backup = db_file.with_name(f"{db_file.name}.bak-{pending[0][0]:03d}")
        if not backup.exists():
            shutil.copy2(db_file, backup)
            log.info("Миграция: копия базы сохранена в %s", backup.name)

    for number, description, func in pending:
        with engine.begin() as conn:  # каждая миграция — своя транзакция
            func(conn)
            conn.execute(text("DELETE FROM meta WHERE key = :k"), {"k": SCHEMA_KEY})
            conn.execute(text("INSERT INTO meta (key, value) VALUES (:k, :v)"), {"k": SCHEMA_KEY, "v": str(number)})
        log.info("Миграция %03d применена: %s", number, description)
        version = number
    return version


def latest_version() -> int:
    return MIGRATIONS[-1][0] if MIGRATIONS else 0


__all__ = ["Base", "run_migrations", "latest_version", "current_version"]

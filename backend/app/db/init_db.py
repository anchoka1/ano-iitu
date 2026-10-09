"""Создание таблиц базы данных.

create_all создаёт только недостающие таблицы и не трогает существующие
данные, поэтому вызывать её при каждом запуске безопасно.

Проблема: если в существующую таблицу добавили новую колонку (например,
users.streak), create_all её не добавит. Для учебного проекта мы делаем
простую «автомиграцию»: сравниваем колонки в коде и в файле базы и
добавляем недостающие командой ALTER TABLE ... ADD COLUMN.
В большом проекте для этого используют Alembic.

Изменения схемы для университетской версии оформлены явными
пронумерованными миграциями (db/migrations.py); «автомиграция» ниже
осталась страховкой для старых баз.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from backend.app.db import models, models_campus, models_mod  # noqa: F401 — импорт «регистрирует» таблицы в Base
from backend.app.db.base import Base, get_engine

log = logging.getLogger(__name__)


def _add_missing_columns() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    with engine.begin() as conn:  # begin() — транзакция с автоматическим commit
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(engine.dialect)
                default = ""
                # Значение по умолчанию, чтобы старые строки получили осмысленное значение.
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    value = column.default.arg
                    default = f" DEFAULT {int(value) if isinstance(value, bool) else repr(value)}"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}{default}'))
                log.info("База: добавлена колонка %s.%s", table.name, column.name)


def init_db() -> None:
    from backend.app.db.migrations import run_migrations

    Base.metadata.create_all(get_engine())
    run_migrations()
    _add_missing_columns()
    # Соль для анонимных хешей создаём заранее, вне чужих транзакций (иначе SQLite «database is locked»).
    from backend.app.services.anon import ensure_salt

    ensure_salt()
    from backend.app.security.crypto import ensure_key

    ensure_key()


def check_db() -> bool:
    """Простая проверка «база отвечает» для /api/health."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — для health-проверки любая ошибка = «не в порядке»
        return False

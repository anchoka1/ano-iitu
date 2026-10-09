"""Подключение к базе данных.

Суть: SQLite — база данных в одном файле (data/verdikt.db), сервер базы
ставить не нужно. SQLAlchemy — библиотека, которая позволяет описывать
таблицы Python-классами и работать с записями как с объектами.

Термины:
  engine  — «двигатель»: знает, где база, и держит соединения с ней.
  session — «сеанс работы»: в нём читаем/меняем записи и делаем commit.
  Base    — общий предок всех классов-таблиц.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import get_settings


class Base(DeclarativeBase):
    """Предок всех таблиц. SQLAlchemy по нему находит, какие таблицы создать."""


@lru_cache
def get_engine() -> Engine:
    """Создаёт engine один раз (кэшируется, как и настройки)."""
    url = get_settings().resolved_database_url
    # check_same_thread=False: SQLite по умолчанию запрещает использовать
    # соединение из другого потока, а FastAPI выполняет обработчики в пуле
    # потоков. Сессии у нас короткие и не делятся между потоками — это безопасно.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def get_sessionmaker() -> sessionmaker[Session]:
    # expire_on_commit=False — после commit объекты остаются читаемыми.
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI: даёт сессию на время запроса и закрывает её.

    yield — «отдать значение и подождать»: FastAPI передаёт сессию в
    обработчик, а после ответа возвращается сюда и выполняет finally.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Сбрасывает кэш engine (для тестов, когда меняется путь к базе)."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()

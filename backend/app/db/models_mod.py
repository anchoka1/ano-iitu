"""Таблицы миграции 004: роли, журнал модерации, файлы, разобранные силлабусы.

Принципы:
  - роль модератора/админа хранится отдельно от профиля: её даёт только админ (или .env);
  - журнал модерации — кто, что и когда решил (без текста заявки: он остаётся в posts);
  - файлы лежат на диске зашифрованными под случайными именами, здесь — только служебные данные;
  - силлабус хранится разобранной карточкой (дедлайны, веса), оригинальный файл не сохраняется.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models import utcnow


class Staff(Base):
    """Модератор или админ, назначенный админом через приложение (дополнительно к спискам из .env)."""

    __tablename__ = "staff"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(10), default="moderator")   # moderator | admin
    name: Mapped[str] = mapped_column(String(128), default="")
    granted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModLog(Base):
    """Журнал модерации: кто, что и когда сделал."""

    __tablename__ = "mod_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    moderator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    moderator_name: Mapped[str] = mapped_column(String(128), default="")
    target: Mapped[str] = mapped_column(String(32), index=True)          # post:12 | app:3 | report:12 | staff:123
    kind: Mapped[str] = mapped_column(String(16), default="")            # radar, senior_q, rumor, mentor...
    action: Mapped[str] = mapped_column(String(16))                      # approve | reject | edit | hide | keep | verdict | grant | revoke
    reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class StoredFile(Base):
    """Загруженный файл. На диске — data/private/<storage_name>, содержимое зашифровано."""

    __tablename__ = "stored_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    purpose: Mapped[str] = mapped_column(String(16), index=True)         # post_photo
    ref: Mapped[str] = mapped_column(String(32), default="", index=True)  # к чему привязан: post:12
    storage_name: Mapped[str] = mapped_column(String(64), unique=True)    # случайное имя, без расширения
    mime: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class Syllabus(Base):
    """Разобранный силлабус: только структура (дедлайны, веса, правила). Оригинал файла не храним."""

    __tablename__ = "syllabi"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    hub_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    card_json: Mapped[str] = mapped_column(Text, default="{}")
    added_to_plan: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


MOD_TABLES = (Staff, ModLog, StoredFile, Syllabus)

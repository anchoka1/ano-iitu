"""Хранилище загруженных файлов: вне публичной папки, под случайными именами, зашифровано.

  - папка data/private/ — её не раздаёт веб-сервер (Mini App отдаётся только из miniapp/);
  - имя файла — случайные 32 символа, расширения нет: по имени нельзя угадать ни владельца, ни содержимое;
  - содержимое зашифровано ключом DATA_ENCRYPTION_KEY (security/crypto.py);
  - отдаём только через API после проверки прав (владелец, модератор или «объект виден этому человеку»);
  - срок хранения — FILE_RETENTION_DAYS, потом файл удаляется планировщиком;
  - «Удалить мои данные» удаляет и записи, и файлы.

Документы, которые нужны только для разбора (силлабус, чек, заявление), сюда не попадают вовсе:
текст извлекается в памяти, оригинал не сохраняется.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.db.base import get_sessionmaker
from backend.app.db.models_mod import StoredFile
from backend.app.security import crypto

log = logging.getLogger(__name__)
PRIVATE_DIR = PROJECT_ROOT / "data" / "private"
LEGACY_UPLOADS = PROJECT_ROOT / "data" / "uploads"


def _path(storage_name: str):
    return PRIVATE_DIR / storage_name


def save(owner_id: int, purpose: str, ref: str, raw: bytes, mime: str, days: int | None = None) -> int:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16)
    _path(name).write_bytes(crypto.encrypt(raw))
    keep = get_settings().file_retention_days if days is None else days
    with get_sessionmaker()() as session:
        row = StoredFile(owner_id=owner_id, purpose=purpose, ref=ref, storage_name=name, mime=mime, size=len(raw),
                         expires_at=datetime.now(timezone.utc) + timedelta(days=max(1, keep)))
        session.add(row)
        session.commit()
        log.info("Файл сохранён: #%s (%s, %d байт)", row.id, purpose, len(raw))
        return row.id


def find(ref: str, purpose: str) -> StoredFile | None:
    with get_sessionmaker()() as session:
        return session.scalar(select(StoredFile).where(StoredFile.ref == ref, StoredFile.purpose == purpose).order_by(StoredFile.id.desc()))


def read(row: StoredFile) -> bytes | None:
    path = _path(row.storage_name)
    if not path.is_file():
        return None
    return crypto.decrypt(path.read_bytes())


def _remove(session, row: StoredFile) -> None:
    _path(row.storage_name).unlink(missing_ok=True)
    session.delete(row)


def delete_ref(ref: str) -> int:
    with get_sessionmaker()() as session:
        rows = list(session.scalars(select(StoredFile).where(StoredFile.ref == ref)))
        for row in rows:
            _remove(session, row)
        session.commit()
        return len(rows)


def delete_user(session, user_id: int) -> None:
    for row in session.scalars(select(StoredFile).where(StoredFile.owner_id == user_id)):
        _remove(session, row)


def purge_expired(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    with get_sessionmaker()() as session:
        rows = [r for r in session.scalars(select(StoredFile).where(StoredFile.expires_at.is_not(None)))
                if (r.expires_at.replace(tzinfo=timezone.utc) if r.expires_at.tzinfo is None else r.expires_at) <= now]
        for row in rows:
            _remove(session, row)
        session.commit()
    if rows:
        log.info("Удалено файлов с истёкшим сроком хранения: %d", len(rows))
    return len(rows)


def migrate_legacy_uploads() -> int:
    """Старые фото потеряшек лежали открыто (data/uploads/<id>.jpg) — шифруем и переносим, оригинал удаляем."""
    if not LEGACY_UPLOADS.is_dir():
        return 0
    from backend.app.db.models_campus import Post

    moved = 0
    for path in LEGACY_UPLOADS.glob("*.jpg"):
        if not path.stem.isdigit():
            continue
        with get_sessionmaker()() as session:
            post = session.get(Post, int(path.stem))
            owner = post.author_id if post and post.author_id else 0
        if post is not None:
            save(owner, "post_photo", f"post:{post.id}", path.read_bytes(), "image/jpeg")
            moved += 1
        path.unlink(missing_ok=True)
    if moved:
        log.info("Старые фото объявлений зашифрованы и перенесены в data/private: %d", moved)
    return moved

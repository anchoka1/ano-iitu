"""Роли: студент, преподаватель, модератор, админ.

  - студент / преподаватель — из профиля (users.role); подтверждённый преподаватель — users.teacher_verified;
  - модератор и админ — из .env (MODERATOR_IDS, ADMIN_IDS; старое имя OWNER_IDS = админы)
    или назначены админом в приложении (таблица staff).

Права проверяет сервер в каждом обработчике (require_moderator / require_admin), а не интерфейс:
спрятанная кнопка — не защита.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models_mod import ModLog, Staff
from backend.app.i18n import t


class RoleError(Exception):
    pass


def _ids(raw: str) -> set[int]:
    return {int(x) for x in (raw or "").replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def env_admins() -> set[int]:
    s = get_settings()
    return _ids(s.admin_ids) | _ids(s.owner_ids)


def env_moderators() -> set[int]:
    return _ids(get_settings().moderator_ids)


def _staff_role(user_id: int) -> str:
    with get_sessionmaker()() as session:
        row = session.get(Staff, user_id)
        return row.role if row else ""


def is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    return user_id in env_admins() or _staff_role(user_id) == "admin"


def is_moderator(user_id: int | None) -> bool:
    """Модератор или админ."""
    if not user_id:
        return False
    return user_id in env_admins() or user_id in env_moderators() or _staff_role(user_id) in ("moderator", "admin")


def moderator_ids() -> set[int]:
    with get_sessionmaker()() as session:
        staff = {r.user_id for r in session.scalars(select(Staff))}
    return env_admins() | env_moderators() | staff


def role_of(user_id: int) -> str:
    if is_admin(user_id):
        return "admin"
    if is_moderator(user_id):
        return "moderator"
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        return user.role if user and user.role else "student"


def require_moderator(user_id: int) -> None:
    if not is_moderator(user_id):
        raise RoleError(t("mod.err.moderator_only"))


def require_admin(user_id: int) -> None:
    if not is_admin(user_id):
        raise RoleError(t("mod.err.admin_only"))


def list_staff() -> list[dict]:
    with get_sessionmaker()() as session:
        rows = {r.user_id: {"user_id": r.user_id, "role": r.role, "name": r.name, "source": "app"} for r in session.scalars(select(Staff))}
        for uid in env_moderators():
            rows.setdefault(uid, {"user_id": uid, "role": "moderator", "name": "", "source": "env"})
        for uid in env_admins():
            rows[uid] = {"user_id": uid, "role": "admin", "name": rows.get(uid, {}).get("name", ""), "source": "env"}
        for row in rows.values():
            if not row["name"] and (u := repo.get_user(session, row["user_id"])):
                row["name"] = u.first_name
        return sorted(rows.values(), key=lambda r: (r["role"] != "admin", r["name"]))


def grant(admin_id: int, admin_name: str, user_id: int, role: str = "moderator") -> dict:
    """Админ назначает модератора (или админа). Человек должен хотя бы раз открыть бота."""
    require_admin(admin_id)
    if role not in ("moderator", "admin"):
        raise RoleError(t("mod.err.role"))
    with get_sessionmaker()() as session:
        user = repo.get_user(session, user_id)
        if user is None:
            raise RoleError(t("mod.err.unknown_user"))
        row = session.get(Staff, user_id) or Staff(user_id=user_id)
        row.role, row.name, row.granted_by = role, user.first_name or "", admin_id
        session.add(row)
        session.add(ModLog(moderator_id=admin_id, moderator_name=admin_name[:128], target=f"staff:{user_id}", kind=role, action="grant"))
        session.commit()
    return {"user_id": user_id, "role": role}


def revoke(admin_id: int, admin_name: str, user_id: int) -> None:
    require_admin(admin_id)
    if user_id in env_admins() or user_id in env_moderators():
        raise RoleError(t("mod.err.env_role"))
    with get_sessionmaker()() as session:
        row = session.get(Staff, user_id)
        if row is not None:
            session.delete(row)
            session.add(ModLog(moderator_id=admin_id, moderator_name=admin_name[:128], target=f"staff:{user_id}", kind=row.role, action="revoke"))
            session.commit()


def delete_user(session, user_id: int) -> None:
    row = session.get(Staff, user_id)
    if row is not None:
        session.delete(row)

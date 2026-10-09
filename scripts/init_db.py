"""Подготовка базы данных: создаёт файл SQLite и таблицы.

Запускается скриптами run.* перед стартом сервера. Можно и вручную:
    .venv\\Scripts\\python scripts\\init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Скрипт лежит в scripts/, а пакет backend — в корне проекта. Добавляем
# корень в sys.path (список папок, где Python ищет модули), чтобы
# работал импорт "from backend.app ...".
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.config import get_settings  # noqa: E402 — импорт после правки sys.path
from backend.app.db.init_db import init_db  # noqa: E402


def main() -> int:
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[ANO IITU] Не удалось создать базу: {exc}")
        print("[ANO IITU] Проверьте DATABASE_URL в .env и права на папку data/.")
        return 1
    print(f"[ANO IITU] База готова: {get_settings().resolved_database_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

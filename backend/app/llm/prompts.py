"""Загрузка промптов из файлов.

Промпты лежат в backend/app/llm/prompts/*.md. Первая строка файла —
версия, например:  <!-- version: 1 -->
Версию пишем в лог при каждом запросе: если поменяли промпт и качество
упало, видно, какая версия виновата.

Промпт = общая часть (base.md: правила честности и безопасности)
       + часть режима (pravda.md, razvod.md ...).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION_RE = re.compile(r"<!--\s*version:\s*([\w.]+)\s*-->")


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Возвращает (текст, версия) промпта по имени файла без .md."""
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    version = match.group(1) if match else "0"
    text = _VERSION_RE.sub("", text).strip()
    return text, f"{name}@{version}"


def system_prompt(mode_prompt: str, with_base: bool = True) -> tuple[str, str]:
    """Собирает системный промпт: общие правила + промпт режима."""
    text, version = load_prompt(mode_prompt)
    if not with_base:
        return text, version
    base, base_version = load_prompt("base")
    return f"{base}\n\n{text}", f"{base_version}+{version}"

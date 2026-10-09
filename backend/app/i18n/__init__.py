"""Локализация: все тексты интерфейса лежат в locales/<язык>.json.

Суть: в коде пишем t("bot.start.greeting", name="Аня"), а сам текст берётся
из файла. Чтобы добавить казахский, достаточно создать locales/kk.json с
теми же ключами — код менять не нужно. Если ключа нет в нужном языке,
берётся русский вариант (язык по умолчанию).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LANG = "ru"


@lru_cache
def load_strings(lang: str) -> dict[str, str]:
    """Читает словарь строк для языка. Нет файла — пустой словарь."""
    path = LOCALES_DIR / f"{lang}.json"
    if not path.is_file():
        return {}
    # encoding="utf-8" обязателен: на Windows по умолчанию другая кодировка.
    return json.loads(path.read_text(encoding="utf-8"))


def available_languages() -> list[str]:
    return sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


def strings_for(lang: str) -> dict[str, str]:
    """Все строки языка, дополненные русскими там, где перевода нет."""
    merged = dict(load_strings(DEFAULT_LANG))
    merged.update(load_strings(lang))
    return merged


def t(key: str, lang: str = DEFAULT_LANG, **params: object) -> str:
    """Возвращает текст по ключу и подставляет параметры {name} и т. п.

    Если ключа нет совсем, возвращаем сам ключ — так ошибку сразу видно
    на экране, а программа не падает.
    """
    template = load_strings(lang).get(key) or load_strings(DEFAULT_LANG).get(key) or key
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template

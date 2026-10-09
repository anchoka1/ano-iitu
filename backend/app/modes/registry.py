"""Справочник режимов.

Главная идея: режимы отличаются не кодом, а «настройками» в этой таблице —
каким промптом спрашивать модель, искать ли источники, нужны ли признаки
мошенничества, обязателен ли источник для уверенного вывода.
Движок (core/engine.py) один для всех.

Важно про команды: Telegram разрешает в командах только латиницу, цифры
и «_». Команду «/правда» Telegram не распознает как команду (её нельзя
добавить в меню, а в группах бот её даже не получит из-за режима
приватности). Поэтому команды — транслитом (/pravda), а в меню и в
интерфейсе показываем русские названия.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.i18n import t


@dataclass(frozen=True)
class Mode:
    key: str             # внутреннее имя режима
    command: str         # команда бота (латиницей)
    emoji: str
    prompt: str = ""     # имя файла промпта в llm/prompts (без .md)
    use_rag: bool = False         # искать ли в базе источников
    use_signals: bool = False     # искать ли признаки мошенничества и ссылки
    source_required: bool = False  # без источника — только ⚪ «недостаточно данных»
    accepts_files: bool = False   # можно ли прислать фото/PDF
    is_check: bool = True         # выдаёт карточку вердикта (у /dogovorilis — свой формат)

    def title(self, lang: str = "ru") -> str:
        return t(f"mode.{self.key}.title", lang)

    def description(self, lang: str = "ru") -> str:
        return t(f"mode.{self.key}.desc", lang)


# Кортеж (tuple) — неизменяемый список: порядок здесь = порядок на экране и в меню.
MODES: tuple[Mode, ...] = (
    Mode("pravda", "pravda", "🔎", "pravda", use_rag=True, use_signals=True, source_required=True),
    Mode("razvod", "razvod", "🚨", "razvod", use_rag=True, use_signals=True, accepts_files=True),
    Mode("spor", "spor", "🗣", "spor", use_rag=True),
    Mode("dogovor", "dogovorilis", "✍️", "dogovorilis", is_check=False),
    Mode("chek", "chek", "🧾", "chek", use_rag=True, use_signals=True, accepts_files=True),
    Mode("prava", "prava", "⚖️", "prava", use_rag=True),
    # --- университет (МУИТ) ---
    # «Спроси МУИТ»: ответ ТОЛЬКО по базе знаний из официальных источников; нет источника — «не знаю» + куда идти.
    Mode("vopros", "sprosi", "📖", "vopros", use_rag=True, source_required=True),
    # «Объявление»: для преподавателей и старост — однозначно ли сообщение группе (что, когда, где, кому).
    Mode("obyavlenie", "obyavlenie", "📣", "obyavlenie"),
)


def get_mode(key: str) -> Mode | None:
    return next((m for m in MODES if m.key == key), None)


def mode_by_command(command: str) -> Mode | None:
    return next((m for m in MODES if m.command == command), None)

"""Хранилище источников и поиск по ним.

Формат файла источника (data/sources/имя.md):

    ---
    title: Название
    url: https://...
    date: 2026-01-15          # дата актуальности
    publisher: Кто опубликовал
    is_demo: true             # примерный источник для демо
    modes: pravda, razvod     # для каких режимов подходит
    verdict: red              # (необязательно) для разоблачений: red/green/yellow
    claim: Какое утверждение разбирается
    keywords: слово, слово
    do: Что делать | Ещё шаг
    dont: Чего не делать | Ещё
    checked: 2026-10-07       # дата, когда текст сверили с официальной страницей
    office: registrar         # куда идти по теме (id из data/iitu/services.json)
    ---
    Текст источника...

Поиск простой и понятный — без нейросетей и внешних библиотек:
  1. Слова приводим к «основе» (обрезаем окончания): «заблокируют» -> «заблок».
  2. Считаем, сколько основ запроса встречается в источнике, с весом
     IDF (редкие слова важнее частых — «пенсия» важнее «это»).
  3. Ключевые слова (keywords) и утверждение (claim) весят больше.
Для учебной базы из десятков файлов этого достаточно; для тысяч
документов позже можно подключить векторный поиск.

Кроме файлов, в базу автоматически попадают:
  - академический календарь МУИТ (data/iitu/calendar_*.json) — одним источником;
  - свежие новости МУИТ из кэша ленты (university/news.py) — см. search_all().
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

SOURCES_DIR = PROJECT_ROOT / "data" / "sources"

# Частые слова, которые ничего не говорят о смысле («стоп-слова»).
_STOP = set(
    "и в во на не что это как так по к ко с со у о об от до из за для же ли или но а бы был была были быть "
    "вы мы он она они его ее её их мне меня вас нам все всё всех ещё уже только очень есть нет да при про "
    "этот эта эти того тот кто где когда чтобы если то также будет будут ваш ваша ваши наш".split()
)
_WORD_RE = re.compile(r"[a-zа-яёәғқңөұүһі0-9]+", re.IGNORECASE)


def stem(word: str) -> str:
    """Грубое выделение основы: оставляем первые 6 букв длинного слова."""
    word = word.lower().replace("ё", "е")
    return word[:6] if len(word) > 6 else word


def tokenize(text: str) -> list[str]:
    # Двухбуквенные латинские сокращения (FX, IT, AI, UX) — значимые слова в вузе, их не отбрасываем.
    return [stem(w) for w in _WORD_RE.findall(text)
            if (len(w) > 2 or (len(w) == 2 and w.isascii() and w.isalpha())) and w.lower() not in _STOP]


@dataclass
class Source:
    id: str
    title: str
    url: str
    date: str
    publisher: str = ""
    is_demo: bool = False
    modes: list[str] = field(default_factory=list)
    verdict: str = ""
    claim: str = ""
    keywords: list[str] = field(default_factory=list)
    do: list[str] = field(default_factory=list)
    dont: list[str] = field(default_factory=list)
    checked: str = ""   # дата проверки по официальной странице
    office: str = ""    # подразделение «куда идти» (id сервиса)
    text: str = ""
    tokens: list[str] = field(default_factory=list, repr=False)
    boost_tokens: set[str] = field(default_factory=set, repr=False)

    def public(self) -> dict:
        """Данные для API (без служебных полей)."""
        return {
            "id": self.id, "title": self.title, "url": self.url, "date": self.date,
            "publisher": self.publisher, "is_demo": self.is_demo, "modes": self.modes,
            "checked": self.checked or self.date, "office": self.office,
        }


@dataclass
class Retrieved:
    source: Source
    score: float
    snippet: str


def _split_list(value: str, sep: str = ",") -> list[str]:
    return [v.strip() for v in value.split(sep) if v.strip()]


def parse_source(path: Path) -> Source:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        # Разделяем «шапку» (между двумя ---) и текст.
        _, header, body = raw.split("---", 2)
        for line in header.strip().splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                key, value = line.split(":", 1)
                meta[key.strip()] = value.split("  #")[0].strip()
    source = Source(
        id=path.stem,
        title=meta.get("title", path.stem),
        url=meta.get("url", ""),
        date=meta.get("date", ""),
        publisher=meta.get("publisher", ""),
        is_demo=meta.get("is_demo", "false").lower() == "true",
        modes=_split_list(meta.get("modes", "")),
        verdict=meta.get("verdict", ""),
        claim=meta.get("claim", ""),
        keywords=_split_list(meta.get("keywords", "")),
        do=_split_list(meta.get("do", ""), "|"),
        dont=_split_list(meta.get("dont", ""), "|"),
        checked=meta.get("checked", ""),
        office=meta.get("office", ""),
        text=body.strip(),
    )
    return index_source(source)


def index_source(source: Source) -> Source:
    """Готовит слова источника для поиска (общая часть для файлов и «виртуальных» источников)."""
    source.tokens = tokenize(" ".join([source.title, source.claim, " ".join(source.keywords), source.text]))
    source.boost_tokens = set(tokenize(" ".join([source.claim, " ".join(source.keywords)])))
    return source


def calendar_source() -> Source | None:
    """Академический календарь МУИТ как источник базы знаний (даты — из data/iitu, не из кода)."""
    from datetime import date

    from backend.app.university import calendar

    cal = calendar.calendar_for(date.today()) or (calendar.calendars()[-1] if calendar.calendars() else None)
    if cal is None:
        return None
    text = calendar.as_source_text(cal.year_start)
    return index_source(Source(
        id="iitu_calendar", title=f"МУИТ: академический календарь {cal.academic_year} (бакалавриат)", url=cal.page_url,
        date=cal.checked, checked=cal.checked, publisher="МУИТ (iitu.edu.kz), академический календарь", office="registrar",
        modes=["vopros", "pravda", "prava", "spor", "dogovor"],
        keywords=["календарь", "когда", "сессия", "рубежный контроль", "рубежка", "экзамены", "каникулы", "пересдача", "fx",
                  "регистрация", "иуп", "практика", "диплом", "летний семестр", "выходной", "праздник", "пары", "занятия"],
        do=["Сверяй даты на странице академического календаря", "Вопросы по срокам — в Офис-регистратор"],
        dont=["Не верь «переносам сессии» из чатов без подтверждения на сайте или от Офиса регистратора"],
        text=text,
    ))


class SourceStore:
    def __init__(self, directory: Path | None = SOURCES_DIR, extra: list[Source] | None = None) -> None:
        self.sources: list[Source] = []
        if directory is not None and directory.is_dir():
            self.sources = [parse_source(p) for p in sorted(directory.glob("*.md")) if p.name.lower() != "readme.md"]
        self.sources += extra or []
        # IDF: log(N / (1 + сколько документов содержат слово)) — редкое слово весит больше.
        n = len(self.sources) or 1
        doc_freq: dict[str, int] = {}
        for s in self.sources:
            for token in set(s.tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        self.idf = {t: math.log(1 + n / (1 + df)) for t, df in doc_freq.items()}

    def get(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def search(self, query: str, mode: str | None = None, limit: int = 3, min_score: float = 2.5) -> list[Retrieved]:
        """Ищет источники для запроса. min_score — порог: ниже него считаем, что ничего не нашли."""
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        results: list[Retrieved] = []
        for source in self.sources:
            if mode and source.modes and mode not in source.modes:
                continue
            doc_tokens = set(source.tokens)
            score = 0.0
            for token in query_tokens & doc_tokens:
                score += self.idf.get(token, 0.0) * (2.0 if token in source.boost_tokens else 1.0)
            if score >= min_score:
                results.append(Retrieved(source, round(score, 2), _snippet(source.text, query_tokens)))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]


def _snippet(text: str, query_tokens: set[str], size: int = 700) -> str:
    """Кусок текста источника вокруг первого совпадения — чтобы не отправлять модели весь файл."""
    lowered = text.lower()
    for token in query_tokens:
        pos = lowered.find(token)
        if pos >= 0:
            start = max(0, pos - size // 3)
            return text[start:start + size].strip()
    return text[:size].strip()


@lru_cache
def get_store() -> SourceStore:
    extra = [s for s in [calendar_source()] if s]
    return SourceStore(extra=extra)


def search_all(query: str, mode: str | None = None, limit: int = 3) -> list[Retrieved]:
    """Поиск по базе источников и по свежим новостям МУИТ (для «Правда?» и «Спроси МУИТ»)."""
    results = get_store().search(query, mode, limit=limit)
    if mode in ("pravda", "vopros"):
        from backend.app.university.news import get_news_store

        results += get_news_store().search(query, mode, limit=2, min_score=3.0)
    if mode in ("pravda", "razvod", "chek"):
        # Одобренные модератором записи Радара и проверенные слухи — «своя база» сообщества.
        from backend.app.rag import community

        results += community.search(query, mode)
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]

"""Загрузка официального текста в базу источников (data/sources/).

Суть: бот ссылается только на тексты из data/sources/. Этот скрипт
скачивает страницу с ОФИЦИАЛЬНОГО сайта (законы, госорганы), убирает
HTML-оформление и сохраняет как источник с сегодняшней датой актуальности.

    .venv\\Scripts\\python scripts\\fetch_source.py URL [--title "Название"] [--modes prava,chek] [--keywords "слово, слово"]

Сайт adilet.zan.kz подгружает текст законов скриптом, поэтому для него:
  1) откройте закон в браузере, выделите и скопируйте текст в файл law.txt (UTF-8);
  2) .venv\\Scripts\\python scripts\\fetch_source.py https://adilet.zan.kz/rus/docs/НОМЕР --from-file law.txt --title "Закон РК «...»" --modes prava,chek --keywords "возврат товара, потребитель"
Для обычных страниц (egov.kz, gov.kz) хватает ссылки — скрипт скачает текст сам.

Важно:
  - разрешены только официальные домены (список ниже) — чтобы в базу не
    попали тексты с сомнительных сайтов;
  - большие законы обрезаются до ~40 000 символов (ИИ всё равно получает
    только найденный фрагмент); при необходимости разбейте закон на части;
  - после загрузки ОТКРОЙТЕ файл и проверьте текст глазами.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "sources"
# iitu.edu.kz — официальный сайт МУИТ (база знаний «Спроси МУИТ»)
ALLOWED = ("adilet.zan.kz", "egov.kz", "gov.kz", "nationalbank.kz", "stat.gov.kz", "enpf.kz", "kgd.gov.kz", "afmrk.gov.kz", "online.zakon.kz", "iitu.edu.kz")
MAX_CHARS = 40_000


class TextExtractor(HTMLParser):
    """Собирает видимый текст страницы, пропуская скрипты, стили и меню."""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузить официальный источник")
    parser.add_argument("url")
    parser.add_argument("--title", default="")
    parser.add_argument("--modes", default="pravda,prava,chek")
    parser.add_argument("--keywords", default="")
    parser.add_argument("--from-file", help="взять текст из сохранённого .txt (для сайтов, где текст подгружается скриптом, например adilet.zan.kz)")
    args = parser.parse_args()

    host = (urlsplit(args.url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in ALLOWED):
        print(f"[ANO IITU] Домен {host} не в списке официальных: {', '.join(ALLOWED)}")
        return 1

    if args.from_file:
        # Текст скопирован из браузера вручную; ссылка нужна как адрес первоисточника.
        file = Path(args.from_file)
        if not file.is_file():
            print(f"[ANO IITU] Файл не найден: {file}")
            return 1
        text = file.read_text(encoding="utf-8")[:MAX_CHARS].strip()
        page_title = ""
    else:
        print(f"[ANO IITU] Загружаю {args.url} …")
        try:
            response = httpx.get(args.url, timeout=30, follow_redirects=True, headers={"User-Agent": "Verdikt-source-fetcher/1.0"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"[ANO IITU] Не удалось загрузить: {exc}")
            return 1
        extractor = TextExtractor()
        extractor.feed(response.text)
        text = extractor.text()[:MAX_CHARS]
        page_title = " ".join(extractor.title.split())
    if len(text) < 200:
        print("[ANO IITU] Текста почти нет: сайт подгружает его скриптом (так устроен adilet.zan.kz).")
        print("           Откройте страницу в браузере, скопируйте текст закона в файл law.txt (UTF-8) и запустите:")
        print(f"           .venv\\Scripts\\python scripts\\fetch_source.py {args.url} --from-file law.txt --title \"Название закона\"")
        return 1
    title = args.title or page_title or host
    slug = re.sub(r"[^a-z0-9]+", "_", (urlsplit(args.url).path or host).lower()).strip("_")[:50] or "source"
    path = SOURCES / f"official_{slug}.md"
    header = (
        "---\n"
        f"title: {title}\n"
        f"url: {args.url}\n"
        f"date: {date.today().isoformat()}\n"
        f"publisher: {host}\n"
        "is_demo: false\n"
        f"modes: {args.modes}\n"
        f"keywords: {args.keywords}\n"
        "---\n"
    )
    path.write_text(header + text + "\n", encoding="utf-8")
    print(f"[ANO IITU] Сохранено: {path} ({len(text)} символов). Проверьте текст и перезапустите сервер.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

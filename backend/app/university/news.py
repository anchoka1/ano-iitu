"""Лента новостей МУИТ: официальный сайт и официальный Telegram-канал.

Источники (других без решения владельца проекта не добавляем):
  - https://iitu.edu.kz/ru/news/            — список новостей (HTML);
  - https://t.me/s/iitu_channel             — публичная веб-версия канала.

Как работает:
  1. Планировщик раз в NEWS_REFRESH_MINUTES вызывает refresh().
  2. Новые новости сохраняются в таблицу news_items (кэш). Храним заголовок,
     дату, ссылку и короткий пересказ; полный текст — только по ссылке.
  3. Пересказ «своими словами» делает ИИ (1–2 предложения, только по тексту
     новости). Без ИИ или при ошибке — короткая выдержка из первой фразы
     с пометкой; при следующем обновлении бот попробует пересказать снова.
  4. Если источник недоступен — показываем последнее сохранённое с пометкой
     «обновлено тогда-то», а не пустой экран и не выдуманные новости.
  5. Подписчикам (по умолчанию подписка ВЫКЛЮЧЕНА) приходят новые новости,
     не больше NEWS_NOTIFY_MAX за одно обновление.
  6. Новости — один из источников для режима «Правда?»: если слух из чата
     подтверждается или опровергается новостью, вердикт ссылается на неё.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.db.models import NewsItem, User, iso_utc, utcnow
from backend.app.rag.store import Source, SourceStore, index_source

log = logging.getLogger("verdikt.news")

USER_AGENT = "Verdikt-bot/1.0 (+student project; news digest with links to iitu.edu.kz)"
MONTHS = {m: i for i, m in enumerate(
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"], 1)}
DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
SOURCE_LABEL = {"site": "iitu.edu.kz", "telegram": "Telegram @iitu_channel"}


class NewsError(Exception):
    pass


# ------------------------------------------------------------------ разбор HTML


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    return html.unescape(re.sub(r"[ \t\xa0]+", " ", TAG_RE.sub(" ", fragment))).strip()


def _parse_ru_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    try:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    except ValueError:
        return ""


def parse_site_list(page: str, base: str = "https://iitu.edu.kz") -> list[dict]:
    """Карточки из списка новостей сайта: ссылка, заголовок, дата, картинка."""
    items: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a\s+href="((?:https://iitu\.edu\.kz)?/ru/news/[^"?#]+/)"[^>]*>(.*?)</a>', page, re.S | re.IGNORECASE):
        url, inner = m.group(1), m.group(2)
        if url.startswith("/"):
            url = base + url
        if url in seen or url.rstrip("/").endswith("/ru/news"):
            continue
        published = _parse_ru_date(_text(inner))
        img = re.search(r'<img[^>]+src="([^"]+)"', inner)
        img_alt = re.search(r'<img[^>]+alt="([^"]*)"', inner)
        blocks = [b for b in (_text(x) for x in re.findall(r"<div[^>]*>(.*?)</div>", inner, re.S)) if b and not DATE_RE.fullmatch(b.strip().rstrip(" г.").strip() + "")]
        title = ""
        for block in reversed(blocks):
            if not DATE_RE.search(block) and len(block) > 5:
                title = block
                break
        title = title or (html.unescape(img_alt.group(1)) if img_alt else "")
        if not title:
            continue
        seen.add(url)
        image = img.group(1) if img else ""
        if image.startswith("/"):
            image = base + image
        items.append({"source": "site", "ext_id": url, "url": url, "title": " ".join(title.split())[:500],
                      "published": published, "image": image})
    return items


def parse_article_lead(page: str, max_chars: int = 900) -> str:
    """Первые абзацы статьи — только для пересказа и поиска (полный текст не храним)."""
    start = page.find("<article")
    body = page[start:] if start >= 0 else page
    paragraphs = [_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
    lead = ""
    for p in paragraphs:
        if len(p) < 30:
            continue
        lead = f"{lead} {p}".strip()
        if len(lead) >= max_chars:
            break
    return lead[:max_chars]


def parse_telegram(page: str, channel: str) -> list[dict]:
    """Посты публичной веб-версии канала t.me/s/<канал>."""
    items: list[dict] = []
    chunks = page.split('class="tgme_widget_message_wrap')
    zone = ZoneInfo(get_settings().timezone)
    for chunk in chunks[1:]:
        post = re.search(r'data-post="([^"]+/(\d+))"', chunk)
        if not post:
            continue
        text_m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        text = _text(text_m.group(1)) if text_m else ""
        if len(text) < 15:
            continue  # пост без текста (только фото/видео) — пропускаем, пересказывать нечего
        when = re.search(r'<time[^>]+datetime="([^"]+)"', chunk)
        published = ""
        if when:
            try:
                published = datetime.fromisoformat(when.group(1)).astimezone(zone).date().isoformat()
            except ValueError:
                published = ""
        first_line = next((ln.strip() for ln in text.split("\n") if len(ln.strip()) > 3), text)
        title = first_line if len(first_line) <= 140 else first_line[:137].rsplit(" ", 1)[0] + "…"
        number = post.group(2)
        items.append({"source": "telegram", "ext_id": number, "url": f"https://t.me/{channel}/{number}",
                      "title": title, "published": published, "image": "", "lead": " ".join(text.split())[:900]})
    return items


def excerpt(lead: str, title: str = "", limit: int = 200) -> str:
    """Короткая выдержка (если пересказ ИИ недоступен): первая фраза, не длиннее limit."""
    text = (lead or "").strip()
    if title and text.startswith(title):
        text = text[len(title):].strip(" .:—-\n")
    sentence = re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)[0] if text else ""
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rsplit(" ", 1)[0] + "…"
    return sentence


# ------------------------------------------------------------------ загрузка


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"}, follow_redirects=True)
    response.raise_for_status()
    return response.text


async def fetch_sources(client: httpx.AsyncClient | None = None, known_urls: set[str] | None = None, max_articles: int = 8) -> tuple[list[dict], list[str]]:
    """Скачивает оба источника. Возвращает (новости, ошибки). Ошибка одного источника не мешает другому."""
    settings = get_settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    items: list[dict] = []
    errors: list[str] = []
    known_urls = known_urls or set()
    try:
        try:
            site = parse_site_list(await _get(client, settings.news_site_url))
            fetched = 0
            for item in site:
                if item["url"] in known_urls or fetched >= max_articles:
                    continue
                try:
                    item["lead"] = parse_article_lead(await _get(client, item["url"]))
                except httpx.HTTPError:
                    item["lead"] = ""
                fetched += 1
            items += site
            if not site:
                errors.append("сайт: не нашёл новостей на странице (изменилась вёрстка?)")
        except httpx.HTTPError as exc:
            errors.append(f"сайт недоступен ({type(exc).__name__})")
        channel = settings.news_telegram_channel.strip().lstrip("@")
        if channel:
            try:
                items += parse_telegram(await _get(client, f"https://t.me/s/{channel}"), channel)
            except httpx.HTTPError as exc:
                errors.append(f"Telegram-канал недоступен ({type(exc).__name__})")
    finally:
        if own:
            await client.aclose()
    return items, errors


async def summarize(title: str, lead: str) -> tuple[str, str]:
    """Пересказ своими словами (ИИ) или выдержка. Возвращает (текст, способ)."""
    from backend.app.core.engine import EngineError, get_verdict_engine
    from backend.app.llm.factory import get_llm_client

    if not lead.strip():
        return "", "excerpt"
    if get_llm_client().name == "mock":
        return excerpt(lead, title), "excerpt"  # без ИИ честно показываем выдержку, а не «пересказ»
    try:
        text = await get_verdict_engine().summarize_news(title, lead)
        if text.strip():
            return text.strip()[:400], "llm"
    except EngineError as exc:
        log.info("Пересказ новости не удался, оставляю выдержку: %s", exc)
    return excerpt(lead, title), "excerpt"


# ------------------------------------------------------------------ кэш в базе


def _save_items(items: list[dict]) -> list[int]:
    """Сохраняет новые новости, обновляет известные. Возвращает id новых."""
    new_ids: list[int] = []
    with get_sessionmaker()() as session:
        first_run = session.scalar(select(NewsItem.id).limit(1)) is None
        for item in items:
            row = session.scalar(select(NewsItem).where(NewsItem.source == item["source"], NewsItem.ext_id == item["ext_id"][:256]))
            if row is None:
                row = NewsItem(source=item["source"], ext_id=item["ext_id"][:256], url=item["url"][:1024],
                               notified=first_run)  # при самой первой загрузке не рассылаем всю ленту
                session.add(row)
                session.flush()
                new_ids.append(row.id)
            row.title = item["title"][:512]
            row.published = item.get("published") or row.published
            row.image = item.get("image", "")[:1024] or row.image
            if item.get("lead") and not row.lead:
                row.lead = item["lead"]
            row.fetched_at = utcnow()
        session.commit()
    return new_ids


async def _fill_summaries(limit: int) -> int:
    """Пересказывает новости без пересказа ИИ (не больше limit за раз — экономим лимиты)."""
    with get_sessionmaker()() as session:
        rows = list(session.scalars(
            select(NewsItem).where(NewsItem.summary_by != "llm", NewsItem.lead != "").order_by(NewsItem.published.desc()).limit(30)))
    done = 0
    for row in rows:
        use_llm = done < limit
        if use_llm:
            text, how = await summarize(row.title, row.lead)
            done += 1
        elif row.summary:
            continue
        else:
            text, how = excerpt(row.lead, row.title), "excerpt"
        with get_sessionmaker()() as session:
            item = session.get(NewsItem, row.id)
            if item and text:
                item.summary, item.summary_by = text, how
                session.commit()
    return done


async def refresh(notifier=None, client: httpx.AsyncClient | None = None) -> dict:
    """Одно обновление ленты. Никогда не бросает исключение наружу — пишет ошибку в meta."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        known = {u for u in session.scalars(select(NewsItem.url))}
        repo.set_meta(session, "news_attempt_at", utcnow().isoformat())
    try:
        items, errors = await fetch_sources(client, known)
    except Exception as exc:  # noqa: BLE001 — сбой источника не должен ронять планировщик
        log.warning("Новости: ошибка загрузки: %s", exc)
        items, errors = [], [f"ошибка загрузки ({type(exc).__name__})"]
    new_ids = _save_items(items) if items else []
    await _fill_summaries(settings.news_summary_per_run)
    with get_sessionmaker()() as session:
        if items:
            repo.set_meta(session, "news_fetched_at", utcnow().isoformat())
        repo.set_meta(session, "news_error", "; ".join(errors)[:250])
    get_news_store.cache_clear()
    sent = await notify_subscribers(notifier) if notifier is not None else 0
    if items or errors:
        log.info("Новости: получено %d, новых %d, отправлено %d%s", len(items), len(new_ids), sent,
                 f", ошибки: {'; '.join(errors)}" if errors else "")
    return {"fetched": len(items), "new": len(new_ids), "sent": sent, "errors": errors}


def is_due(now: datetime) -> bool:
    settings = get_settings()
    if not settings.news_enabled:
        return False
    with get_sessionmaker()() as session:
        last = repo.get_meta(session, "news_attempt_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now.astimezone(timezone.utc) - last_dt >= timedelta(minutes=max(5, settings.news_refresh_minutes))


async def run_news_loop() -> None:
    """Фоновый цикл: раз в минуту проверяет, пора ли обновить ленту. Запускается сервером (main.py)."""
    import asyncio

    from backend.app.core.notify import get_notifier

    log.info("Лента новостей МУИТ: обновление каждые %d мин.", get_settings().news_refresh_minutes)
    while True:
        try:
            if is_due(datetime.now(timezone.utc)):
                await refresh(get_notifier())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обновления новостей")
        await asyncio.sleep(60)


# ------------------------------------------------------------------ выдача


def _item_dict(row: NewsItem) -> dict:
    return {
        "id": row.id, "source": row.source, "source_label": SOURCE_LABEL.get(row.source, row.source),
        "url": row.url, "title": row.title, "published": row.published, "summary": row.summary or "",
        "summary_by": row.summary_by, "image": row.image,
    }


def list_news(limit: int = 20, source: str = "") -> dict:
    """Лента для бота и Mini App: новости + когда обновлено + была ли ошибка."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        query = select(NewsItem).order_by(NewsItem.published.desc(), NewsItem.id.desc())
        if source:
            query = query.where(NewsItem.source == source)
        rows = list(session.scalars(query.limit(limit)))
        fetched_at = repo.get_meta(session, "news_fetched_at")
        error = repo.get_meta(session, "news_error")
    stale = bool(error) or not fetched_at
    if fetched_at:
        try:
            age = utcnow() - datetime.fromisoformat(fetched_at)
            stale = stale or age > timedelta(minutes=3 * max(5, settings.news_refresh_minutes))
        except ValueError:
            pass
    return {
        "items": [_item_dict(r) for r in rows], "updated_at": fetched_at, "stale": stale, "error": error,
        "sources": [{"label": "iitu.edu.kz/ru/news", "url": settings.news_site_url},
                    {"label": f"t.me/{settings.news_telegram_channel}", "url": f"https://t.me/{settings.news_telegram_channel}"}],
    }


def _updated_label(fetched_at: str) -> str:
    if not fetched_at:
        return "ещё не обновлялась"
    try:
        dt = datetime.fromisoformat(fetched_at).astimezone(ZoneInfo(get_settings().timezone))
    except ValueError:
        return fetched_at
    return dt.strftime("%d.%m.%Y %H:%M")


def format_date(value: str) -> str:
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return value
    return d.strftime("%d.%m.%Y")


def render_news_html(limit: int = 6) -> str:
    data = list_news(limit)
    e = html.escape
    lines = ["📰 <b>НОВОСТИ МУИТ</b>", ""]
    if not data["items"]:
        lines.append("Пока не удалось загрузить новости. Свежие новости — на iitu.edu.kz/ru/news и в канале @iitu_channel.")
        return "\n".join(lines)
    for n, item in enumerate(data["items"], 1):
        lines.append(f"<b>/{n:02d} · {e(format_date(item['published']))}</b> · <i>{e(item['source_label'])}</i>")
        lines.append(f"<b>{e(item['title'])}</b>")
        if item["summary"]:
            mark = "" if item["summary_by"] == "llm" else " <i>(выдержка)</i>"
            lines.append(f"{e(item['summary'])}{mark}")
        lines.append(f'<a href="{e(item["url"])}">Читать полностью →</a>')
        lines.append("")
    note = f"Обновлено: {_updated_label(data['updated_at'])}"
    if data["stale"]:
        note = f"⚠️ Источник сейчас недоступен — показываю сохранённое. {note}"
    lines.append(f"<i>{e(note)}</i>")
    return "\n".join(lines)


async def notify_subscribers(notifier) -> int:
    settings = get_settings()
    with get_sessionmaker()() as session:
        rows = list(session.scalars(select(NewsItem).where(NewsItem.notified.is_(False)).order_by(NewsItem.published).limit(settings.news_notify_max)))
        users = [u.telegram_id for u in session.scalars(select(User).where(User.news_subscribed.is_(True), User.bot_started.is_(True)))]
        for row in session.scalars(select(NewsItem).where(NewsItem.notified.is_(False))):
            row.notified = True  # остаток помечаем, чтобы не разослать пачкой позже
        session.commit()
    if not rows or not users:
        return 0
    sent = 0
    e = html.escape
    for row in rows:
        text = f"📰 <b>{e(row.title)}</b>\n{e(row.summary or '')}\n<a href=\"{e(row.url)}\">Читать на {e(SOURCE_LABEL.get(row.source, ''))}</a>\n\n<i>Отключить: /settings</i>"
        for user_id in users:
            sent += bool(await notifier.send(user_id, text))
    return sent


# ------------------------------------------------------------------ новости как источник для проверки слухов


@lru_cache
def get_news_store() -> SourceStore:
    with get_sessionmaker()() as session:
        rows = list(session.scalars(select(NewsItem).order_by(NewsItem.published.desc()).limit(60)))
    sources = [index_source(Source(
        id=f"news_{r.id}", title=f"Новость МУИТ: {r.title}"[:300], url=r.url, date=r.published, checked=r.published,
        publisher=f"МУИТ, {SOURCE_LABEL.get(r.source, r.source)}", modes=["pravda", "vopros"], office="reception",
        text=f"{r.title}. {r.lead or r.summary}",
    )) for r in rows]
    return SourceStore(directory=None, extra=sources)


__all__ = ["refresh", "list_news", "render_news_html", "get_news_store", "parse_site_list", "parse_telegram",
           "parse_article_lead", "excerpt", "is_due", "iso_utc"]

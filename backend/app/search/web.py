"""Поиск в интернете для режима «Правда» (второй слой после своей базы).

Провайдер (SEARCH_PROVIDER в .env):
  tavily — Tavily Search API (бесплатно 1000 запросов в месяц), ключ SEARCH_API_KEY с https://app.tavily.com — надёжнее всего;
  brave  — Brave Search API, ключ SEARCH_API_KEY с https://api-dashboard.search.brave.com;
  ddg    — DuckDuckGo без ключа (HTML-выдача): работает сразу, но без дат публикации и может временно ограничить частые запросы;
  groq   — встроенный поиск gpt-oss на Groq (browser_search), ключ — LLM_API_KEY. На бесплатном тарифе один поиск съедает
           десятки тысяч токенов из суточных 200 000 — хватит на пару проверок в день, поэтому только по явной настройке;
  off    — без интернета.
Пусто — выбираем сами: есть SEARCH_API_KEY → tavily, иначе → ddg.

Порядок доверия к найденному: официальный сайт и канал МУИТ и госорганы → крупные СМИ Казахстана →
остальное. Модели передаём только то, что реально нашлось (заголовок, ссылка, дата, фрагмент), — придумать
источник она не может: правила честности (cards/rules.py) выбрасывают всё, чего не было в выдаче.
Перед отправкой запроса личные данные маскируются.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from backend.app.core.config import get_settings
from backend.app.rag.store import Retrieved, Source, index_source
from backend.app.security.masking import mask_sensitive

log = logging.getLogger(__name__)

OFFICIAL = ("iitu.edu.kz", "t.me/iitu_channel", "gov.kz", "egov.kz", "adilet.zan.kz", "edu.gov.kz", "akorda.kz", "primeminister.kz")
MEDIA = ("tengrinews.kz", "informburo.kz", "kursiv.media", "zakon.kz", "inform.kz", "kazinform", "vlast.kz", "orda.kz", "24.kz",
         "khabar.kz", "forbes.kz", "nur.kz", "azattyq.org", "the-steppe.com", "sputnik.kz", "kapital.kz", "liter.kz", "time.kz",
         "astanatimes.com", "baigenews.kz", "lsm.kz", "ktk.kz", "almaty.tv", "bes.media", "reformy.kz", "regtv.kz", "profit.kz",
         "prokadry.kz", "kazpravda.kz", "inbusiness.kz", "dknews.kz", "el.kz", "egemen.kz", "tengritv.kz", "ratel.kz", "kz.kursiv.media")
SOCIAL = ("instagram.com", "tiktok.com", "facebook.com", "vk.com", "youtube.com", "threads.net", "x.com", "twitter.com")
UNIVERSITY_WORDS = re.compile(r"муит|iitu|айти\s*универ|международн\w+\s+университет\w*\s+информац", re.IGNORECASE)
TIMEOUT = 15.0
GROQ_TIMEOUT = 60.0  # встроенный поиск Groq сам ходит по страницам — это дольше
CACHE_SECONDS = 6 * 3600


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str
    date: str = ""

    @property
    def domain(self) -> str:
        host = urlparse(self.url).netloc.lower().removeprefix("www.")
        if host == "t.me":
            path = urlparse(self.url).path.strip("/").split("/")[0]
            return f"t.me/{path}" if path else host
        return host

    @property
    def tier(self) -> int:
        """0 — официальный источник, 1 — СМИ, 2 — прочий сайт, 3 — соцсети."""
        full = self.domain
        if any(full == d or full.endswith("." + d) or full.startswith(d) for d in OFFICIAL):
            return 0
        if any(d in full for d in MEDIA):
            return 1
        if any(full == d or full.endswith("." + d) for d in SOCIAL):
            return 3
        return 2


def provider() -> str:
    s = get_settings()
    chosen = s.search_provider.strip().lower()
    if chosen:
        return chosen if chosen in ("tavily", "brave", "ddg", "groq", "off") else "off"
    if s.search_api_key.strip():
        return "tavily"
    return "ddg"


def enabled() -> bool:
    return provider() != "off"


_cache: dict[str, tuple[float, list[WebResult]]] = {}
_transport: httpx.AsyncBaseTransport | None = None  # подменяют тесты


def _client(timeout: float = TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


def _date(value: str) -> str:
    m = re.search(r"\d{4}-\d{2}-\d{2}", value or "")
    return m.group(0) if m else ""


async def _tavily(query: str, domains: list[str] | None) -> list[WebResult]:
    key = get_settings().search_api_key.strip()
    body = {"query": query, "max_results": 6, "search_depth": "basic", "api_key": key}
    if domains:
        body["include_domains"] = domains
    async with _client() as client:
        r = await client.post("https://api.tavily.com/search", json=body, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        data = r.json()
    return [WebResult(x.get("title", ""), x.get("url", ""), (x.get("content") or "")[:700], _date(x.get("published_date", "")))
            for x in data.get("results", []) if x.get("url")]


async def _brave(query: str, domains: list[str] | None) -> list[WebResult]:
    key = get_settings().search_api_key.strip()
    q = query + (" " + " OR ".join(f"site:{d}" for d in domains) if domains else "")
    async with _client() as client:
        r = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": 8, "search_lang": "ru"},
                             headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    return [WebResult(x.get("title", ""), x.get("url", ""), re.sub(r"<[^>]+>", "", x.get("description", ""))[:700],
                      _date(x.get("page_age", "") or x.get("age", "")))
            for x in (data.get("web") or {}).get("results", []) if x.get("url")]


DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_LINK = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _strip_tags(value: str) -> str:
    import html as html_lib

    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", value or "")).split())


def _ddg_url(href: str) -> str:
    """Ссылки бывают прямыми или через редирект //duckduckgo.com/l/?uddg=<адрес>."""
    import html as html_lib
    from urllib.parse import parse_qs, unquote, urlparse as parse

    href = html_lib.unescape(href)
    if "duckduckgo.com/l/" in href:
        target = parse_qs(parse(href if href.startswith("http") else "https:" + href).query).get("uddg", [""])[0]
        return unquote(target)
    return href


def parse_ddg(page: str) -> list[WebResult]:
    links = list(_DDG_LINK.finditer(page))
    snippets = [_strip_tags(m.group(1)) for m in _DDG_SNIPPET.finditer(page)]
    out = []
    for i, m in enumerate(links[:10]):
        url = _ddg_url(m.group(1))
        if not url.startswith("http") or "duckduckgo.com" in url:
            continue
        snippet = snippets[i] if i < len(snippets) else ""
        out.append(WebResult(_strip_tags(m.group(2)), url, snippet[:700], _date_in_text(snippet)))
    return out


async def _ddg(query: str, domains: list[str] | None) -> list[WebResult]:
    q = query + (" " + " ".join(f"site:{d}" for d in domains) if domains else "")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
    async with _client() as client:
        r = await client.post(DDG_URL, data={"q": q, "kl": "kz-ru"}, headers=headers, follow_redirects=True)
        r.raise_for_status()
    if r.status_code == 202 or "bots use DuckDuckGo" in r.text:
        # DuckDuckGo попросил капчу (много запросов с одного адреса) — это «поиск недоступен», а не «ничего не нашлось».
        raise ValueError("ddg captcha")
    return parse_ddg(r.text)


GROQ_SYSTEM = (
    "Ты помощник по поиску. Найди в интернете источники, которые подтверждают или опровергают утверждение. "
    "Сначала ищи на официальном сайте МУИТ iitu.edu.kz и в Telegram-канале t.me/iitu_channel, затем в крупных СМИ Казахстана. "
    "Верни ТОЛЬКО JSON {\"results\": [{\"title\": \"...\", \"url\": \"https://...\", \"date\": \"ГГГГ-ММ-ДД или пусто\", "
    "\"snippet\": \"дословный фрагмент со страницы\"}]} — не больше 6 результатов. Не выдумывай ссылки: только страницы, "
    "которые ты реально открыл в поиске. Ничего не нашёл — {\"results\": []}. "
    "Экономь: не больше двух поисковых запросов и не больше двух открытых страниц."
)


async def _groq(query: str, domains: list[str] | None) -> list[WebResult]:
    s = get_settings()
    body = {
        "model": "openai/gpt-oss-20b", "temperature": 0, "max_completion_tokens": 1000, "tool_choice": "required",
        "tools": [{"type": "browser_search"}],
        "messages": [{"role": "system", "content": GROQ_SYSTEM}, {"role": "user", "content": f"Сегодня {time.strftime('%Y-%m-%d')}. Утверждение: {query}"}],
    }
    async with _client(GROQ_TIMEOUT) as client:
        r = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {s.llm_api_key.strip()}"})
        r.raise_for_status()
        data = r.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    return parse_groq_message(message)


_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_RU_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s*,?\s+(20\d{2})", re.IGNORECASE)


_DOT_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(20\d{2})\b")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def _date_in_text(text: str) -> str:
    """Дата публикации из текста страницы: самая ранняя в тексте из «1 июля 2026», «01.07.2026», «2026-07-01»
    (на страницах новостей первой идёт дата самой статьи, дальше — даты соседних новостей)."""
    text = text or ""
    found: list[tuple[int, str]] = []
    if m := _RU_DATE.search(text):
        found.append((m.start(), f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"))
    if m := _DOT_DATE.search(text):
        if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
            found.append((m.start(), f"{m.group(3)}-{m.group(2)}-{m.group(1)}"))
    if m := _ISO_DATE.search(text):
        found.append((m.start(), m.group(0)))
    return min(found)[1] if found else ""


def _page_lines(content: str) -> str:
    """Просмотренная страница приходит строками «L12: текст» — убираем номера и служебные пометки."""
    lines = [re.sub(r"^L\d+:\s?", "", ln) for ln in (content or "").splitlines()]
    return " ".join(ln.strip() for ln in lines if ln.strip() and not ln.startswith(("URL:", "#【", "【"))).strip()


def _same(a: str, b: str) -> bool:
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.startswith(b) or b.startswith(a)


def parse_groq_message(message: dict) -> list[WebResult]:
    """Ответ Groq browser_search → источники. Берём только страницы, которые поиск реально нашёл или открыл
    (executed_tools); текст страницы — как фрагмент для проверки, дата — из ответа модели или из текста страницы."""
    found: dict[str, dict] = {}  # url → {title, text}
    for tool in message.get("executed_tools") or []:
        for x in ((tool.get("search_results") or {}).get("results") or []):
            url = str(x.get("url") or "")
            if not url.startswith("http"):
                continue
            title = str(x.get("title") or "")
            content = str(x.get("content") or "")
            if content.startswith("No `find` results"):
                continue
            key = next((u for u in found if _same(u, url)), url)
            item = found.setdefault(key, {"title": "", "text": ""})
            if "viewing lines" in title or content.startswith("L0") or content.lstrip().startswith(("L", "#")):
                item["text"] = (item["text"] + " " + _page_lines(content)).strip()[:1500]
            elif title and not item["title"]:
                item["title"] = title
    out: list[WebResult] = []
    used: set[str] = set()
    m = re.search(r"\{.*\}", message.get("content") or "", re.S)
    try:
        parsed = json.loads(m.group(0)).get("results", []) if m else []
    except ValueError:
        parsed = []
    for x in parsed:
        url = str(x.get("url", ""))
        if not url.startswith("http"):
            continue
        key = next((u for u in found if _same(u, url)), None)
        if found and key is None:
            continue  # ссылки нет в том, что поиск реально вернул, — не верим
        page = found.get(key, {}) if key else {}
        text = page.get("text", "")
        snippet = str(x.get("snippet", "")) or text
        out.append(WebResult(str(x.get("title", "")) or page.get("title", ""), url, (snippet + (" " + text if text and text not in snippet else ""))[:700],
                             _date(str(x.get("date", ""))) or _date_in_text(text)))
        used.add(key or url)
    for url, page in found.items():
        if url in used or not (page["title"] or page["text"]):
            continue
        out.append(WebResult(page["title"] or url, url, page["text"][:700], _date_in_text(page["text"])))
    return out


async def search(claim: str) -> list[WebResult]:
    """Ищет по утверждению: сначала официальные источники МУИТ (если речь о вузе), потом везде."""
    results, _ = await search_checked(claim)
    return results


async def search_checked(claim: str) -> tuple[list[WebResult], bool]:
    """(результаты, поиск сработал). False — сервис поиска недоступен (лимит, сеть), а не «ничего не нашлось»."""
    name = provider()
    query = " ".join(mask_sensitive(claim).split())[:300]
    if name == "off" or len(query) < 6:
        return [], name != "off"
    cache_key = f"{name}:{query.lower()}"
    if (hit := _cache.get(cache_key)) and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], True
    func = {"tavily": _tavily, "brave": _brave, "ddg": _ddg, "groq": _groq}[name]
    results: list[WebResult] = []
    try:
        if UNIVERSITY_WORDS.search(query) and name != "groq":
            results += await func(query, ["iitu.edu.kz"])
        # Свежие события («новый ректор», «перенесли сессию»): без года в запросе поиск тянет старые новости.
        year = time.strftime("%Y")
        results += await func(query if re.search(r"\b20\d{2}\b", query) or name == "groq" else f"{query} {year}", None)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
        log.warning("Поиск в интернете (%s) не сработал: %s %s", name, exc.__class__.__name__, code)
        return [], False
    unique: dict[str, WebResult] = {}
    for r in results:
        unique.setdefault(r.url.rstrip("/"), r)
    candidates = list(unique.values())[:14]
    await _add_dates(candidates)
    ranked = rank(candidates)[:7]
    _cache[cache_key] = (time.time(), ranked)
    return ranked, True


def rank(results: list[WebResult], today: str | None = None) -> list[WebResult]:
    """Порядок для модели: сначала свежие (за последний год), среди них — официальные, потом СМИ, сайты, соцсети;
    без даты — посередине; старые — в конце. Иначе старая новость вуза («назначен ректор» 2018 года)
    перевешивает свежую статью о новом назначении."""
    from datetime import date, timedelta

    year_ago = ((date.fromisoformat(today) if today else date.today()) - timedelta(days=365)).isoformat()

    def freshness(r: WebResult) -> int:
        if not r.date:
            return 1
        return 0 if r.date >= year_ago else 2

    order = {id(r): i for i, r in enumerate(results)}
    return sorted(results, key=lambda r: (freshness(r), r.tier, order[id(r)]))


_META_DATE = re.compile(r'(?:article:published_time|datePublished|pubdate|date)["\']?\s*(?:content|:)\s*=?\s*["\']?(\d{4}-\d{2}-\d{2})', re.I)


async def _page_date(client: httpx.AsyncClient, result: WebResult) -> None:
    """Дата публикации со страницы: метатег или «1 июля 2026» в начале текста. Без даты модель не отличит старую новость от новой."""
    try:
        r = await client.get(result.url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; Verdikt-bot/1.0)"})
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return
        page = r.text[:300_000]
    except (httpx.HTTPError, ValueError):
        return
    m = _META_DATE.search(page)
    text = _strip_tags(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I))
    result.date = (m.group(1) if m else "") or _date_in_text(text[:6000])
    if not result.snippet or len(result.snippet) < 60:
        # Фрагмент вокруг заголовка — чтобы модели было что сверять.
        pos = text.find(result.title[:30]) if result.title else -1
        result.snippet = text[max(0, pos):max(0, pos) + 600] if pos >= 0 else text[:600]


async def _add_dates(results: list[WebResult]) -> None:
    missing = [r for r in results if not r.date and r.tier < 3][:8]
    if not missing:
        return
    async with httpx.AsyncClient(timeout=8.0, transport=_transport) as client:
        await asyncio.gather(*(_page_date(client, r) for r in missing))


def to_retrieved(results: list[WebResult]) -> list[Retrieved]:
    """Результаты поиска — в формат источников движка (как файлы базы знаний)."""
    out = []
    for i, r in enumerate(results):
        tier_label = ("официальный источник", "СМИ", "сайт", "соцсеть")[r.tier]
        source = index_source(Source(
            id=f"web:{i}", title=f"{r.domain}: {r.title}"[:200], url=r.url, date=r.date, publisher=f"{r.domain} ({tier_label})",
            modes=["pravda"], text=r.snippet,
        ))
        out.append(Retrieved(source, score=10.0 - r.tier * 2 - i * 0.1, snippet=r.snippet))
    return out


def reset_cache() -> None:
    _cache.clear()

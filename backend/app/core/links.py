"""Анализ ссылок БЕЗ открытия их в браузере.

Суть: мы никогда не переходим по присланной ссылке (там может быть
вирус или фишинговая страница). Мы смотрим только на САМ АДРЕС:
  - похож ли домен на банковский (kaspi.kz -> kaspi-bonus.kz, kasp1.kz);
  - есть ли «подмена символов» (кириллическая «а» вместо латинской);
  - punycode (xn--...) — так записываются домены с нелатинскими буквами;
  - сокращатели ссылок (bit.ly) прячут настоящий адрес;
  - IP-адрес вместо имени, подозрительные доменные зоны (.xyz, .top);
  - возраст домена — по официальному реестру RDAP (по желанию, с интернетом).
    Это запрос к реестру доменов, а не к самому сайту.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Официальные домены, которые чаще всего подделывают в Казахстане.
OFFICIAL_DOMAINS: dict[str, str] = {
    "kaspi.kz": "Kaspi Bank",
    "halykbank.kz": "Halyk Bank",
    "homebank.kz": "Halyk Homebank",
    "egov.kz": "Портал eGov",
    "gov.kz": "Госорганы РК",
    "adilet.zan.kz": "Адилет (законы РК)",
    "jusan.kz": "Jusan Bank",
    "bcc.kz": "Банк ЦентрКредит",
    "forte.kz": "ForteBank",
    "freedombank.kz": "Freedom Bank",
    "otbasybank.kz": "Отбасы банк",
    "enpf.kz": "ЕНПФ",
    "nationalbank.kz": "Национальный банк РК",
    "olx.kz": "OLX",
    "krisha.kz": "Krisha.kz",
    "kolesa.kz": "Kolesa.kz",
    "kazpost.kz": "Казпочта",
    "telegram.org": "Telegram",
    "t.me": "Telegram",
}
# Ключевые «бренды» — если они встречаются в чужом домене, это подозрительно.
BRANDS = ("kaspi", "halyk", "homebank", "egov", "jusan", "otbasy", "enpf", "kazpost", "olx", "krisha", "kolesa", "freedom", "forte", "nationalbank", "nacbank")

SHORTENERS = {"bit.ly", "tinyurl.com", "clck.ru", "cutt.ly", "is.gd", "t.co", "goo.su", "u.to", "vk.cc", "rb.gy", "shorturl.at", "tiny.cc"}
RISKY_TLDS = {"xyz", "top", "click", "online", "site", "icu", "live", "shop", "buzz", "info", "fun", "cyou", "rest", "monster", "sbs"}

# Буквы кириллицы, которые выглядят как латинские (а, е, о, р, с, х, у, к ...)
_CONFUSABLE = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "к": "k", "і": "i", "ӏ": "l"})
# Цифры, которыми заменяют буквы: kasp1 -> kaspi, ha1yk -> halyk
_DIGIT_SWAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})

URL_RE = re.compile(
    r"(?:(?:https?://)|(?:www\.))[^\s<>\"'«»]+|\b(?:[a-zа-я0-9-]+\.)+(?:kz|ru|com|net|org|xyz|top|click|online|site|info|shop|live|icu|ly|me|io|gl|cc|su|uz|kg)(?:/[^\s<>\"'«»]*)?",
    re.IGNORECASE,
)


@dataclass
class LinkReport:
    url: str
    domain: str
    risk: int = 0  # 0 — нет замечаний, 1–2 — подозрительно, 3+ — опасно
    findings: list[str] = field(default_factory=list)
    official_name: str = ""  # если это официальный домен
    age_days: int | None = None

    @property
    def is_official(self) -> bool:
        return bool(self.official_name)


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)»")
        if url not in urls:
            urls.append(url)
    return urls[:10]


def _domain_of(url: str) -> str:
    raw = url if "://" in url else "http://" + url
    host = (urlsplit(raw).hostname or "").lower().strip(".")
    return host


def _levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна: сколько букв надо поменять, чтобы из a получить b."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _official_match(domain: str) -> str:
    for official, name in OFFICIAL_DOMAINS.items():
        if domain == official or domain.endswith("." + official):
            return name
    return ""


def analyze_url(url: str) -> LinkReport:
    """Анализирует адрес ссылки (без сети)."""
    domain = _domain_of(url)
    report = LinkReport(url=url, domain=domain)
    if not domain:
        report.findings.append("Не удалось разобрать адрес ссылки.")
        report.risk += 1
        return report

    official = _official_match(domain)
    if official:
        report.official_name = official
        report.findings.append(f"Домен {domain} — официальный адрес ({official}).")
        if url.lower().startswith("http://"):
            report.findings.append("Но ссылка без защищённого соединения (http, не https).")
            report.risk += 1
        return report

    # Не латинские буквы в домене или punycode
    if re.search(r"[а-яёәғқңөұүһі]", domain):
        report.findings.append("В адресе есть русские/казахские буквы — так подделывают известные домены.")
        report.risk += 2
    if "xn--" in domain:
        report.findings.append("Домен записан в punycode (xn--…): буквы могут лишь выглядеть как латинские.")
        report.risk += 2

    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", domain):
        report.findings.append("Вместо имени сайта — IP-адрес. Настоящие банки так не делают.")
        report.risk += 2

    if domain in SHORTENERS:
        report.findings.append("Сокращённая ссылка прячет настоящий адрес. Не открывайте её.")
        report.risk += 1
        path = url.lower().split(domain, 1)[-1]
        brand = next((b for b in BRANDS if b in path), None)
        if brand:
            report.findings.append(f"Короткая ссылка маскируется под «{brand}» — настоящие организации так не делают.")
            report.risk += 2

    tld = domain.rsplit(".", 1)[-1]
    if tld in RISKY_TLDS:
        report.findings.append(f"Доменная зона .{tld} часто используется для одноразовых мошеннических сайтов.")
        report.risk += 1

    if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
        report.findings.append("В адресе есть «@»: настоящий сайт указан после него, начало — обманка.")
        report.risk += 2

    # Похожесть на бренд: бренд внутри чужого домена или «почти» бренд
    normalized = domain.translate(_CONFUSABLE).translate(_DIGIT_SWAP)
    labels = re.split(r"[.-]", normalized)
    for brand in BRANDS:
        if brand in normalized:
            report.findings.append(f"В адресе есть «{brand}», но это НЕ официальный домен. Похоже на подделку.")
            report.risk += 3
            break
        if any(len(label) >= 4 and 0 < _levenshtein(label, brand) <= 1 for label in labels):
            report.findings.append(f"Адрес отличается от «{brand}» на одну букву — типичная подмена.")
            report.risk += 3
            break

    if url.lower().startswith("http://"):
        report.findings.append("Нет защищённого соединения (http, не https).")
        report.risk += 1

    if not report.findings:
        report.findings.append("Явных признаков подделки в адресе нет. Это не гарантия, что сайт безопасен.")
    return report


async def lookup_domain_age(domain: str, timeout: float = 5.0) -> int | None:
    """Возраст домена в днях через RDAP (реестр доменов). None — неизвестно.

    RDAP — официальный протокол реестров доменов (замена WHOIS). Мы
    обращаемся к rdap.org, а НЕ к самому сайту из ссылки.
    Для зоны .kz RDAP может быть недоступен — тогда честно «неизвестно».
    """
    import httpx  # импорт внутри функции: модуль нужен только при онлайн-проверке

    base = ".".join(domain.split(".")[-2:])  # login.kaspi-bonus.kz -> kaspi-bonus.kz
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(f"https://rdap.org/domain/{base}")
        if response.status_code != 200:
            return None
        for event in response.json().get("events", []):
            if event.get("eventAction") == "registration":
                registered = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - registered).days
    except Exception as exc:  # noqa: BLE001 — сеть ненадёжна, это не ошибка проверки
        log.debug("RDAP недоступен для %s: %s", base, exc)
    return None


async def analyze_links(text: str, online: bool) -> list[LinkReport]:
    reports = [analyze_url(u) for u in extract_urls(text)]
    if online:
        for report in reports:
            if report.is_official or not report.domain or report.domain in SHORTENERS:
                continue
            age = await lookup_domain_age(report.domain)
            report.age_days = age
            if age is not None and age < 90:
                report.findings.append(f"Домен зарегистрирован всего {age} дн. назад — у мошеннических сайтов короткая жизнь.")
                report.risk += 2
    return reports

"""Прочее: планировщик, PDF и картинка, локализация, промпты, профиль бота."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.core.scheduler import tick
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from backend.app.i18n import load_strings
from backend.app.llm.prompts import load_prompt
from backend.app.modes.registry import MODES
from backend.app.telegram.profile import build_commands


def test_scheduler_reminders(notifier):
    with get_sessionmaker()() as session:
        repo.upsert_user(session, 501, "Подписчик", bot_started=True)
        session.commit()
        tomorrow = (date(2026, 10, 7) + timedelta(days=1)).isoformat()
        repo.create_agreement(session, creator_id=501, creator_name="А", counterparty_id=502, status="confirmed",
                              text="Вернуть 5000", draft_json='{"what": "Вернуть 5000"}', deadline_iso=tomorrow)
    now = datetime(2026, 10, 7, 11, 0)
    asyncio.run(tick(notifier, now))
    recipients = [chat for chat, _ in notifier.sent]
    assert 501 in recipients and 502 in recipients
    assert any("Завтра срок" in text for _, text in notifier.sent)
    count = len(notifier.sent)
    asyncio.run(tick(notifier, now + timedelta(minutes=1)))  # второй раз за день — ничего не шлём
    assert len(notifier.sent) == count


def test_card_image_and_pdf():
    from backend.app.cards.image import render_card_png
    from backend.app.cards.schema import Reason, VerdictCard
    from backend.app.core.pdf import agreement_pdf

    card = VerdictCard(status="red", title="Похоже на мошенничество 🚨", confidence=90,
                       reasons=[Reason(text="Просят код", quote="код из SMS")], do=["Позвоните в банк"], dont=["Не сообщайте код"])
    assert render_card_png(card)[:8] == b"\x89PNG\r\n\x1a\n"
    with get_sessionmaker()() as session:
        agreement = repo.create_agreement(session, creator_id=1, creator_name="Аскар", text="Аскар отдаёт 20 000 ₸",
                                          draft_json='{"what": "Отдать долг", "amount": "20 000 ₸"}')
    assert agreement_pdf(agreement)[:4] == b"%PDF"


def _keys_used_in_code() -> set[str]:
    keys: set[str] = set()
    # Только «целые» ключи в кавычках: t("a.b") — да, t("a." + x) — нет (такие проверяет test_dynamic_i18n_keys_exist).
    py_re = re.compile(r"""\bt\(\s*["']([a-z_]+\.[a-z0-9_.]*[a-z0-9_])["']""")
    js_re = re.compile(r"""\btr\(\s*["']([a-z_]+\.[a-z0-9_.]*[a-z0-9_])["']|data-i18n="([a-z_.0-9]+)\"""")
    for path in (PROJECT_ROOT / "backend").rglob("*.py"):
        keys |= set(py_re.findall(path.read_text(encoding="utf-8")))
    for path in list((PROJECT_ROOT / "miniapp").rglob("*.js")) + [PROJECT_ROOT / "miniapp" / "index.html"]:
        for a, b in js_re.findall(path.read_text(encoding="utf-8")):
            keys.add(a or b)
    return keys


def test_all_i18n_keys_exist():
    strings = load_strings("ru")
    missing = sorted(k for k in _keys_used_in_code() if k not in strings)
    assert not missing, f"Нет в ru.json: {missing}"


def test_dynamic_i18n_keys_exist():
    strings = load_strings("ru")
    for mode in MODES:
        assert f"mode.{mode.key}.title" in strings and f"bot.cmd.{mode.command}" in strings
        if mode.is_check:
            assert f"bot.ask_input.{mode.key}" in strings
    for status in ("green", "yellow", "red", "unknown"):
        assert f"card.status.{status}" in strings
    for status in ("pending", "confirmed", "declined", "done", "cancelled"):
        assert f"agr.status.{status}" in strings


def test_bot_profile_limits():
    strings = load_strings("ru")
    # Telegram считает длину в UTF-16 (эмодзи — 2 единицы)
    assert len(strings["bot.short_description"].encode("utf-16-le")) // 2 <= 120
    assert len(strings["bot.description"].encode("utf-16-le")) // 2 <= 512
    commands = build_commands()
    names = [c for c, _ in commands]
    # Исходные режимы «Вердикта» — первыми и в прежнем порядке; затем режимы и разделы МУИТ, в конце служебные.
    assert names[:7] == ["pravda", "razvod", "spor", "dogovorilis", "chek", "prava", "sprosi"]
    assert names[-2:] == ["help", "delete"] and len(names) <= 20
    assert {"radar", "fakty", "plan", "syllabus", "put", "news", "moi", "settings"} <= set(names)
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", c) and 3 <= len(d) <= 256 for c, d in commands)


def test_prompts_have_versions():
    for name in ["base"] + [m.prompt for m in MODES] + ["simplify", "trainer_scammer", "trainer_review"]:
        text, version = load_prompt(name)
        assert text and version.split("@")[1].isdigit(), name


def test_no_real_article_numbers_in_demo_sources():
    """Демо-источники не должны выдавать себя за конкретные статьи закона."""
    for path in Path(PROJECT_ROOT / "data" / "sources").glob("demo_*.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bстать[ьяи]\s*\d+", text, re.IGNORECASE), path.name
        assert "Демо-текст" in text, path.name

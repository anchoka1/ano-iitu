"""Анализ без ИИ: признаки мошенничества, ссылки, источники, даты и суммы."""

from __future__ import annotations

from datetime import date

from backend.app.core.guard import RateLimiter, is_surveillance_request
from backend.app.core.links import analyze_url, extract_urls
from backend.app.core.signals import MANIPULATION_RULES, find_signals, is_opinion
from backend.app.core.textparse import find_deadline, find_money
from backend.app.rag.store import get_store

SMS = "Ваша карта заблокирована! Служба безопасности банка. Срочно назовите код из SMS, иначе деньги спишутся."


def test_scam_signals_found_with_quotes():
    hits = {h.id: h for h in find_signals(SMS)}
    assert {"account_blocked", "fake_authority", "urgency", "code_request"} <= set(hits)
    assert any("код из SMS" in q for q in hits["code_request"].quotes)


def test_no_signals_in_normal_text():
    assert find_signals("Привет! Встречаемся завтра в 18:00 у школы.") == []


def test_manipulation_and_opinion():
    hits = find_signals("Срочно разошлите всем, пока не удалили! СМИ молчат.", MANIPULATION_RULES)
    assert {h.id for h in hits} >= {"spread_call", "secret_knowledge"}
    assert is_opinion("По-моему, это самый ужасный закон")


def test_extract_urls():
    urls = extract_urls("Перейдите: https://kaspi-bonus.kz/login и www.egov.kz, а также bit.ly/abc")
    assert "https://kaspi-bonus.kz/login" in urls
    assert any("bit.ly" in u for u in urls)


def test_official_domain():
    report = analyze_url("https://kaspi.kz/shop")
    assert report.is_official and report.risk == 0


def test_brand_in_fake_domain():
    report = analyze_url("https://kaspi-bonus.kz/login")
    assert report.risk >= 3 and not report.is_official


def test_digit_swap():
    assert analyze_url("http://ha1yk.top").risk >= 3


def test_cyrillic_lookalike():
    report = analyze_url("https://kаspi.kz")  # «а» — кириллическая
    assert report.risk >= 2 and not report.is_official


def test_shortener():
    assert analyze_url("https://bit.ly/3xYz").risk >= 1


def test_rag_finds_fake_source():
    results = get_store().search("С понедельника штраф за голосовые сообщения в WhatsApp", "pravda")
    assert results and results[0].source.id == "demo_fake_voice_fine"


def test_rag_nothing_for_unrelated():
    assert get_store().search("Как приготовить плов из баранины", "pravda") == []


def test_all_sources_have_date_and_title():
    for source in get_store().sources:
        assert source.title and source.date and source.url.startswith("https://"), source.id
        if source.id.startswith("demo_"):
            assert source.is_demo and "[ДЕМО]" in source.title


def test_money_and_dates():
    assert find_money("Штраф 25 000 ₸ и ещё 300 тг")[0].startswith("25 000")
    today = date(2026, 10, 7)
    assert find_deadline("до 15 октября", today)[1] == "2026-10-15"
    assert find_deadline("до 5 марта", today)[1] == "2027-03-05"  # прошедший месяц — следующий год
    assert find_deadline("оплатить до 20.10.2026", today)[1] == "2026-10-20"
    assert find_deadline("верну завтра", today)[1] == "2026-10-08"
    assert find_deadline("через 2 недели", today)[1] == "2026-10-21"
    assert find_deadline("когда-нибудь", today) == ("", "")


def test_surveillance_detected():
    assert is_surveillance_request("Как узнать где находится человек по номеру телефона")
    assert is_surveillance_request("хочу прочитать переписку мужа в whatsapp")
    assert not is_surveillance_request("Мне пришло SMS с номера +7 701 000 00 00, это развод?")


def test_rate_limiter():
    limiter = RateLimiter(per_minute=2, per_day=3)
    assert limiter.check(1, now=0) is None
    assert limiter.check(1, now=1) is None
    assert "минуту" in limiter.check(1, now=2)
    assert limiter.check(1, now=100) is None
    assert "Дневной" in limiter.check(1, now=200)
    assert limiter.check(2, now=200) is None  # у другого человека свой лимит

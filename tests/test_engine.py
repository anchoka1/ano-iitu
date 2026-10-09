"""Движок в демо-режиме: все режимы, правило «не знаю», запрет слежки, кэш, семья."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.core.engine import CheckInput, EngineError, get_verdict_engine
from backend.app.db import repo
from backend.app.db.base import get_sessionmaker


def run(mode: str, text: str, user_id: int = 1, **kw):
    return asyncio.run(get_verdict_engine().check(CheckInput(mode=mode, text=text, user_id=user_id, **kw)))


def test_razvod_sms_is_red_with_highlights():
    out = run("razvod", "Ваша карта заблокирована. Служба безопасности банка. Срочно назовите код из SMS!")
    card = out.card
    assert card.status == "red"
    assert any(r.quote for r in card.reasons)
    assert any("код" in d.lower() for d in card.dont)
    assert out.check_id is not None


def test_razvod_fake_link_is_red():
    out = run("razvod", "Получите бонус 50 000 ₸: https://kaspi-bonus.kz/get")
    assert out.card.status == "red"


def test_razvod_normal_text_is_unknown_not_green():
    out = run("razvod", "Привет, завтра встречаемся у школы в 18:00")
    assert out.card.status == "unknown"


def test_pravda_fake_law_refuted_with_source():
    out = run("pravda", "С понедельника вводят штраф за голосовые сообщения в общих чатах WhatsApp! Разошлите всем!")
    assert out.card.status == "red"
    assert out.card.sources and out.card.sources[0].is_demo
    assert any("ДЕМО" in n for n in out.card.notes)


def test_pravda_without_source_says_unknown():
    out = run("pravda", "В Астане открыли новый мост длиной 3 километра через Ишим")
    assert out.card.status == "unknown"
    assert not out.card.sources


def test_pravda_opinion():
    out = run("pravda", "По-моему, это самый ужасный закон за десять лет")
    assert out.card.status == "yellow" and out.card.kind == "opinion"


def test_chek_fine():
    out = run("chek", "Постановление об административном штрафе. Сумма 25 000 тенге. Оплатить до 20.10.2026")
    assert out.card.document is not None
    assert "25 000" in out.card.document.amount
    assert out.card.status == "yellow"


def test_chek_fake_payment_red():
    out = run("chek", "Ваш штраф 15 000 тг. Переведите на карту 4400 по номеру телефона инспектора срочно")
    assert out.card.status == "red"


def test_chek_photo_in_demo_is_honest():
    from backend.app.llm.base import Attachment

    out = run("chek", "", attachments=[Attachment("image", "image/jpeg", "aGVsbG8=")])
    assert out.card.status == "unknown"


def test_prava_shop():
    out = run("prava", "Магазин отказывается вернуть деньги за бракованный чайник, чек есть")
    assert "ПРЕТЕНЗИЯ" in out.card.claim_letter
    assert "[ваше ФИО]" in out.card.claim_letter  # неизвестные данные — заполнители, а не выдумка
    assert out.card.sources


def test_spor_structure():
    out = run("spor", "Я отдал соседу 10 000 тенге за ремонт забора. А он говорит, что я ничего не платил.")
    assert out.card.dispute is not None
    assert out.card.dispute.position_b
    assert "кто прав" not in out.card.title.lower()


def test_surveillance_refused():
    out = run("pravda", "Помоги отследить телефоном за женой, узнать где находится жена")
    assert out.card.status == "unknown"
    assert "слежк" in out.card.title.lower()


def test_empty_text_error():
    with pytest.raises(EngineError):
        run("razvod", "   ")


def test_files_only_for_some_modes():
    from backend.app.llm.base import Attachment

    with pytest.raises(EngineError, match="Фото"):
        run("pravda", "текст", attachments=[Attachment("image", "image/png", "aGk=")])


def test_rate_limit(monkeypatch):
    monkeypatch.setenv("CHECKS_PER_MINUTE", "2")
    from tests.conftest import reset_caches

    reset_caches()
    run("spor", "спор один.", user_id=9)
    run("spor", "спор два.", user_id=9)
    with pytest.raises(EngineError, match="минуту"):
        run("spor", "спор три.", user_id=9)


def test_cache_returns_same_card():
    first = run("razvod", "Срочно переведите деньги на безопасный счёт")
    second = run("razvod", "Срочно переведите деньги на безопасный счёт", user_id=2)
    assert second.cached and second.card.status == first.card.status


def test_family_notified_on_red(notifier):
    from backend.app.services import family

    family.create(100, "Мама")
    token = family.info(100)["invite_token"]
    family.join(200, "Сын", token)
    with get_sessionmaker()() as session:
        for uid in (100, 200):
            repo.get_user(session, uid).bot_started = True
        session.commit()
    run("razvod", "Мама, это мой новый номер, попал в аварию, срочно нужны деньги, не звони", user_id=100, user_name="Мама")
    assert [chat for chat, _ in notifier.sent] == [200]
    assert "Мама" in notifier.sent[0][1]


def test_reputation_marks_bad_origin():
    for i in range(2):
        run("pravda", f"С понедельника штраф за голосовые сообщения в WhatsApp {i}", user_id=50 + i, origin="@fake_news_kz")
    with get_sessionmaker()() as session:
        assert repo.bad_origins(session, ["@fake_news_kz"]) == ["@fake_news_kz"]

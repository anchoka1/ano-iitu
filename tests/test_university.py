"""Университетская версия (МУИТ): онбординг, база знаний, новости, календарь,
навигатор, групповые договорённости, правила достоверности, кризис, миграция БД.

Без интернета: страницы сайта и канала — сохранённые копии в tests/fixtures/.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from tests.conftest import tma_header
from tests.test_bot import Harness

FIXTURES = Path(__file__).parent / "fixtures"
OFFICIAL_IBAN = "KZ926010131000124681"


def _student(user_id: int, course: str = "1", year: int = 2026, started: bool = True) -> None:
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, user_id, "Студент", bot_started=started)
        repo.set_profile(session, user, academic_year=year, role="student", course=course, faculty="fctc")


# ------------------------------------------------------------------ миграция


def test_migration_upgrades_old_database_without_losing_data(tmp_path, monkeypatch):
    """Старая база «Вердикта» (до МУИТ) получает новые колонки и таблицы, данные остаются."""
    from tests.conftest import reset_caches

    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT UNIQUE, first_name VARCHAR(128), language_code VARCHAR(8),
            created_at DATETIME, last_seen_at DATETIME, bot_started BOOLEAN, large_font BOOLEAN, daily_subscribed BOOLEAN,
            streak INTEGER, last_active_date VARCHAR(10), family_id INTEGER, family_notify BOOLEAN, code_word_set BOOLEAN);
        INSERT INTO users (telegram_id, first_name, language_code, bot_started, large_font, daily_subscribed, streak,
            last_active_date, family_notify, code_word_set) VALUES (555, 'Старый', 'ru', 1, 1, 1, 3, '2026-10-01', 1, 0);
        CREATE TABLE agreements (id INTEGER PRIMARY KEY, code VARCHAR(16) UNIQUE, chat_id BIGINT, message_id BIGINT, creator_id BIGINT,
            creator_name VARCHAR(128), counterparty_id BIGINT, counterparty_name VARCHAR(128), counterparty_username VARCHAR(64),
            text TEXT, draft_json TEXT, status VARCHAR(12), deadline_iso VARCHAR(10), reminded_before BOOLEAN, reminded_due BOOLEAN,
            created_at DATETIME, confirmed_at DATETIME);
        INSERT INTO agreements (code, creator_id, creator_name, text, draft_json, status, deadline_iso, reminded_before, reminded_due)
            VALUES ('OLD1', 555, 'Старый', 'Верну книгу', '{}', 'pending', '', 0, 0);
    """)
    con.commit()
    con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    reset_caches()
    from backend.app.db.init_db import init_db
    from backend.app.db.migrations import current_version, latest_version
    from backend.app.db.base import get_engine

    init_db()
    init_db()  # повторный запуск безопасен
    with get_sessionmaker()() as session:
        user = repo.get_user(session, 555)
        assert user.first_name == "Старый" and user.streak == 3 and user.large_font
        assert user.role == "" and user.onboarded is False and user.news_subscribed is False and user.calendar_reminders is True
        assert repo.get_agreement(session, "OLD1").multi is False
    with get_engine().connect() as conn:
        assert current_version(conn) == latest_version() == 4
    assert (tmp_path / "old.db.bak-001").exists()  # копия базы перед миграцией


# ------------------------------------------------------------------ онбординг


def test_student_onboarding_and_greeting():
    h = Harness()
    h.message("/start", user_id=70, name="Аружан")
    assert "Кто ты?" in h.last_text()
    h.click("onb:role:student", user_id=70, name="Аружан")
    assert "курсе" in h.last_text()
    h.click("onb:course:1", user_id=70, name="Аружан")
    assert "факультет" in h.last_text()
    h.click("onb:fac:fctc", user_id=70, name="Аружан")
    assert "6B06101" in " ".join(h.last_markup_data()) or "программа" in h.last_text()
    h.click("onb:prog:6B06101", user_id=70, name="Аружан")
    assert "языке" in h.last_text()
    h.click("onb:lang:kk", user_id=70, name="Аружан")  # казахский ещё готовится → русский
    texts = h.session.texts()
    assert any("Студент · 1 курс · ФКТК · Компьютерные науки" in x for x in texts)
    assert any("Что меняется в этом году" in x for x in texts)
    with get_sessionmaker()() as session:
        user = repo.get_user(session, 70)
        assert user.onboarded and user.ui_lang == "ru" and user.course == "1" and user.program == "6B06101"
    h.message("/start", user_id=70, name="Аружан")
    assert "Это ANO IITU" in h.last_text()


def test_teacher_onboarding_uses_formal_tone_and_gives_no_rights():
    h = Harness()
    h.message("/start", user_id=71, name="Асель")
    h.click("onb:role:teacher", user_id=71, name="Асель")
    assert "не даёт прав" in h.last_text()
    h.click("onb:dep:cyber", user_id=71, name="Асель")
    h.click("onb:lang:ru", user_id=71, name="Асель")
    assert "Здравствуйте, Асель" in h.last_text() and "Кибербезопасность" in h.last_text()
    with get_sessionmaker()() as session:
        user = repo.get_user(session, 71)
        assert user.role == "teacher" and user.course == "" and user.department == "cyber"


def test_settings_toggle_news_and_calendar():
    h = Harness()
    h.message("/settings", user_id=72)
    assert "Новости МУИТ: выключены" in h.last_text()  # по умолчанию не спамим
    h.click("set:news:1", user_id=72)
    with get_sessionmaker()() as session:
        assert repo.get_user(session, 72).news_subscribed
    h.click("set:cal:0", user_id=72)
    with get_sessionmaker()() as session:
        assert not repo.get_user(session, 72).calendar_reminders


# ------------------------------------------------------------------ база знаний и достоверность


def test_sprosi_answers_from_knowledge_base_with_source_and_date():
    h = Harness()
    h.message("/sprosi Когда зимняя сессия?")
    card = h.last_text()
    assert "🎓" in card and "14–31 декабря" in card
    assert "академический календарь" in card.lower() and "2026-10-07" in card


def test_sprosi_without_answer_says_dont_know_and_where_to_go():
    h = Harness()
    h.message("/sprosi Какой пароль от вайфая в столовой?")
    card = h.last_text()
    assert "Не знаю" in card and "Обратись" in card and "⚪" in card


def test_payment_question_never_prints_requisites():
    h = Harness()
    h.message("/sprosi Какие реквизиты для оплаты обучения?")
    card = h.last_text()
    assert OFFICIAL_IBAN not in card and "student-guide" in card


def test_scrub_rule_removes_invented_requisites_amounts_and_dates():
    from backend.app.cards.rules import enforce_university
    from backend.app.cards.schema import SourceRef, VerdictCard
    from backend.app.modes.registry import get_mode
    from backend.app.rag.store import get_store

    retrieved = get_store().search("оплата обучения реквизиты", "vopros")
    card = VerdictCard(status="green", kind="answer", title="Оплата", confidence=80,
                       answer=f"Плати на {OFFICIAL_IBAN}, сумма 950 000 тенге до 15 сентября.",
                       sources=[SourceRef(title=retrieved[0].source.title, url=retrieved[0].source.url)])
    card = enforce_university(card, get_mode("vopros"), "Как оплатить обучение?", retrieved)
    assert OFFICIAL_IBAN not in card.answer and "950 000" not in card.answer and "15 сентября" not in card.answer
    assert any("официальной странице" in x for x in card.do)


def test_tuition_scam_with_foreign_requisites_is_red():
    h = Harness()
    h.message("/razvod Срочно оплатите обучение в МУИТ до пятницы на новый счёт KZ12 3456 7890 1234 5678, старые реквизиты не работают")
    card = h.last_text()
    assert "🔴" in card and "не совпадают с официальными" in card
    assert OFFICIAL_IBAN not in card


@pytest.mark.parametrize("text", [
    "Продам ответы на рубежку по алгоритмам, 100% совпадение, 5000 тг на карту",
    "Закрою FX по матану, договорюсь с преподавателем, пишите в лс",
    "Диплом под ключ за неделю, антиплагиат 90%",
])
def test_student_scams_are_red(text):
    h = Harness()
    h.message(f"/razvod {text}")
    assert "🔴" in h.last_text()


def test_pravda_university_rumor_without_source_is_unknown_and_points_to_calendar():
    h = Harness()
    h.message("/pravda Говорят, стипендию в этом семестре отменили для всех")
    card = h.last_text()
    assert "⚪" in card and ("календар" in card or "Офис-регистратор" in card)


# ------------------------------------------------------------------ кризис


def test_crisis_message_gets_support_not_verdict():
    h = Harness()
    h.message("Мне очень плохо, не хочу жить")
    text = h.last_text()
    assert "150" in text and "244-51-23" in text and "Что проверить" not in text


def test_crisis_inside_check_mode_returns_support_card():
    h = Harness()
    h.message("/pravda У меня выгорание, я больше не справляюсь с учёбой")
    card = h.last_text()
    assert "Поддержка" in card and "Уверенность" not in card and "🔴" not in card


def test_crisis_does_not_trigger_on_forwarded_news_about_others():
    from backend.app.university.crisis import detect

    assert detect("В МУИТ прошёл тренинг о выгорании для сотрудников") == ""
    assert detect("я не хочу жить так") == "acute"


# ------------------------------------------------------------------ объявления и договорённости с группой


def test_announcement_check_finds_missing_details():
    h = Harness()
    h.message("/obyavlenie Завтра пары не будет, сдаём лабу как обычно")
    card = h.last_text()
    assert "Не хватает" in card and "[аудитория" in card and "Аккуратная версия" in card


def test_group_agreement_each_student_confirms_via_api(client, with_token):
    teacher, s1, s2, s3 = tma_header(9001, "Асель"), tma_header(9002, "Тимур"), tma_header(9003, "Дана"), tma_header(9004, "Ерлан")
    created = client.post("/api/agreements", json={"text": "Курсовая по БД: PDF до 20 ноября", "group": True}, headers=teacher).json()
    code = created["code"]
    assert created["multi"] and created["participants"] == []
    assert client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=teacher).status_code == 409
    assert client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=s1).json()["status"] == "confirmed"
    data = client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=s2).json()
    assert [p["name"] for p in data["participants"] if p["accepted"]] == ["Тимур", "Дана"]
    assert client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=s1).status_code == 409  # дважды нельзя
    declined = client.post(f"/api/agreements/{code}/action", json={"action": "decline"}, headers=s3).json()
    assert any(not p["accepted"] for p in declined["participants"])
    assert len(client.get("/api/agreements", headers=s2).json()) == 1  # видна участнику
    assert client.post(f"/api/agreements/{code}/action", json={"action": "done"}, headers=s3).status_code == 409  # отказавшийся — не участник
    assert client.post(f"/api/agreements/{code}/action", json={"action": "done"}, headers=s1).json()["status"] == "done"


def test_group_agreement_in_chat_shows_confirmations():
    h = Harness()
    h.message("/dogovor_gruppa Лабораторная 3: отчёт в PDF до 15 ноября", user_id=80, chat_id=-300)
    code = next(d for d in h.last_markup_data() if d.startswith("agr:ok:")).split(":")[2]
    h.click(f"agr:ok:{code}", user_id=81, name="Тимур", chat_id=-300)
    h.click(f"agr:ok:{code}", user_id=82, name="Дана", chat_id=-300)
    assert "Подтвердили (2)" in h.last_text() and "Тимур" in h.last_text()


# ------------------------------------------------------------------ новости


def _transport(fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        url = str(request.url)
        if url.startswith("https://t.me/s/"):
            return httpx.Response(200, text=(FIXTURES / "iitu_channel.html").read_text(encoding="utf-8"))
        if url.rstrip("/").endswith("/ru/news"):
            return httpx.Response(200, text=(FIXTURES / "iitu_news_list.html").read_text(encoding="utf-8"))
        return httpx.Response(200, text=(FIXTURES / "iitu_news_article.html").read_text(encoding="utf-8"))
    return httpx.MockTransport(handler)


def test_news_refresh_caches_and_survives_source_outage(client, notifier):
    from backend.app.university import news

    async def run(fail):
        async with httpx.AsyncClient(transport=_transport(fail)) as http:
            return await news.refresh(notifier, client=http)

    first = asyncio.run(run(False))
    assert first["fetched"] == 7 and first["new"] == 7 and not first["errors"]
    feed = client.get("/api/news").json()
    assert len(feed["items"]) == 7 and not feed["stale"]
    item = next(i for i in feed["items"] if i["source"] == "site")
    assert item["url"].startswith("https://iitu.edu.kz/ru/news/") and item["published"] and item["summary"]
    assert item["summary_by"] == "excerpt"  # демо-режим без ИИ честно помечает выдержку
    assert notifier.sent == []  # первая загрузка не рассылается

    outage = asyncio.run(run(True))
    assert outage["errors"]
    feed = client.get("/api/news").json()
    assert len(feed["items"]) == 7 and feed["stale"] and feed["updated_at"]  # кэш на месте, с пометкой
    text = news.render_news_html()
    assert "недоступен" in text and "Обновлено" in text


def test_news_subscribers_get_only_new_items(notifier):
    from backend.app.university import news

    async def run():
        async with httpx.AsyncClient(transport=_transport()) as http:
            return await news.refresh(notifier, client=http)

    asyncio.run(run())
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, 300, "Подписчик", bot_started=True)
        user.news_subscribed = True
        session.commit()
        from backend.app.db.models import NewsItem
        session.query(NewsItem).filter(NewsItem.source == "telegram").delete()  # «новые» посты канала
        session.commit()
    result = asyncio.run(run())
    assert result["new"] == 3 and 1 <= len([s for s in notifier.sent if s[0] == 300]) <= 3


def test_rumor_check_can_cite_university_news():
    from backend.app.rag.store import search_all
    from backend.app.university import news

    async def run():
        async with httpx.AsyncClient(transport=_transport()) as http:
            return await news.refresh(None, client=http)

    asyncio.run(run())
    found = search_all("В МУИТ откроют лабораторию Unitree Robotics?", "pravda")
    assert any(r.source.id.startswith("news_") and "Unitree" in r.source.title for r in found)


def test_news_command_without_cache_does_not_invent_news():
    h = Harness()
    h.message("/news")
    assert "Пока не удалось загрузить" in h.last_text() and "iitu.edu.kz" in h.last_text()


# ------------------------------------------------------------------ календарь, навигатор, курс


def test_master_track_calendar_and_navigator():
    from backend.app.university import calendar
    from backend.app.university.navigator import course_block

    events = calendar.events_for("m2w25", date(2026, 12, 15), 7)
    assert any(e.title == "Защита магистерских диссертаций" and e.dates_label() == "21–31 декабря" for e in events)
    assert not any("m2w25" in e.courses and e.kind != "holiday" for e in calendar.events_for("1", date(2026, 12, 15), 7))
    typo = next(e for e in calendar.calendars()[-1].events if e.id == "m2_research_practice1")
    assert typo.note and not typo.remind  # опечатка в PDF помечена, напоминаний нет
    assert course_block("m1w26", date(2026, 10, 7))["title"] == "Магистратура"


def test_calendar_dates_come_from_data_file():
    from backend.app.university import calendar

    events = calendar.events_for("1", date(2026, 10, 15), 7)
    assert any(e.id == "midterm1_fall" and e.dates_label() == "19–24 октября" for e in events)
    assert all("4" not in e.courses or "1" in e.courses for e in events)
    assert calendar.calendar_for(date(2030, 1, 1)) is None  # нет файла — нет выдуманных дат


def test_calendar_reminders_sent_once(notifier):
    from backend.app.core.scheduler import send_calendar_reminders

    _student(400, "2")
    _student(401, "4")
    sent = asyncio.run(send_calendar_reminders(notifier, date(2026, 10, 17)))  # за 2 дня до рубежного контроля
    # 2 курс: рубежный контроль; 4 курс: рубежный контроль и утверждение тем дипломов (тоже с 19.10)
    assert sent == 3 and {c for c, _ in notifier.sent} == {400, 401}
    assert "Рубежный контроль 1" in notifier.sent[0][1]
    assert asyncio.run(send_calendar_reminders(notifier, date(2026, 10, 17))) == 0


def test_course_up_question_once_per_year(notifier):
    from backend.app.core.scheduler import send_course_up

    _student(410, "1", year=2025)
    _student(411, "1", year=2026)  # уже указал курс в этом году
    assert asyncio.run(send_course_up(notifier, date(2026, 9, 1))) == 1
    assert "2 курсе" in notifier.sent[0][1]
    assert asyncio.run(send_course_up(notifier, date(2026, 9, 2))) == 0


def test_navigator_command_and_status_marks():
    _student(420, "3")
    h = Harness()
    h.message("/navigator", user_id=420)
    text = h.last_text()
    assert "3 КУРС" in text and "уточнено по официальному источнику" in text
    h.click("nav:2", user_id=420)
    assert "Военная кафедра" in h.last_text() and "не подтверждено" not in h.last_text()  # пункт сверен


def test_services_and_calendar_commands():
    h = Harness()
    h.message("/services")
    assert "Офис-регистратор" in h.last_text() and "trust@iitu.edu.kz" in h.last_text()
    h.click("svc:psych")
    assert "help@iitu.edu.kz" in h.last_text() and "источник" in h.last_text()
    h.message("/kalendar")
    assert "iitu.edu.kz" in h.last_text()
    h.message("/adal")
    assert "Кодекс академической честности" in h.last_text()


# ------------------------------------------------------------------ API Mini App


def test_profile_api_validates_and_returns_changes(client):
    ref = client.get("/api/iitu/reference").json()
    assert {f["id"] for f in ref["faculties"]} == {"fctc", "fbmu"} and len(ref["courses"]) == 10
    assert client.patch("/api/me/profile", json={"role": "student", "course": "9"}).status_code == 422
    assert client.patch("/api/me/profile", json={"role": "admin"}).status_code == 422
    saved = client.patch("/api/me/profile", json={"role": "student", "course": "2", "faculty": "fbmu", "program": "6B04105", "ui_lang": "en"}).json()
    assert saved["onboarded"] and saved["ui_lang"] == "ru" and saved["changes"]  # английский ещё не готов
    me = client.get("/api/me").json()
    assert me["profile_label"] == "Студент · 2 курс · ФБМУ · Финансовые технологии"


def test_home_navigator_services_about_api(client):
    client.patch("/api/me/profile", json={"role": "student", "course": "4"})
    home = client.get("/api/home").json()
    assert "week" in home and "news" in home and home["calendar"]["year"] == "2026-2027"
    nav = client.get("/api/navigator?course=4").json()
    assert nav["title"] == "4 курс" and any(i["status"] == "confirmed" for i in nav["items"])
    assert client.get("/api/navigator?course=7").status_code == 404
    services = client.get("/api/services").json()["services"]
    assert any(s["id"] == "trust" for s in services)
    about = client.get("/api/about").json()
    assert about["official_sources"] >= 20 and about["modes"] == 8
    cal = client.get("/api/calendar?course=1&days=366").json()
    assert cal["loaded"] in (True, False)
    assert client.get("/api/news?source=vk").status_code == 400


def test_i18n_has_kazakh_and_english_stubs(client):
    assert client.get("/api/i18n/kk").status_code == 200
    assert client.get("/api/i18n/en").json()["ui.nav.home"] == "Главная"  # нет перевода — русский


# ------------------------------------------------------------------ «Группы и потоки» (университетская «Семья»)


def test_groups_in_bot_create_invite_join_and_post(notifier):
    from backend.app.services import circles

    h = Harness()
    h.message("/gruppa", user_id=800, name="Асель")
    assert "ГРУППЫ И ПОТОКИ" in h.last_text()
    h.click("grp:new", user_id=800, name="Асель")
    h.click("grp:kind:teacher", user_id=800, name="Асель")
    h.message("Базы данных — CS-2401", user_id=800, name="Асель")
    assert "создана" in h.last_text() and "start=grp_" in h.last_text()
    token = h.last_text().split("start=grp_")[1].split()[0]
    h.message(f"/start grp_{token}", user_id=801, name="Тимур")
    assert "Вступить?" in h.last_text()
    h.click(f"grp:join:{token}", user_id=801, name="Тимур")
    with get_sessionmaker()() as session:
        repo.upsert_user(session, 801, "Тимур", bot_started=True)
    circle_id = circles.list_for_user(800)[0]["id"]
    assert circles.info(circle_id, 801)["members_count"] == 2
    # объявление с предпросмотром → отправка
    h.click(f"grp:post:{circle_id}", user_id=800, name="Асель")
    h.message("15 октября в 10:00, ауд. 305 — рубежный контроль", user_id=800, name="Асель")
    assert "Отправить" in h.last_text()
    h.click("grp:send", user_id=800, name="Асель")
    assert any(c == 801 and "рубежный контроль" in text for c, text in notifier.sent)
    # участник не может рассылать
    h.click(f"grp:post:{circle_id}", user_id=801, name="Тимур")
    assert not any("Пришли текст объявления" in x for x in h.session.texts()[-1:])


def test_group_chat_join_button():
    h = Harness()
    h.message("/gruppa", user_id=810, chat_id=-555, name="Староста")
    assert "Вступить" in str(h.last_markup_data()) or any(d.startswith("grp:join:") for d in h.last_markup_data())
    join = next(d for d in h.last_markup_data() if d.startswith("grp:join:"))
    h.click(join, user_id=811, chat_id=-555, name="Дана")
    from backend.app.services import circles

    assert circles.for_chat(-555)["members_count"] == 2


def test_red_check_alerts_group_anonymously(notifier):
    from backend.app.services import circles

    with get_sessionmaker()() as session:
        for uid, name in ((820, "Аружан"), (821, "Ерлан"), (822, "Мадина")):
            repo.upsert_user(session, uid, name, bot_started=True)
    data = circles.create(820, "Аружан", "CS-2402")
    token = circles.invite_token(data["id"])
    circles.join(821, "Ерлан", token)
    circles.join(822, "Мадина", token)
    circles.update_settings(data["id"], 822, alerts=False)
    h = Harness()
    h.message("/razvod Продам ответы на рубежку, 100% совпадение, скидывай 5000 на карту", user_id=820, name="Аружан")
    alerts = [(c, text) for c, text in notifier.sent if "Группа «CS-2402»" in text]
    assert [c for c, _ in alerts] == [821]  # Мадина выключила предупреждения, автору не шлём
    assert "Аружан" not in alerts[0][1] and "Продам ответы" not in alerts[0][1]  # анонимно, без текста


def test_groups_api_roles_rights_and_agreement(client, with_token, notifier):
    owner, s1, s2 = tma_header(830, "Асель"), tma_header(831, "Тимур"), tma_header(832, "Дана")
    created = client.post("/api/circles", json={"title": "ФКТК поток 1", "kind": "stream", "course": "1"}, headers=owner).json()
    cid = created["id"]
    token = client.get(f"/api/circles/{cid}", headers=owner).json()["invite_token"]
    assert client.get(f"/api/circles/{cid}", headers=s1).status_code == 403  # чужим не видно
    assert client.get(f"/api/circles/invite/{token}", headers=s1).json()["title"] == "ФКТК поток 1"
    client.post("/api/circles/join", json={"token": token}, headers=s1)
    client.post("/api/circles/join", json={"token": token}, headers=s2)
    info = client.get(f"/api/circles/{cid}", headers=s1).json()
    assert info["members_count"] == 3 and info["my_role"] == "member" and info["upcoming"]
    assert client.post(f"/api/circles/{cid}/posts", json={"text": "Привет всем"}, headers=s1).status_code == 409  # не админ
    assert client.post(f"/api/circles/{cid}/members/831", json={"role": "admin"}, headers=s2).status_code == 409  # не владелец
    client.post(f"/api/circles/{cid}/members/831", json={"role": "admin"}, headers=owner)
    assert client.post(f"/api/circles/{cid}/posts", json={"text": "Рубежка 19 октября"}, headers=s1).status_code == 200
    agr = client.post(f"/api/circles/{cid}/agreements", json={"text": "Отчёт по практике в PDF до 20 ноября"}, headers=owner).json()
    assert agr["multi"] and agr["circle_id"] == cid
    assert client.get(f"/api/agreements/{agr['code']}", headers=s2).status_code == 200  # участник группы видит
    assert client.post(f"/api/agreements/{agr['code']}/action", json={"action": "confirm"}, headers=s2).json()["status"] == "confirmed"
    # владелец уходит — владение переходит админу
    client.post(f"/api/circles/{cid}/leave", headers=owner)
    assert client.get(f"/api/circles/{cid}", headers=s1).json()["my_role"] == "owner"
    for _ in range(5):
        client.post(f"/api/circles/{cid}/posts", json={"text": "ещё объявление"}, headers=s1)
    assert client.post(f"/api/circles/{cid}/posts", json={"text": "спам"}, headers=s1).status_code == 409  # лимит в сутки


def test_delete_my_data_leaves_groups(client):
    from backend.app.services import circles

    data = circles.create(1, "Разработчик", "Моя группа")
    circles.join(840, "Друг", circles.invite_token(data["id"]))
    assert client.get("/api/me/export").json()["groups"][0]["title"] == "Моя группа"
    client.delete("/api/me")
    assert circles.info(data["id"], 840)["my_role"] == "owner" and circles.info(data["id"], 840)["members_count"] == 1

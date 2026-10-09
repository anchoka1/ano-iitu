"""Новые функции: планер, волны 1–3, учебные хабы, модерация, флаги, приватность и анонимность."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from tests.conftest import reset_caches

TODAY = date(2026, 10, 8)  # четверг


def _user(uid: int, name: str = "Тест", role: str = "student", course: str = "2", started: bool = True):
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, uid, name, bot_started=started)
        repo.set_profile(session, user, role=role, course=course if role == "student" else "")
        return user


def _owner(monkeypatch, *ids: int):
    monkeypatch.setenv("OWNER_IDS", ",".join(str(i) for i in ids))
    from backend.app.core.config import get_settings

    get_settings.cache_clear()


# ------------------------------------------------------------------ флаги и тексты


def test_feature_flag_hides_api(client, monkeypatch):
    assert client.get("/api/features").json()["flags"]["gpa"] is True
    monkeypatch.setenv("FEATURE_GPA", "false")
    from backend.app.core.features import get_flags

    get_flags.cache_clear()
    assert client.get("/api/features").json()["flags"]["gpa"] is False
    assert client.post("/api/gpa/final", json={"admission": 70, "target": 75}).status_code == 404
    monkeypatch.setenv("FEATURE_PLANNER", "false")
    get_flags.cache_clear()
    assert client.get("/api/plan").status_code == 404


def test_no_official_status_phrases_in_texts():
    from backend.app.i18n import load_strings

    bad = re.compile(r"официальн\w* (бот|сервис)|согласован|при поддержке университета|по поручению", re.IGNORECASE)
    strings = load_strings("ru")
    assert not [k for k, v in strings.items() if bad.search(v)]
    assert "бот для студентов и преподавателей МУИТ" in strings["bot.official_answer"]


# ------------------------------------------------------------------ планер


def test_parse_task_text():
    from backend.app.services.planner import parse_task_text

    d = parse_task_text("сдать лабу по Python в пятницу до 18:00", TODAY)
    assert d["due_date"] == "2026-10-09" and d["due_time"] == "18:00" and d["subject"] == "Python"
    assert d["title"].lower().startswith("сдать лабу")
    d = parse_task_text("срочно эссе до 15 октября", TODAY)
    assert d["due_date"] == "2026-10-15" and d["priority"] == 2
    assert parse_task_text("купить тетради", TODAY)["due_date"] == ""


def test_planner_crud_views_and_privacy():
    from backend.app.services import planner

    _user(10)
    _user(11)
    t1 = planner.create_task(10, "Лаба 1", due_date=TODAY.isoformat(), due_time="18:00", subject="Python")
    t2 = planner.create_task(10, "Эссе", due_date=(TODAY + timedelta(days=3)).isoformat(), priority=2)
    planner.create_task(10, "Без срока")
    today = planner.view(10, "today", TODAY)
    assert [x["title"] for x in today["today"]] == ["Лаба 1"]
    week = planner.view(10, "week", TODAY)
    assert any(x["title"] == "Эссе" for d in week["days"] for x in d["tasks"]) and week["nodate"][0]["title"] == "Без срока"
    assert planner.view(10, "board", TODAY)["columns"]["todo"]
    matrix = planner.view(10, "matrix", TODAY)["quadrants"]
    assert any(x["title"] == "Лаба 1" for x in matrix["q3"] + matrix["q1"])
    assert planner.view(10, "semester", TODAY)["view"] == "semester"
    # изменить, перенести, выполнить, удалить
    planner.update_task(10, t1["id"], title="Лаба 1 (Python)")
    moved = planner.move_task(10, t1["id"], days=1)
    assert moved["due_date"] > TODAY.isoformat() and moved["moved_count"] == 1
    assert planner.complete_task(10, t2["id"])["status"] == "done"
    # личные задачи одного пользователя не видны другому
    assert planner.view(11, "board", TODAY)["columns"]["todo"] == []
    with pytest.raises(planner.PlannerError):
        planner.get_task(11, t1["id"])
    planner.delete_task(10, t1["id"])
    with pytest.raises(planner.PlannerError):
        planner.get_task(10, t1["id"])


def test_repeat_steps_ics_and_delete_all():
    from backend.app.services import planner

    _user(10)
    t = planner.create_task(10, "Курсовая работа", due_date=(TODAY + timedelta(days=20)).isoformat(), repeat="weekly")
    planner.complete_task(10, t["id"])
    week = planner.view(10, "week", TODAY + timedelta(days=21))
    assert any(x["title"] == "Курсовая работа" and x["status"] != "done" for d in week["days"] for x in d["tasks"])
    steps = planner.steps_preview_rules("Курсовая работа", t["due_date"], TODAY)
    assert len(steps) >= 4 and all(s["due_date"] <= t["due_date"] for s in steps)
    planner.add_steps(10, t["id"], steps)
    assert len(planner.get_task(10, t["id"])["subtasks"]) == len(steps)
    ics = planner.export_ics(10)
    assert ics.startswith("BEGIN:VCALENDAR") and "Курсовая работа" in ics and ics.strip().endswith("END:VCALENDAR")
    planner.delete_all(10)
    assert planner.view(10, "board")["columns"]["todo"] == []


def test_suggestion_from_agreement_needs_confirmation():
    from backend.app.services import agreements, planner

    _user(21, "Аскар")
    _user(22, "Марат")
    agreement = asyncio.run(agreements.create_agreement("Аскар отдаёт Марату 20 000 ₸ до 15 октября 18:00", 21, "Аскар"))
    assert agreement.deadline_time == "18:00"
    assert planner.view(21, "board")["columns"]["todo"] == []  # молча ничего не добавлено
    s = planner.list_suggestions(21)
    assert s and s[0]["source"] == "agreement"
    agreements.respond(agreement.code, 22, "Марат", "", accept=True)
    sug22 = planner.list_suggestions(22)
    task = planner.accept_suggestion(22, sug22[0]["id"])
    assert task["due_date"] == agreement.deadline_iso and task["source"] == "agreement"
    assert planner.list_suggestions(22) == []


def test_reminders_respect_subscription_and_quiet_hours():
    from backend.app.services import planner, prefs

    _user(10)
    zone = ZoneInfo("Asia/Almaty")
    planner.create_task(10, "Сдать отчёт", due_date="2026-10-08", due_time="15:00")
    now = datetime(2026, 10, 8, 14, 30, tzinfo=zone)
    assert planner.due_reminders(now) == []  # напоминания по умолчанию выключены
    prefs.set_value(10, "plan.remind", "1")
    prefs.set_value(10, "plan.quiet", "14-16")
    assert planner.due_reminders(now) == []  # тихие часы
    prefs.set_value(10, "plan.quiet", "23-8")
    due = planner.due_reminders(now)
    assert len(due) == 1 and due[0][0] == 10 and "Сдать отчёт" in due[0][1]
    assert planner.due_reminders(now) == []  # повторно не напоминает


def test_promise_witness_flow(notifier):
    from backend.app.services import planner

    _user(10, "Айгерим")
    _user(30, "Друг")
    t = planner.create_task(10, "Пробежка", due_date="2026-10-07")
    token = planner.make_promise(10, t["id"], witness=True)["witness_token"]
    with pytest.raises(planner.PlannerError):
        planner.witness_answer(token, 10, "Айгерим", True)  # сам себе не свидетель
    assert planner.witness_answer(token, 30, "Друг", True)["state"] == "accepted"
    assert planner.overdue_promises(date(2026, 10, 8)) == 1
    asyncio.run(planner.flush_witness_queue())
    assert notifier.sent and notifier.sent[-1][0] == 30 and "Пробежка" in notifier.sent[-1][1]


def test_team_board_from_agreement():
    from backend.app.services import agreements, planner

    _user(21, "Аскар")
    _user(22, "Марат")
    a = asyncio.run(agreements.create_agreement("Делаем проект: Аскар — бэкенд, Марат — фронт до 20 октября", 21, "Аскар"))
    agreements.respond(a.code, 22, "Марат", "", accept=True)
    board = planner.create_board(22, "Марат", agreement_code=a.code)
    assert {m["user_id"] for m in board["members"]} == {21, 22}
    asyncio.run(planner.add_board_task(22, board["id"], "API", assignee_id=21))
    view = planner.board_view(21, board["id"])
    assert view["columns"]["todo"][0]["assignee_name"] == "Аскар"
    _user(99)
    with pytest.raises(planner.PlannerError):
        planner.board_view(99, board["id"])


def test_plan_api_flow(client):
    draft = client.post("/api/plan/parse", json={"text": "сдать лабу по Python в пятницу до 18:00"}).json()
    assert draft["due_time"] == "18:00"
    task = client.post("/api/plan/tasks", json={"title": draft["title"], "due_date": draft["due_date"], "due_time": draft["due_time"]}).json()
    for view in ("today", "week", "semester", "board", "matrix"):
        assert client.get(f"/api/plan?view={view}").status_code == 200, view
    assert client.patch(f"/api/plan/tasks/{task['id']}", json={"status": "doing"}).json()["status"] == "doing"
    assert client.post(f"/api/plan/tasks/{task['id']}/move", json={"days": 7}).status_code == 200
    assert client.post(f"/api/plan/tasks/{task['id']}/steps/preview").json()["steps"]
    assert client.get("/api/plan/export.ics").text.startswith("BEGIN:VCALENDAR")
    assert client.get("/api/plan/week-verdict").json()["title"].startswith("Итоги недели")
    assert client.get("/api/plan/week-verdict.png").content[:4] == b"\x89PNG"
    assert client.patch("/api/plan/settings", json={"remind": True, "remind_offset": 1440, "quiet": "22-7"}).json()["quiet"] == "22-7"
    assert client.get("/api/home/extra").status_code == 200
    assert client.delete("/api/plan").json() == {"deleted": True}


# ------------------------------------------------------------------ волна 1


def test_gpa_final_needed():
    from backend.app.services import campus

    r = campus.final_needed(70, 75)
    assert r["need"] == 82.5 and r["reachable"] and r["final_weight"] == 40
    assert campus.final_needed(30, 90)["reachable"] is False
    assert campus.final_needed(100, 50)["already"] is True
    # Шкала и формула — из QM-02 (табл. 1) и R-11 (п. 4.8–4.9)
    r = campus.final_needed(0, 80, r1=70, r2=74)
    assert r["admission"] == 72 and r["target_letter"]["letter"] == "B" and r["admitted"]
    needs = {x["letter"]: x["need"] for x in r["letters"]}
    assert "A" not in needs and needs["B"] == 92.0 and needs["D"] == 17.0  # A с допуском 72% недостижима
    assert campus.final_needed(0, 50, r1=40, r2=50)["admitted"] is False  # средний РК < 50% — нет допуска
    assert campus.letter_for(94.6)["letter"] == "A" and campus.letter_for(49)["letter"] == "FX"
    assert campus.gpa([{"points": 4, "credits": 5}, {"points": 3, "credits": 5}])["gpa"] == 3.5
    assert campus.gpa([{"letter": "A-", "credits": 5}, {"letter": "B+", "credits": 5}, {"letter": "P", "credits": 3}])["gpa"] == 3.5
    with pytest.raises(campus.CampusError):
        campus.gpa([{"letter": "Z", "credits": 5}])


def test_countdown_and_syllabus_rules():
    from backend.app.services import campus

    cd = campus.countdown("2", TODAY)
    assert {c["kind"] for c in cd} <= {"midterm", "session", "holidays"} and cd
    text = ("Силлабус. Дисциплина: Базы данных\nЛабораторная 1 — сдать до 20 октября (10%)\nРубежный контроль 1 — 7 неделя (20%)\n"
            "Итоговый экзамен — 40%\nПересдача только в период FX\nПропуск более 20% занятий — недопуск\nИспользование ChatGPT для кода запрещено")
    assert campus.looks_like_syllabus(text)
    card = campus.syllabus_rules(text, TODAY)
    assert any(d.date == "2026-10-20" and d.weight == "10%" for d in card.deadlines)
    assert any("7 неделя" in d.when_text for d in card.deadlines)
    assert card.retake_rules and card.absence_rules and card.ai_policy
    _user(10)
    assert campus.syllabus_to_plan(10, card) == 1


def test_class_poll_is_anonymous_and_hidden_until_five(client):
    from sqlalchemy import select

    from backend.app.db.models_campus import PollAnswer
    from backend.app.services import polls

    _user(50, "Преподаватель", role="teacher")
    poll = polls.create("class", "Что было непонятно?", owner_id=50)
    for uid in range(100, 104):
        _user(uid)
        polls.answer(poll["id"], uid, text="непонятны графы и деревья")
    with pytest.raises(polls.PollError):
        polls.answer(poll["id"], 100, text="ещё раз")  # дважды нельзя
    res = polls.results(poll["id"], 50)
    assert res["answers"] == 4 and "results" not in res  # меньше пяти — скрыто
    polls.answer(poll["id"], 104, text="графы")
    res = polls.results(poll["id"], 50)
    assert res["visible"] and any(t["word"] == "графы" for t in res["results"]["topics"])
    with pytest.raises(polls.PollError):
        polls.results(poll["id"], 100)  # итоги видит только автор
    with get_sessionmaker()() as session:
        rows = list(session.scalars(select(PollAnswer)))
    assert all(not str(r.voter_hash).isdigit() and "100" not in r.voter_hash[:3] for r in rows)
    assert all(r.day and len(r.day) == 10 for r in rows)  # только дата, без времени


def test_pulse_weekly_question():
    from backend.app.services import polls

    p = polls.current_pulse(TODAY, 10)
    assert p["kind"] == "pulse" and len(p["options"]) == 4
    assert polls.current_pulse(TODAY, 10)["id"] == p["id"]


def test_agreement_hour_and_participant_reminders(notifier):
    from backend.app.core.scheduler import send_reminders
    from backend.app.core.scheduler_campus import agreement_hour_reminders
    from backend.app.services import agreements

    _user(21, "Аскар")
    _user(22, "Марат")
    a = asyncio.run(agreements.create_agreement("Сдать отчёт до 9 октября 18:00", 21, "Аскар", chat_id=-300, thread_id=7))
    agreements.respond(a.code, 22, "Марат", "", accept=True)
    sent = asyncio.run(send_reminders(notifier, date(2026, 10, 8)))  # за день: в группу и каждому участнику
    targets = {c for c, _ in notifier.sent}
    assert sent >= 3 and {-300, 21, 22} <= targets
    notifier.sent.clear()
    now = datetime(2026, 10, 9, 17, 10, tzinfo=ZoneInfo("Asia/Almaty"))
    assert asyncio.run(agreement_hour_reminders(notifier, now)) == 3
    assert asyncio.run(agreement_hour_reminders(notifier, now)) == 0  # один раз


# ------------------------------------------------------------------ сообщество и модерация


def test_moderation_and_report_reach_owner(monkeypatch, notifier):
    from backend.app.services import community

    _owner(monkeypatch, 1000)
    _user(1000, "Владелец")
    _user(10)
    q = community.create_post("senior_q", 10, "Айгерим", "Как пережить первую сессию?")
    assert q["pending"]
    assert community.list_posts("senior_q", 11) == []  # чужим не видно до модерации
    assert community.list_posts("senior_q", 10)[0]["id"] == q["id"]  # автор видит своё
    asyncio.run(community.announce_pending(q["id"]))
    assert notifier.sent[-1][0] == 1000 and "mq:a:post:" in json.dumps(notifier.buttons[-1])
    with pytest.raises(community.CommunityError):
        asyncio.run(community.moderate(q["id"], 10, "ok"))  # не владелец
    assert asyncio.run(community.moderate(q["id"], 1000, "ok")) == "approved"
    a = community.create_post("senior_a", 12, "Старшекурсник", "", "Начни готовиться за две недели", parent_id=q["id"])
    assert a["status"] == "approved"
    assert community.vote(a["id"], 13)["score"] == 1
    notifier.sent.clear()
    asyncio.run(community.report(a["id"], 13, "грубость"))
    assert notifier.sent and notifier.sent[0][0] == 1000 and "Жалоба" in notifier.sent[0][1]
    assert "mq:h:report:" in json.dumps(notifier.buttons[-1])


def test_reviews_about_people_and_honesty_rejected():
    from backend.app.services import community

    _user(10)
    with pytest.raises(community.CommunityError):
        community.create_post("review", 10, "", "Базы данных", "Преподаватель плохо объясняет", {"subject": "БД", "load": 3, "difficulty": 4})
    with pytest.raises(community.CommunityError):
        community.create_post("resource", 10, "", "Готовые ответы на экзамен по физике", "", {"url": "https://example.com"})
    ok = community.create_post("review", 10, "", "Базы данных", "Много практики, начинай лабы заранее", {"subject": "БД", "load": 4, "difficulty": 3})
    assert ok["pending"] and ok["author_name"] == ""


def test_anonymous_posts_store_no_author_id():
    from backend.app.db.models_campus import Post
    from backend.app.services import community

    _user(10)
    p = community.create_post("radar", 10, "Айгерим", "Продают «ответы» на рубежку", "Пишут в личку, просят предоплату на карту")
    with get_sessionmaker()() as session:
        row = session.get(Post, p["id"])
        assert row.author_id is None and row.author_name == "" and row.author_hash


def test_slots_booking_and_event_going():
    from backend.app.services import community, planner

    _user(50, "Преподаватель", role="teacher")
    _user(10, "Студент")
    slots = community.create_slots(50, "Преподаватель", ["2099-10-10T14:00", "2099-10-10T14:15"], 15, "каб. 301")
    booked = asyncio.run(community.book_slot(slots[0]["id"], 10, "Студент"))
    assert booked["mine"]
    with pytest.raises(community.CommunityError):
        asyncio.run(community.book_slot(slots[0]["id"], 11, "Другой"))
    assert any(s["source"] == "consult" for s in planner.list_suggestions(10))
    with pytest.raises(community.CommunityError):
        community.create_slots(10, "Студент", ["2099-10-10T14:00"])  # слоты — только преподавателю


# ------------------------------------------------------------------ хабы


def test_hubs_catalog_and_teacher_verification(monkeypatch, notifier):
    from backend.app.services import community, hubs

    _owner(monkeypatch, 1000)
    hubs.seed_hubs()
    _user(50, "Преподаватель", role="teacher")
    cat = hubs.catalog(50)
    assert {h["slug"] for h in cat["cross"]} >= {"first-year", "practice", "dorm", "coursera", "cisco-netacad", "fortinet", "student-life"}
    assert cat["teachers"] == []  # «Преподавательская» — только подтверждённым
    hub = hubs.create_hub_direct(1000, "Базы данных", "2")
    with pytest.raises(hubs.HubError):
        hubs.attach_teacher(hub["id"], 50)  # самозаявленной роли недостаточно
    with pytest.raises(community.CommunityError):
        community.create_post("announce", 50, "Преподаватель", "Лабораторная перенесена", hub_id=hub["id"])
    app = asyncio.run(hubs.apply("teacher", 50, "Преподаватель", {"about": "кафедра ИС", "hub_ids": [hub["id"]]}))
    assert "mq:a:app:" in json.dumps(notifier.buttons[-1])
    with pytest.raises(hubs.HubError):
        asyncio.run(hubs.decide(app["id"], 50, True))  # решает только владелец
    asyncio.run(hubs.decide(app["id"], 1000, True))
    assert hubs.catalog(50)["teachers"]
    post = community.create_post("announce", 50, "Преподаватель", "Лабораторная перенесена", hub_id=hub["id"])
    assert post["status"] == "approved" and post["is_teacher"]


def test_course_plan_load_map_and_suggestions(monkeypatch):
    from backend.app.services import hubs, planner

    _owner(monkeypatch, 1000)
    _user(1000)
    _user(10)
    with get_sessionmaker()() as session:
        user = repo.upsert_user(session, 50, "Преподаватель")
        user.teacher_verified = True
        session.commit()
    db = hubs.create_hub_direct(1000, "Базы данных", "2")
    oop = hubs.create_hub_direct(1000, "ООП", "2")
    for h in (db, oop):
        hubs.join(h["id"], 10, "Студент")
        hubs.attach_teacher(h["id"], 50)
    asyncio.run(hubs.publish_deadline(oop["id"], 50, "Преподаватель", "Проект ООП", "2026-10-21"))
    m = hubs.load_map(db["id"], "2026-10-22", 50)
    assert m["count"] == 1 and m["items"][0]["hub"] == "ООП"
    r = asyncio.run(hubs.publish_deadline(db["id"], 50, "Преподаватель", "Лаба БД", "2026-10-22"))
    assert r["suggested"] == 1
    assert {s["title"] for s in planner.list_suggestions(10)} >= {"Проект ООП", "Лаба БД"}
    summary = hubs.subject_summary(db["id"], 50)
    assert "questions" in summary
    with pytest.raises(hubs.HubError):
        hubs.subject_summary(db["id"], 10)  # сводка — только преподавателю хаба


def test_faq_save_confirm_and_repeat(monkeypatch):
    from backend.app.services import hubs

    _owner(monkeypatch, 1000)
    hub = hubs.create_hub_direct(1000, "Математика", "1")
    hubs.link_chat(-500, 12, hub["id"], "Группа · Math", 1000)
    assert hubs.hub_for_chat(-500, 12)["hub_id"] == hub["id"]
    item = hubs.save_answer(-500, 12, 77, "Как пересдать FX по математике?", "FX пересдаётся только финальный экзамен в период сессии", 10, 11, "https://t.me/c/500/12/77")
    assert hubs.find_similar(hub["id"], "как пересдать FX по математике") is None  # ещё не подтверждён ментором
    with pytest.raises(hubs.HubError):
        hubs.confirm_faq(item["id"], 10)
    hubs.confirm_faq(item["id"], 1000)
    found = hubs.find_similar(hub["id"], "подскажите, как пересдать FX по математике")
    assert found and found["link"].endswith("/77")
    assert "Дайджест" in hubs.digest_text(-500, 12)


# ------------------------------------------------------------------ прочее (волны 2–3)


def test_checklist_appeal_capsule_badges_rating():
    from backend.app.services import campus

    _user(10)
    c = campus.checklist(10, "2")
    assert c["total"] > 0 and c["done"] == 0
    c = campus.checklist_mark(10, "2", c["items"][0]["key"], True)
    assert c["done"] == 1 and "сделано 1 из" in c["label"]
    draft = campus.appeal_draft("appeal", {"name": "Айгерим", "subject": "Физика", "reason": "баллы посчитаны неверно"})
    assert "АПЕЛЛЯЦИЮ" in draft["text"] and "Декану" in draft["text"] and draft["crisis"] is None and draft["source_url"].startswith("https://iitu.edu.kz")
    assert campus.appeal_draft("trust", {"what": "не могу больше, хочу умереть"})["crisis"]
    assert campus.capsule_date("1", TODAY) == "2027-01-01"  # письмо приходит после зимней сессии
    assert campus.capsule_date("4", TODAY) == "2027-01-01"  # теперь и для 4 курса — конец семестра
    assert all(not b["earned"] for b in campus.badges(10))
    campus.set_rating_opt_in(-700, "ИС-21", "supergroup", True)
    assert campus.group_rating()[0]["title"] == "ИС-21"


def test_rumor_of_week_needs_several_people():
    from backend.app.services import campus

    _user(10)
    _user(11)
    rumor = "С понедельника штраф за голосовые сообщения в общих чатах WhatsApp"
    from backend.app.core.engine import CheckInput, get_verdict_engine

    asyncio.run(get_verdict_engine().check(CheckInput(mode="pravda", text=rumor, user_id=10)))
    assert campus.pick_rumor(date.today()) is None  # проверил один человек — не публикуем
    asyncio.run(get_verdict_engine().check(CheckInput(mode="pravda", text=rumor, user_id=11)))
    r = campus.rumor_of_week()
    assert r and r["verdict_label"] in ("правда", "неправда", "частично")


def test_delete_my_data_removes_campus_data():
    from backend.app.services import campus, community, planner, prefs

    _user(10)
    planner.create_task(10, "Задача")
    prefs.set_value(10, "sub.morning", "1")
    community.create_post("radar", 10, "", "Схема с картой", "Просят код")
    campus.capsule_create(10, "2", "Письмо себе на четвёртый курс")
    with get_sessionmaker()() as session:
        repo.delete_user_data(session, 10)
    assert planner.view(10, "board")["columns"]["todo"] == []
    assert prefs.get(10, "sub.morning") == ""
    assert community.my_posts(10) == [] and campus.capsule_list(10) == []


def test_migration_from_v2_keeps_data(tmp_path, monkeypatch):
    """База версии 2 (до новых функций) обновляется до 3 без потери данных."""
    import sqlite3

    from sqlalchemy import inspect

    from backend.app.db.base import get_engine
    from backend.app.db.init_db import init_db

    path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    reset_caches()
    init_db()
    with get_sessionmaker()() as session:
        repo.upsert_user(session, 555, "Старый")
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    for table in ("tasks", "hubs", "posts"):
        con.execute(f"DROP TABLE {table}")
    con.commit()
    con.close()
    reset_caches()
    init_db()
    assert {"tasks", "hubs", "posts"} <= set(inspect(get_engine()).get_table_names())
    with get_sessionmaker()() as session:
        assert repo.get_user(session, 555).first_name == "Старый"

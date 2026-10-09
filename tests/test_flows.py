"""Сквозные сценарии приёмки: от кнопки «Отправить» до результата, который видят остальные.

1. Радар: студент отправил → очередь модерации → модератор одобрил → лента Радара → подписчикам пришло
   предупреждение → автор получил ответ и видит статус → «Правда»/«Развод?» учитывают запись.
2. Слух: «Правда» не подтвердила → «Отправить модератору» → вердикт модератора кнопкой в Telegram → карточка
   автора обновилась, ему пришло сообщение → лента «Слухи и факты» → следующий спросивший сразу получает вердикт.
3. Отказ с причиной: автор видит причину в «Моих обращениях».
4. Права проверяет сервер: чужой не видит очередь, не может решить заявку, не получит чужой файл, силлабус или проверку.
5. Файлы: тип по содержимому, согласие, шифрование на диске, удаление вместе с данными.
6. Силлабус: PDF и DOCX → разбор → дедлайны в плане, напоминания включены, привязка к хабу.
7. «Правда» с поиском в интернете (подменённый Tavily): источники со ссылкой и датой.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile

import httpx
import pytest

from backend.app.db import repo
from backend.app.db.base import get_sessionmaker
from tests.conftest import reset_caches, tma_header

STUDENT, OTHER, MOD, SUBSCRIBER, ADMIN = 101, 102, 900, 103, 901


@pytest.fixture
def staff(monkeypatch, with_token):
    monkeypatch.setenv("MODERATOR_IDS", str(MOD))
    monkeypatch.setenv("ADMIN_IDS", str(ADMIN))
    reset_caches()
    with get_sessionmaker()() as session:
        for uid, name in ((STUDENT, "Айгерим"), (OTHER, "Чужой"), (MOD, "Модератор"), (SUBSCRIBER, "Подписчик"), (ADMIN, "Админ")):
            repo.upsert_user(session, uid, name, bot_started=True)
    return with_token


def h(uid: int) -> dict:
    return tma_header(uid, {STUDENT: "Айгерим", OTHER: "Чужой", MOD: "Модератор", SUBSCRIBER: "Подписчик", ADMIN: "Админ"}.get(uid, "Тест"))


def test_radar_end_to_end(client, staff, notifier):
    from backend.app.services import prefs

    prefs.set_value(SUBSCRIBER, "sub.radar", "1")
    text = "В чатах продают «ответы на рубежку по физике» за 5000 тенге переводом на карту Kaspi"
    post = client.post("/api/posts", json={"kind": "radar", "title": "Продают ответы на рубежку по физике", "body": text}, headers=h(STUDENT)).json()
    assert post["status"] == "pending" and post["author_name"] == ""
    # Заявка ушла модератору с кнопками
    assert {MOD, ADMIN} <= {uid for uid, _ in notifier.sent} and "mq:a:post:" in json.dumps(notifier.buttons[-1])
    # Статус у автора
    mine = client.get("/api/me/submissions", headers=h(STUDENT)).json()
    assert mine[0]["status"] == "pending" and mine[0]["status_label"] == "На проверке"
    # Очередь видна только модератору (проверка на сервере)
    assert client.get("/api/mod/queue", headers=h(OTHER)).status_code == 403
    assert client.post("/api/mod/decide", json={"key": f"post:{post['id']}", "action": "approve"}, headers=h(OTHER)).status_code == 403
    queue = client.get("/api/mod/queue", headers=h(MOD)).json()["items"]
    assert [i["key"] for i in queue] == [f"post:{post['id']}"]
    # До одобрения в ленте пусто для остальных
    assert client.get("/api/radar", headers=h(OTHER)).json()["items"] == []
    notifier.sent.clear()
    r = client.post("/api/mod/decide", json={"key": f"post:{post['id']}", "action": "approve"}, headers=h(MOD))
    assert r.json()["status"] == "approved"
    # Повторное решение — понятная ошибка, а не второй раз
    assert client.post("/api/mod/decide", json={"key": f"post:{post['id']}", "action": "reject", "reason": "spam"}, headers=h(MOD)).status_code == 409
    recipients = [uid for uid, _ in notifier.sent]
    assert STUDENT in recipients and SUBSCRIBER in recipients  # автору — решение, подписчику — предупреждение
    assert any("Опубликовано" in txt for uid, txt in notifier.sent if uid == STUDENT)
    # Лента Радара у всех
    feed = client.get("/api/radar", headers=h(OTHER)).json()["items"]
    assert feed and feed[0]["title"].startswith("Продают ответы")
    assert client.get("/api/me/submissions", headers=h(STUDENT)).json()[0]["status_label"] == "Опубликовано"
    # Журнал
    log = client.get("/api/mod/log", headers=h(MOD)).json()
    assert log[0]["action"] == "approve" and log[0]["moderator"] == "Модератор"
    # «Развод?» и «Правда» учитывают одобренную запись Радара
    check = client.post("/api/check", json={"mode": "razvod", "text": "Продам ответы на рубежку по физике, 5000 тенге на карту"}, headers=h(OTHER)).json()
    assert any("Радар" in s["title"] for s in check["card"]["sources"])
    # Главная автора и подписчика показывает свежее предупреждение
    assert client.get("/api/today", headers=h(SUBSCRIBER)).json()["radar"][0]["id"] == post["id"]


def test_rumor_escalation_end_to_end(client, staff, notifier):
    claim = "Говорят, у МУИТ новый ректор с понедельника"
    check = client.post("/api/check", json={"mode": "pravda", "text": claim}, headers=h(STUDENT)).json()
    assert check["truth"]["code"] == "unconfirmed" and check["truth"]["can_escalate"]
    assert "Поиск в интернете не подключён" in " ".join(check["card"]["notes"])
    # Чужую проверку отправить нельзя
    assert client.post(f"/api/checks/{check['id']}/escalate", headers=h(OTHER)).status_code == 403
    sent = client.post(f"/api/checks/{check['id']}/escalate", headers=h(STUDENT)).json()
    assert sent["status"] == "pending"
    again = client.get(f"/api/checks/{check['id']}", headers=h(STUDENT)).json()
    assert again["truth"]["code"] == "pending" and again["truth"]["label"] == "На проверке" and not again["truth"]["can_escalate"]
    # Второй человек спросил то же — присоединился к проверке
    other = client.post("/api/check", json={"mode": "pravda", "text": "у МУИТ новый ректор с понедельника, говорят"}, headers=h(OTHER)).json()
    assert other["truth"]["times_checked"] == 2 and other["truth"]["code"] == "pending"
    # Модератор выносит вердикт кнопкой в Telegram
    from tests.test_bot import Harness

    harness = Harness()
    harness.click(f"mq:v:post:{sent['post_id']}:refuted", user_id=OTHER)  # не модератор — откажут
    assert client.get(f"/api/checks/{check['id']}", headers=h(STUDENT)).json()["truth"]["code"] == "pending"
    notifier.sent.clear()
    harness.click(f"mq:v:post:{sent['post_id']}:refuted", user_id=MOD, name="Модератор")
    updated = client.get(f"/api/checks/{check['id']}", headers=h(STUDENT)).json()
    assert updated["truth"]["code"] == "refuted" and updated["card"]["status"] == "red"
    assert any(uid == STUDENT and "Опровергнуто" in txt for uid, txt in notifier.sent)
    # Лента «Слухи и факты»
    feed = client.get("/api/facts", headers=h(OTHER)).json()
    assert feed[0]["label"] == "Опровергнуто" and feed[0]["times_checked"] >= 2
    # Следующий спросивший получает готовый вердикт сразу (без ИИ)
    third = client.post("/api/check", json={"mode": "pravda", "text": "Новый ректор у МУИТ с понедельника!!!"}, headers=h(SUBSCRIBER)).json()
    assert third["card"]["status"] == "red" and third["provider"] == "community" and third["truth"]["times_checked"] == 3


def test_reject_with_reason_visible_to_author(client, staff, notifier):
    post = client.post("/api/posts", json={"kind": "senior_q", "title": "Как сдать вышмат без нервов?"}, headers=h(STUDENT)).json()
    r = client.post("/api/mod/decide", json={"key": f"post:{post['id']}", "action": "reject", "reason": "details"}, headers=h(MOD))
    assert r.json()["status"] == "rejected"
    item = client.get("/api/me/submissions", headers=h(STUDENT)).json()[0]
    assert item["status_label"] == "Отклонено" and "подробностей" in item["reason"]
    assert any(uid == STUDENT and "Причина" in txt for uid, txt in notifier.sent)
    # Отказ без причины не принимается
    post2 = client.post("/api/posts", json={"kind": "senior_q", "title": "Где взять справку?"}, headers=h(STUDENT)).json()
    assert client.post("/api/mod/decide", json={"key": f"post:{post2['id']}", "action": "reject", "reason": ""}, headers=h(MOD)).status_code == 409
    # «Отредактировать и одобрить»
    r = client.post("/api/mod/decide", json={"key": f"post:{post2['id']}", "action": "edit", "title": "Где взять справку с места учёбы?"}, headers=h(MOD))
    assert r.json()["status"] == "approved"
    assert client.get(f"/api/posts/{post2['id']}", headers=h(OTHER)).json()["title"] == "Где взять справку с места учёбы?"


def test_admin_grants_moderator(client, staff):
    assert client.post("/api/mod/staff", json={"user_id": OTHER}, headers=h(MOD)).status_code == 403  # модератор не назначает
    staff_list = client.post("/api/mod/staff", json={"user_id": OTHER}, headers=h(ADMIN)).json()
    assert any(s["user_id"] == OTHER and s["role"] == "moderator" for s in staff_list)
    assert client.get("/api/mod/queue", headers=h(OTHER)).status_code == 200
    assert client.get("/api/me", headers=h(OTHER)).json()["is_moderator"] is True
    client.delete(f"/api/mod/staff/{OTHER}", headers=h(ADMIN))
    assert client.get("/api/mod/queue", headers=h(OTHER)).status_code == 403


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


def test_lost_photo_encrypted_and_access_checked(client, staff):
    from backend.app.services import files

    post = client.post("/api/posts", json={"kind": "lost", "title": "синий шарф", "body": "аудитория 405"}, headers=h(STUDENT)).json()
    raw = _png()
    body = {"file_base64": base64.b64encode(raw).decode(), "file_type": "image/png"}
    assert client.post(f"/api/posts/{post['id']}/photo", json=body, headers=h(STUDENT)).status_code == 428  # нет согласия
    client.post("/api/me/consent", json={"given": True}, headers=h(STUDENT))
    # Исполняемый файл под видом картинки не пройдёт
    fake = {"file_base64": base64.b64encode(b"MZ\x90\x00" + b"\x00" * 100).decode(), "file_type": "image/png"}
    assert client.post(f"/api/posts/{post['id']}/photo", json=fake, headers=h(STUDENT)).status_code == 400
    assert client.post(f"/api/posts/{post['id']}/photo", json=body, headers=h(STUDENT)).json() == {"ok": True}
    # Чужой не может добавить фото к чужому объявлению и не видит фото до модерации
    assert client.post(f"/api/posts/{post['id']}/photo", json=body, headers=h(OTHER)).status_code in (403, 409)
    assert client.get(f"/api/posts/{post['id']}/photo", headers=h(OTHER)).status_code in (404, 409)
    assert client.get(f"/api/posts/{post['id']}/photo", headers=h(STUDENT)).content == raw
    # На диске — шифротекст под случайным именем, без расширения
    row = files.find(f"post:{post['id']}", "post_photo")
    stored = (files.PRIVATE_DIR / row.storage_name).read_bytes()
    assert raw not in stored and len(row.storage_name) == 32 and "." not in row.storage_name
    client.post("/api/mod/decide", json={"key": f"post:{post['id']}", "action": "approve"}, headers=h(MOD))
    assert client.get(f"/api/posts/{post['id']}/photo", headers=h(OTHER)).content == raw
    # «Удалить мои данные» удаляет и файл
    client.delete("/api/me", headers=h(STUDENT))
    assert not (files.PRIVATE_DIR / row.storage_name).exists()


SYLLABUS_TEXT = """Дисциплина: Базы данных
Лабораторная работа 1 — до 20.10.2026 (10%)
Рубежный контроль 1 — 25.10.2026 (20%)
Проект — сдать до 05.12.2026 (20%)
Итоговый экзамен 40%
Пересдача FX — по заявлению в Офис регистратора
Пропуск более 20% занятий — недопуск к экзамену
"""


def _docx(text: str) -> bytes:
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{paras}<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Эссе</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>до 01.11.2026 (10%)</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    from fpdf import FPDF

    from backend.app.core.fonts import find_fonts

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("Body", "", find_fonts()[0])
    pdf.set_font("Body", size=11)
    for line in text.splitlines():
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@pytest.mark.parametrize("maker", [_docx, _pdf])
def test_syllabus_file_to_plan(client, staff, maker):
    from backend.app.services import hubs, prefs

    hubs.seed_hubs()
    hub = hubs.create_hub_direct(MOD, "Базы данных", "2")
    raw = maker(SYLLABUS_TEXT)
    body = {"file_base64": base64.b64encode(raw).decode(), "file_name": "syllabus"}
    assert client.post("/api/syllabus/upload", json=body, headers=h(STUDENT)).status_code == 428
    client.post("/api/me/consent", json={"given": True}, headers=h(STUDENT))
    sy = client.post("/api/syllabus/upload", json=body, headers=h(STUDENT)).json()
    titles = [d["title"] for d in sy["card"]["deadlines"]]
    assert sy["dated"] >= 3 and any("Лабораторная" in x for x in titles)
    assert sy["hub_id"] == hub["id"]  # хаб подобран по названию дисциплины
    assert any("Пересдача" in x for x in sy["card"]["retake_rules"]) and sy["card"]["absence_rules"]
    # Чужой не видит и не меняет мой силлабус
    assert client.get(f"/api/syllabi/{sy['id']}", headers=h(OTHER)).status_code == 404
    assert client.post(f"/api/syllabi/{sy['id']}/to-plan", headers=h(OTHER)).status_code == 404
    # Дедлайны с датами попадают в «План» сразу при загрузке, напоминания включаются; повторно — без дублей
    assert sy["plan"]["added"] == sy["dated"] and sy["plan"]["reminders_on"] and prefs.get_bool(STUDENT, "plan.remind")
    assert client.post(f"/api/syllabi/{sy['id']}/to-plan", headers=h(STUDENT)).json()["added"] == 0
    # «Предметы»: предмет со страницей дисциплины, его задачи и как считается оценка
    subjects = client.get("/api/subjects", headers=h(STUDENT)).json()["items"]
    item = next(x for x in subjects if x["key"] == f"h{hub['id']}")
    assert item["tasks_total"] == sy["dated"] and item["syllabus_id"] == sy["id"]
    page = client.get(f"/api/subjects/h{hub['id']}", headers=h(STUDENT)).json()
    assert len(page["tasks"]) == sy["dated"] and page["grading"]["admission_min_percent"] == 50
    assert client.get(f"/api/subjects/h{hub['id']}", headers=h(OTHER)).status_code == 404
    tasks = client.get("/api/plan?view=board", headers=h(STUDENT)).json()
    flat = json.dumps(tasks, ensure_ascii=False)
    assert "Лабораторная работа 1" in flat and "Базы данных" in flat
    # Привязка к хабу видна в хабе (мой силлабус)
    assert client.get(f"/api/syllabi?hub_id={hub['id']}", headers=h(STUDENT)).json()[0]["id"] == sy["id"]


def test_upload_errors_are_clear(client, staff):
    client.post("/api/me/consent", json={"given": True}, headers=h(STUDENT))
    exe = base64.b64encode(b"MZ" + b"\x00" * 200).decode()
    r = client.post("/api/syllabus/upload", json={"file_base64": exe}, headers=h(STUDENT))
    assert r.status_code == 422 and "формат" in r.json()["error"]
    zip_not_docx = io.BytesIO()
    with zipfile.ZipFile(zip_not_docx, "w") as z:
        z.writestr("evil.exe", "x")
    r = client.post("/api/syllabus/upload", json={"file_base64": base64.b64encode(zip_not_docx.getvalue()).decode()}, headers=h(STUDENT))
    assert r.status_code == 422
    big = base64.b64encode(b"%PDF-" + b"0" * (11 * 1024 * 1024)).decode()
    r = client.post("/api/syllabus/upload", json={"file_base64": big}, headers=h(STUDENT))
    assert r.status_code == 413 and "МБ" in r.json()["error"]


def test_other_users_check_not_accessible(client, staff):
    check = client.post("/api/check", json={"mode": "spor", "text": "Сосед не вернул 5000 тенге"}, headers=h(STUDENT)).json()
    for url in (f"/api/checks/{check['id']}", f"/api/checks/{check['id']}/image.png"):
        assert client.get(url, headers=h(OTHER)).status_code == 403
    assert client.post(f"/api/checks/{check['id']}/vote", json={"value": 1}, headers=h(OTHER)).status_code == 403


def test_masking_before_llm_and_in_history():
    from backend.app.core.engine import build_user_prompt

    prompt = build_user_prompt("Мой ИИН 990101300123, карта 4400 4301 2345 6789, тел +7 701 123 45 67, паспорт N12345678", [], [], [])
    assert "990101300123" not in prompt and "4400 4301 2345 6789" not in prompt and "701 123 45" not in prompt and "12345678" not in prompt


def test_pravda_web_layer_sources_with_links_and_dates(client, staff, monkeypatch):
    from backend.app.search import web

    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "test-key")
    reset_caches()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("include_domains") == ["iitu.edu.kz"]:
            results = [{"title": "Назначен новый ректор МУИТ", "url": "https://iitu.edu.kz/ru/news/rector-2026/",
                        "content": "Приказом назначен новый ректор МУИТ.", "published_date": "2026-10-01"}]
        else:
            results = [{"title": "В МУИТ сменился ректор", "url": "https://tengrinews.kz/kazakhstan_news/muit-rektor/",
                        "content": "В Международном университете информационных технологий новый ректор.", "published_date": "2026-10-02"},
                       {"title": "Блог: слухи о МУИТ", "url": "https://example.blog/muit", "content": "Говорят..."}]
        return httpx.Response(200, json={"results": results})

    monkeypatch.setattr(web, "_transport", httpx.MockTransport(handler))
    check = client.post("/api/check", json={"mode": "pravda", "text": "новый ректор МУИТ"}, headers=h(STUDENT)).json()
    sources = check["card"]["sources"]
    assert sources and sources[0]["url"].startswith("https://iitu.edu.kz") and sources[0]["date"] == "2026-10-01"
    assert all(s["url"] for s in sources)
    # Демо без ИИ не выдумывает вывод: «Не подтверждено» и можно отправить модератору
    assert check["truth"]["code"] == "unconfirmed" and check["truth"]["can_escalate"]


def test_today_and_path_screens(client, staff):
    client.patch("/api/me/profile", json={"role": "student", "course": "1"}, headers=h(STUDENT))
    today = client.get("/api/today", headers=h(STUDENT)).json()
    assert {"deadlines", "radar", "answers", "decisions"} <= set(today)
    path = client.get("/api/path", headers=h(STUDENT)).json()
    assert path["next"] == "quiz" and path["quiz"]["questions"] and path["checklist"]["total"] > 0
    assert path["letter"]["deliver_on"] >= "2026-12-01"


def test_moderator_chat_buttons_and_reasons(monkeypatch, staff, notifier):
    from backend.app.services import community
    from tests.test_bot import Harness

    from backend.app.core.notify import set_notifier

    monkeypatch.setenv("MOD_CHAT_ID", "-1009999")
    reset_caches()
    set_notifier(notifier)
    post = community.create_post("lost", STUDENT, "Айгерим", "Потерял(а): наушники", "у столовой")
    asyncio.run(community.announce_pending(post["id"]))
    assert notifier.sent[-1][0] == -1009999  # в служебный чат
    harness = Harness()
    harness.click(f"mq:n:post:{post['id']}", user_id=MOD, name="Модератор", chat_id=-1009999)  # показать причины
    harness.click(f"mq:r:post:{post['id']}:personal", user_id=MOD, name="Модератор", chat_id=-1009999)
    assert community.get_post(post["id"], STUDENT)["status"] == "rejected"
    assert "личные данные" in community.get_post(post["id"], STUDENT)["reject_reason"]


def test_bot_radar_moi_and_escalate_button(staff, notifier):
    """Бот: «/radar описание» → модератору; «/moi» — статус; «Правда» без подтверждений → кнопка «Отправить модератору»."""
    from tests.test_bot import Harness

    harness = Harness()
    harness.message("/radar Продают «сливы» рубежки за 3000 на карту", user_id=STUDENT, name="Айгерим")
    assert "модератор" in harness.last_text().lower()
    assert any(uid == MOD for uid, _ in notifier.sent)
    harness.message("/moi", user_id=STUDENT, name="Айгерим")
    assert "На проверке" in harness.last_text()
    harness.message("/pravda Говорят, МУИТ закрывают с понедельника", user_id=STUDENT, name="Айгерим")
    assert any(d.startswith("esc:") for d in harness.last_markup_data())
    esc = next(d for d in harness.last_markup_data() if d.startswith("esc:"))
    harness.click(esc, user_id=OTHER, name="Чужой")  # чужая проверка — нельзя
    harness.click(esc, user_id=STUDENT, name="Айгерим")
    with get_sessionmaker()() as session:
        from backend.app.db.models_campus import Post

        assert session.query(Post).filter(Post.kind == "rumor", Post.status == "pending").count() == 1


def test_groq_browser_search_parsing():
    """Структура как в реальном ответе Groq browser_search: результаты поиска без текста + просмотренные страницы строками L0..."""
    from backend.app.search.web import parse_groq_message

    url = "https://iitu.edu.kz/ru/news/rustem-bigari-appointed-chairman-of-the-management-board-rector/"
    message = {
        "content": '{"results":[{"title":"Рустем Бигари назначен Ректором","url":"' + url + '","date":"","snippet":""},'
                   '{"title":"Выдуманная статья","url":"https://fake.example/news","date":"2026-01-01","snippet":"..."}]}',
        "executed_tools": [
            {"search_results": {"results": [{"title": "Рустем Бигари назначен Председателем Правления", "url": url, "content": ""},
                                            {"title": "Новым ректором МУИТ стал Рустем Бигари", "url": "https://bes.media/news/novim-rektorom-muit/", "content": ""}]}},
            {"search_results": {"results": [{"title": "iitu.edu.kz - viewing lines [0 - 20] of 133", "url": url,
                                             "content": "L0: \nL1: URL: x\nL7: # Рустем Бигари назначен Председателем Правления – Ректором\nL9: 1 июля 2026"}]}},
            {"search_results": {"results": [{"title": "iitu.edu.kz", "url": url, "content": "No `find` results for pattern: `2025`"}]}},
        ],
    }
    results = parse_groq_message(message)
    urls = [r.url for r in results]
    assert url in urls and "https://fake.example/news" not in urls  # выдуманную ссылку не берём
    official = next(r for r in results if r.url == url)
    assert "Ректором" in official.snippet and official.date == "2026-07-01" and official.tier == 0
    assert any("bes.media" in u for u in urls)


def test_web_rank_prefers_fresh_then_official_and_detects_captcha(monkeypatch):
    """Свежая статья СМИ выше старой новости вуза; DuckDuckGo с капчей — «поиск недоступен», а не «ничего не нашлось»."""
    from backend.app.search import web

    old_official = web.WebResult("Назначен ректор", "https://iitu.edu.kz/ru/news/old/", "…", "2018-05-01")
    fresh_media = web.WebResult("Новым ректором МУИТ стал …", "https://bes.media/news/x/", "…", "2026-07-01")
    fresh_official = web.WebResult("Назначен ректор", "https://iitu.edu.kz/ru/news/new/", "…", "2026-07-01")
    social = web.WebResult("Пост", "https://www.instagram.com/p/x/", "…", "2026-07-02")
    ranked = web.rank([old_official, social, fresh_media, fresh_official], today="2026-10-09")
    assert [r.url for r in ranked] == [fresh_official.url, fresh_media.url, social.url, old_official.url]

    monkeypatch.setenv("SEARCH_PROVIDER", "ddg")
    reset_caches()
    monkeypatch.setattr(web, "_transport", httpx.MockTransport(lambda req: httpx.Response(202, text="Unfortunately, bots use DuckDuckGo too.")))
    results, worked = asyncio.run(web.search_checked("новый ректор МУИТ"))
    assert results == [] and worked is False

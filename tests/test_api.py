"""API Mini App: здоровье, статика, проверки, договорённости, тренажёр, семья, приватность."""

from __future__ import annotations

from tests.conftest import tma_header


# ---------- каркас ----------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["llm"] == "demo"


def test_miniapp_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "ANO IITU" in page.text
    for path in ("/js/app.js", "/js/core.js", "/js/card.js", "/js/screens/today.js", "/js/screens/subjects.js", "/img/iitu-logo.svg", "/css/app.css"):
        assert client.get(path).status_code == 200, path


def test_unknown_api_404_in_russian(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert "нет" in response.json()["error"]


def test_i18n_and_modes(client):
    strings = client.get("/api/i18n/ru").json()
    assert strings["ui.home.title"] == "Что проверить?"
    assert "card.status.red" in strings
    assert client.get("/api/i18n/xx").status_code == 404
    modes = client.get("/api/modes").json()
    assert [m["command"] for m in modes][:2] == ["pravda", "razvod"]


# ---------- проверки ----------

def test_check_flow(client):
    response = client.post("/api/check", json={"mode": "razvod", "text": "Срочно назовите код из SMS, карта заблокирована"})
    assert response.status_code == 200, response.text
    check = response.json()
    assert check["status"] == "red"
    cid = check["id"]

    history = client.get("/api/checks").json()
    assert history[0]["id"] == cid

    assert client.post(f"/api/checks/{cid}/vote", json={"value": 1}).json() == {"agree": 1, "disagree": 0}
    assert client.post(f"/api/checks/{cid}/vote", json={"value": -1}).json() == {"agree": 0, "disagree": 1}  # передумал

    assert client.post(f"/api/checks/{cid}/sources", json={"url": "not a link"}).status_code == 400
    assert client.post(f"/api/checks/{cid}/sources", json={"url": "https://nationalbank.kz/news"}).status_code == 200
    assert client.get(f"/api/checks/{cid}").json()["user_sources"][0]["url"] == "https://nationalbank.kz/news"

    simple = client.post(f"/api/checks/{cid}/simplify").json()
    assert "опасно" in simple["text"].lower()

    png = client.get(f"/api/checks/{cid}/image.png")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"

    me = client.get("/api/me").json()
    assert me["checks_count"] == 1


def test_check_validation_errors_in_russian(client):
    assert "текст" in client.post("/api/check", json={"mode": "razvod", "text": ""}).json()["error"]
    assert client.post("/api/check", json={"mode": "unknown", "text": "x"}).status_code == 422
    # Без согласия на обработку файлов загрузка не принимается (428), с согласием — проверяется содержимое.
    no_consent = client.post("/api/check", json={"mode": "chek", "text": "", "file_base64": "%%%", "file_type": "image/png"})
    assert no_consent.status_code == 428
    client.post("/api/me/consent", json={"given": True})
    bad = client.post("/api/check", json={"mode": "chek", "text": "", "file_base64": "%%%", "file_type": "image/png"})
    assert bad.status_code == 400


def test_private_check_hidden_from_others(client, with_token):
    owner = tma_header(1001, "Автор")
    other = tma_header(1002, "Чужой")
    cid = client.post("/api/check", json={"mode": "spor", "text": "Сосед по общаге не вернул 5000 тенге"}, headers=owner).json()["id"]
    assert client.get(f"/api/checks/{cid}", headers=other).status_code == 403
    assert client.get(f"/api/checks/{cid}", headers=owner).status_code == 200


# ---------- договорённости ----------

def test_agreement_confirm_by_second_party(client, with_token, notifier):
    a = tma_header(2001, "Аскар")
    b = tma_header(2002, "Марат", username="marat_k")
    created = client.post("/api/agreements", json={"text": "Аскар отдаёт Марату 20 000 ₸ до 15 октября"}, headers=a).json()
    code = created["code"]
    assert created["status"] == "pending"
    assert "20 000" in created["draft"]["amount"]
    assert created["deadline_iso"].endswith("-10-15")

    own = client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=a)
    assert own.status_code == 409  # автор сам себе не подтверждает

    confirmed = client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=b).json()
    assert confirmed["status"] == "confirmed" and confirmed["counterparty_name"] == "Марат"

    pdf = client.get(f"/api/agreements/{code}/pdf", headers=a)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get(f"/api/agreements/{code}/pdf", headers=tma_header(2003)).status_code == 403

    done = client.post(f"/api/agreements/{code}/action", json={"action": "done"}, headers=b).json()
    assert done["status"] == "done"
    assert len(client.get("/api/agreements", headers=b).json()) == 1


def test_agreement_for_specific_username(client, with_token):
    a = tma_header(3001, "Аня")
    code = client.post("/api/agreements", json={"text": "@dana_kz вернёт книгу завтра"}, headers=a).json()["code"]
    wrong = client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=tma_header(3002, username="someone"))
    assert wrong.status_code == 409 and "@dana_kz" in wrong.json()["error"]
    right = client.post(f"/api/agreements/{code}/action", json={"action": "confirm"}, headers=tma_header(3003, username="Dana_KZ"))
    assert right.status_code == 200


# ---------- тренажёр, проверка дня ----------

def test_trainer_flow(client):
    data = client.get("/api/trainer/scenarios").json()
    assert len(data["scenarios"]) >= 5
    session = client.post("/api/trainer/start", json={"scenario": "bank_security"}).json()
    assert session["messages"][0]["role"] == "bot"
    sid = session["id"]
    step = client.post(f"/api/trainer/{sid}/message", json={"text": "А кто вы? Я сам перезвоню в банк"}).json()
    assert not step["finished"]
    final = client.post(f"/api/trainer/{sid}/message", json={"text": "Нет, код не скажу, это мошенничество"}).json()
    assert final["finished"] and final["review"]["immunity"] >= 80
    assert client.get("/api/me").json()["immunity"] >= 80
    assert client.get(f"/api/trainer/{sid}").json()["finished"]


def test_trainer_risky_answer_low_immunity(client):
    sid = client.post("/api/trainer/start", json={"scenario": "bank_security"}).json()["id"]
    result = client.post(f"/api/trainer/{sid}/message", json={"text": "Да, конечно, вот код 482913"}).json()
    assert result["finished"] and result["review"]["immunity"] <= 60
    assert result["review"]["mistakes"]


# ---------- семья, чаты, источники ----------

def test_family(client, with_token):
    mom = tma_header(4001, "Мама")
    son = tma_header(4002, "Сын")
    info = client.post("/api/family", headers=mom).json()
    assert info["has_family"] and info["invite_token"]
    joined = client.post("/api/family/join", json={"token": info["invite_token"]}, headers=son).json()
    assert len(joined["members"]) == 2
    assert client.post("/api/family/join", json={"token": "wrong-token"}, headers=tma_header(4003)).status_code == 409
    settings = client.patch("/api/family/settings", json={"notify": False, "code_word_set": True}, headers=son).json()
    assert settings["notify"] is False and settings["code_word_set"] is True
    assert client.post("/api/family/leave", headers=son).json()["has_family"] is False


def test_sources_and_chats(client):
    data = client.get("/api/sources").json()
    assert len(data["base"]) >= 10 and all("is_demo" in s for s in data["base"])
    assert client.get("/api/chats").json() == []


# ---------- приватность ----------

def test_export_and_delete(client):
    client.post("/api/check", json={"mode": "spor", "text": "Спор о том, кто моет посуду в общаге"})
    client.patch("/api/me/settings", json={"large_font": True})
    exported = client.get("/api/me/export").json()
    assert exported["user"]["large_font"] is True and len(exported["checks"]) == 1
    assert client.delete("/api/me").json() == {"deleted": True}
    assert client.get("/api/checks").json() == []


def test_send_without_bot_gives_hint(client):
    response = client.post("/api/me/export/send")
    assert response.status_code == 409 and "бот" in response.json()["error"]

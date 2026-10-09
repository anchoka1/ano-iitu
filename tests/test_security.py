"""Безопасность: подпись initData, авторизация, режим разработки, маскирование."""

from __future__ import annotations

import json
import time

import pytest

from backend.app.security.initdata import InitDataError, build_init_data, validate_init_data
from backend.app.security.masking import mask_sensitive
from tests.conftest import TEST_TOKEN, tma_header

USER = json.dumps({"id": 777, "first_name": "Айгерим", "language_code": "ru"}, ensure_ascii=False)


def make(fields=None, token=TEST_TOKEN, auth_date=None):
    data = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAE", "user": USER}
    data.update(fields or {})
    return build_init_data(data, token)


# ---------- initData ----------

def test_valid_init_data():
    result = validate_init_data(make(), TEST_TOKEN)
    assert result.user.id == 777
    assert result.user.first_name == "Айгерим"


def test_start_param_passed():
    result = validate_init_data(make({"start_param": "check_15"}), TEST_TOKEN)
    assert result.start_param == "check_15"


def test_tampered_user_rejected():
    data = make().replace("777", "778")  # «подменили» id пользователя
    with pytest.raises(InitDataError, match="Подпись"):
        validate_init_data(data, TEST_TOKEN)


def test_other_bot_token_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(make(token="999:" + "B" * 35), TEST_TOKEN)


def test_expired_rejected():
    old = int(time.time()) - 2 * 86400
    with pytest.raises(InitDataError, match="устарела"):
        validate_init_data(make(auth_date=old), TEST_TOKEN, max_age_seconds=86400)


def test_missing_hash_rejected():
    with pytest.raises(InitDataError, match="hash"):
        validate_init_data(f"auth_date={int(time.time())}&user=x", TEST_TOKEN)


def test_duplicate_fields_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(make() + "&user=evil", TEST_TOKEN)


def test_no_token_on_server():
    with pytest.raises(InitDataError, match="TELEGRAM_BOT_TOKEN"):
        validate_init_data(make(), "")


# ---------- авторизация API ----------

def test_dev_mode_local_gets_dev_user(client):
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["is_dev"] is True


def test_dev_mode_through_tunnel_denied(client):
    # Туннель (Cloudflare/ngrok) приходит на 127.0.0.1, но добавляет заголовки прокси.
    response = client.get("/api/me", headers={"X-Forwarded-For": "203.0.113.5"})
    assert response.status_code == 401
    assert "Telegram" in response.json()["error"]


def test_production_requires_signature(client, with_token):
    assert client.get("/api/me").status_code == 401


def test_production_valid_signature(client, with_token):
    response = client.get("/api/me", headers=tma_header(555, "Марат"))
    assert response.status_code == 200
    body = response.json()
    assert body["telegram_id"] == 555 and body["is_dev"] is False


def test_production_bad_signature(client, with_token):
    headers = tma_header(555)
    headers["Authorization"] = headers["Authorization"].replace("555", "556")
    assert client.get("/api/me", headers=headers).status_code == 401


# ---------- маскирование ----------

@pytest.mark.parametrize("raw, hidden, kept", [
    ("Позвоните +7 701 123 45 67", "701 123", "+7 *** *** ** 67"),
    ("карта 4400 4301 2345 6789", "4301", "**** **** **** 6789"),
    ("ИИН 900101300123", "900101300123", "[ИИН скрыт]"),
    ("тел 87011234567 срочно", "7011234", "+7 *** *** ** 67"),
])
def test_masking(raw, hidden, kept):
    masked = mask_sensitive(raw)
    assert hidden not in masked
    assert kept in masked


def test_masking_keeps_ordinary_text():
    text = "Штраф 25 000 ₸ до 15 октября"
    assert mask_sensitive(text) == text

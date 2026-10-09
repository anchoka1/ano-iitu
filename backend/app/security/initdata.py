"""Проверка подписи initData от Telegram Mini App.

Суть задачи: когда Mini App открывается в Telegram, Telegram передаёт ему
строку initData — «кто открыл приложение» (id, имя, время входа) плюс
подпись hash. Подпись считается секретом — токеном бота, который знает
только наш сервер. Значит, если подпись сходится, данные точно пришли от
Telegram и их никто не подменил.

initDataUnsafe (то же самое, но уже разобранное в JS) доверять нельзя:
его можно подделать в браузере. Поэтому сервер всегда проверяет сырую
строку initData сам.

Алгоритм (из документации Telegram, раздел «Validating data»):
  1. Разобрать строку вида "a=1&b=2&hash=..." на пары ключ=значение.
  2. Убрать hash, остальные пары отсортировать по ключу и склеить
     через перевод строки: "auth_date=...\\nquery_id=...\\nuser=...".
  3. secret_key = HMAC_SHA256(ключ="WebAppData", сообщение=токен_бота).
  4. Ожидаемый hash = HMAC_SHA256(ключ=secret_key, сообщение=строка из п.2)
     в виде шестнадцатеричной строки.
  5. Сравнить с присланным hash. Плюс проверить, что auth_date не старый.

HMAC — «подпись с секретом»: без знания ключа нельзя получить правильный
результат, а изменение хоть одного символа данных полностью меняет подпись.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode


class InitDataError(Exception):
    """Подпись не прошла проверку. Текст ошибки — для человека, по-русски."""


@dataclass(frozen=True)
class TelegramUser:
    """Пользователь Telegram (только нужные нам поля — минимум данных)."""

    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = "ru"


@dataclass(frozen=True)
class ValidatedInitData:
    """Результат успешной проверки."""

    user: TelegramUser
    auth_date: int
    start_param: str = ""  # параметр из ссылки t.me/бот/приложение?startapp=...


def _secret_key(bot_token: str) -> bytes:
    # Шаг 3: обратите внимание — ключом служит строка "WebAppData",
    # а токен бота — это сообщение. Перепутать порядок — частая ошибка.
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _data_check_string(fields: dict[str, str]) -> str:
    # Шаг 2: все поля, кроме hash, по алфавиту, через "\n".
    return "\n".join(f"{k}={fields[k]}" for k in sorted(fields) if k != "hash")


def compute_hash(fields: dict[str, str], bot_token: str) -> str:
    """Шаг 4: считает правильную подпись для набора полей."""
    return hmac.new(
        _secret_key(bot_token), _data_check_string(fields).encode(), hashlib.sha256
    ).hexdigest()


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400,
    now: float | None = None,
) -> ValidatedInitData:
    """Проверяет initData и возвращает данные пользователя.

    Бросает InitDataError, если что-то не так. Параметр now нужен тестам,
    чтобы «подменить» текущее время.
    """
    if not bot_token:
        raise InitDataError("На сервере не задан TELEGRAM_BOT_TOKEN — подпись проверить нечем.")
    if not init_data:
        raise InitDataError("Пустые данные авторизации Telegram.")

    try:
        # parse_qsl разбирает "a=1&b=2" в [("a","1"),("b","2")] и раскодирует %XX.
        # strict_parsing=True — ругаться на мусор вместо молчаливого пропуска.
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("Данные авторизации Telegram повреждены.") from exc

    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InitDataError("В данных авторизации повторяются поля.")

    received_hash = fields.get("hash", "")
    if not received_hash:
        raise InitDataError("В данных авторизации нет подписи (hash).")

    expected_hash = compute_hash(fields, bot_token)
    # compare_digest сравнивает строки за одинаковое время независимо от того,
    # где первое отличие. Обычное == позволило бы подбирать подпись по времени ответа.
    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("Подпись Telegram не совпала: данные подделаны или от другого бота.")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("Неверное время входа (auth_date).") from exc

    current = time.time() if now is None else now
    if auth_date <= 0 or current - auth_date > max_age_seconds:
        raise InitDataError("Сессия Telegram устарела. Закройте и откройте приложение заново.")

    try:
        user_raw = json.loads(fields.get("user", ""))
        user = TelegramUser(
            id=int(user_raw["id"]),
            first_name=str(user_raw.get("first_name", "")),
            last_name=str(user_raw.get("last_name", "")),
            username=str(user_raw.get("username", "")),
            language_code=str(user_raw.get("language_code", "ru")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("В данных авторизации нет пользователя.") from exc

    return ValidatedInitData(
        user=user, auth_date=auth_date, start_param=fields.get("start_param", "")
    )


def build_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Собирает подписанную строку initData — так, как это делает Telegram.

    Нужна тестам (и для ручных экспериментов): продакшен-код её не использует.
    """
    signed = dict(fields)
    signed["hash"] = compute_hash(fields, bot_token)
    return urlencode(signed)

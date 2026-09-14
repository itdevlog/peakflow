"""Валидация Telegram initData (HMAC-SHA256)."""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

from web.auth import get_user_from_init_data, parse_init_data, validate_init_data

BOT_TOKEN = "123456:ABC-DEF_token"
SECRET = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()


def _make_init_data(params: dict, sign: bool = True, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    params = {**params, "auth_date": str(auth_date)}
    pairs = sorted(params.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    if sign:
        sig = hmac.new(SECRET, data_check_string.encode(), hashlib.sha256).hexdigest()
        pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def test_parse_init_data_decodes_user():
    user = {"id": 42, "first_name": "Иван"}
    raw = _make_init_data({"user": json.dumps(user, ensure_ascii=False)})
    parsed = parse_init_data(raw)
    assert parsed["user"]["id"] == 42
    assert parsed["user"]["first_name"] == "Иван"
    assert "hash" in parsed


def test_validate_ok():
    raw = _make_init_data({"user": json.dumps({"id": 42})})
    result = validate_init_data(raw, BOT_TOKEN)
    assert result is not None
    assert result["user"]["id"] == 42


def test_validate_bad_signature():
    raw = _make_init_data({"user": '{"id": 42}'}, sign=False) + "&hash=deadbeef"
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_expired():
    raw = _make_init_data({"user": '{"id": 42}'}, auth_date=int(time.time()) - 90000)
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_future():
    raw = _make_init_data({"user": '{"id": 42}'}, auth_date=int(time.time()) + 90000)
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_empty():
    assert validate_init_data("", BOT_TOKEN) is None
    assert validate_init_data("auth_date=1&hash=zz", "") is None


def test_validate_wrong_token():
    raw = _make_init_data({"user": '{"id": 42}'})
    assert validate_init_data(raw, "999:other") is None


def test_get_user():
    raw = _make_init_data({"user": '{"id": 7}'})
    assert get_user_from_init_data(raw, BOT_TOKEN) == {"id": 7}


def test_get_user_invalid():
    assert get_user_from_init_data("auth_date=1&hash=zz", BOT_TOKEN) is None


def test_validate_non_ascii_hash_returns_none():
    raw = f"auth_date={int(time.time())}&hash={quote('пароль')}"
    assert validate_init_data(raw, BOT_TOKEN) is None

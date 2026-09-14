"""Валидация Telegram WebApp initData (HMAC-SHA256)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def parse_init_data(init_data: str) -> dict:
    """Разбирает строку initData в dict; 'user'/'receiver' — из JSON."""
    result: dict = dict(parse_qsl(init_data, keep_blank_values=True))
    for key in ("user", "receiver"):
        if key in result:
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                result[key] = None
    return result


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict | None:
    """Проверяет подпись HMAC и свежесть auth_date. None = невалидно."""
    if not init_data or not bot_token:
        return None
    raw = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = raw.pop("hash", None)
    if not received_hash:
        return None
    try:
        auth_age = time.time() - int(raw.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_age < 0 or auth_age > max_age:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(raw.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(calculated.encode(), received_hash.encode()):
            return None
    except (ValueError, TypeError):
        return None
    return parse_init_data(init_data)


def get_user_from_init_data(init_data: str, bot_token: str) -> dict | None:
    """Валидирует initData и возвращает dict пользователя (или None)."""
    data = validate_init_data(init_data, bot_token)
    user = (data or {}).get("user")
    return user if isinstance(user, dict) else None

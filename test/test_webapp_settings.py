"""Тесты настроек Mini App (SP2c)."""
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import get_reminder_hours, get_setting, init_db
from web.api import create_app

TEST_DB = "test_peakflow.db"
BOT_TOKEN = "123456:ABC-DEF_token"
CHILD_ID = 111
PARENT_IDS = [222, 333]


def make_init_data(user_id: int) -> str:
    auth_date = int(time.time())
    params = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id, "first_name": "T"})}
    pairs = sorted(params.items())
    dcs = "\n".join(f"{k}={v}" for k, v in pairs)
    sig = hmac.new(
        hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest(),
        dcs.encode(), hashlib.sha256,
    ).hexdigest()
    pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def _config():
    return SimpleNamespace(
        DB_PATH=TEST_DB, BOT_TOKEN=BOT_TOKEN, CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS,
        CHILD_NAME="Motya", TARGET_PEF=260, TZ_OFFSET=5,
        ZONE_GREEN=80, ZONE_YELLOW=60, ZONE_RED=50,
    )


def _client():
    return TestClient(create_app({"config": _config(), "bot": None}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


@pytest.fixture(autouse=True)
def _db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    yield


def test_settings_child_forbidden():
    assert _client().get("/api/settings", headers=_auth(CHILD_ID)).status_code == 403


def test_settings_parent_ok():
    body = _client().get("/api/settings", headers=_auth(PARENT_IDS[0])).json()
    assert body["target_pef"] == 260
    assert body["child_name"] == "Motya"
    assert body["total"] == 0
    assert body["reminder_hours"]["child_morning"] == 8


def test_put_target_parent_ok():
    r = _client().put("/api/settings/target", json={"target_pef": 300}, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json() == {"target_pef": 300}
    assert get_setting(TEST_DB, "target_pef") == "300"


@pytest.mark.parametrize("bad", [99, 801])
def test_put_target_out_of_range(bad):
    assert _client().put("/api/settings/target", json={"target_pef": bad},
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_put_target_child_forbidden():
    assert _client().put("/api/settings/target", json={"target_pef": 300},
                         headers=_auth(CHILD_ID)).status_code == 403


def test_put_reminders_parent_ok():
    body = {"child_morning": 7, "child_evening": 19, "parent_morning": 9, "parent_evening": 21}
    r = _client().put("/api/settings/reminders", json=body, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json()["reminder_hours"]["child_morning"] == 7
    assert get_reminder_hours(TEST_DB)["parent_evening"] == 21


@pytest.mark.parametrize("bad", [24, -1])
def test_put_reminders_out_of_range(bad):
    body = {"child_morning": bad, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
    assert _client().put("/api/settings/reminders", json=body,
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_put_reminders_child_forbidden():
    body = {"child_morning": 8, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
    assert _client().put("/api/settings/reminders", json=body,
                         headers=_auth(CHILD_ID)).status_code == 403


def test_put_reminders_rejects_parent_at_child_hour():
    """Escalation must come after the child ping, not at the same minute."""
    body = {"child_morning": 8, "child_evening": 20, "parent_morning": 8, "parent_evening": 22}
    r = _client().put("/api/settings/reminders", json=body, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 422
    assert get_reminder_hours(TEST_DB)["parent_morning"] == 10  # unchanged default

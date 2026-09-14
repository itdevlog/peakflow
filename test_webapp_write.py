"""Тесты write-API Mini App (SP2b)."""
import asyncio
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import add_measurement, get_all_measurements, init_db
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


def _client(bot=None):
    return TestClient(create_app({"config": _config(), "bot": bot}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


def _setup_db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)


@pytest.fixture(autouse=True)
def _fix_tod(monkeypatch):
    import web.api as api
    monkeypatch.setattr(api, "_auto_time_of_day", lambda config: "morning")


def test_add_child_creates_manual_measurement():
    _setup_db()
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pef"] == 250
    assert body["tod"] == "morning"
    assert body["zone"] == "green"
    assert body["pct"] == 96
    rows = get_all_measurements(TEST_DB, CHILD_ID)
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_add_parent_allowed():
    _setup_db()
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200


def test_add_replaces_auto_record():
    _setup_db()
    add_measurement(TEST_DB, 180, "morning", CHILD_ID, 0, source="auto")
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
    assert r.status_code == 200
    rows = get_all_measurements(TEST_DB, CHILD_ID, include_auto=True)
    assert len(rows) == 1
    assert rows[0]["pef_value"] == 250
    assert rows[0]["source"] == "manual"


def test_add_replaces_auto_returns_replaced_slot_id():
    _setup_db()
    auto_id = add_measurement(TEST_DB, 180, "morning", CHILD_ID, 0, source="auto")
    # a newer manual evening row that get_last_measurement would return instead
    add_measurement(TEST_DB, 220, "evening", CHILD_ID, CHILD_ID)
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
    assert r.status_code == 200, r.text
    assert r.json()["tod"] == "morning"
    assert r.json()["id"] == auto_id


def test_add_second_slot_goes_to_other_tod():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    r = _client().post("/api/measurements", json={"pef": 230}, headers=_auth(CHILD_ID))
    assert r.status_code == 200
    assert r.json()["tod"] == "evening"


def test_add_pef_out_of_range():
    _setup_db()
    assert _client().post("/api/measurements", json={"pef": 99}, headers=_auth(CHILD_ID)).status_code == 422
    assert _client().post("/api/measurements", json={"pef": 691}, headers=_auth(CHILD_ID)).status_code == 422


def test_add_requires_auth():
    _setup_db()
    assert _client().post("/api/measurements", json={"pef": 250}).status_code == 403

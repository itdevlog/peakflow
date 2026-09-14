"""Тесты CSV-экспорта и бэкапа Mini App (SP2c)."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import init_db
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


def _client_named(child_name):
    cfg = _config()
    cfg.CHILD_NAME = child_name
    return TestClient(create_app({"config": cfg, "bot": None}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


@pytest.fixture(autouse=True)
def _db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    conn = sqlite3.connect(TEST_DB)
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source, note) "
        "VALUES (111, 240, 'morning', '2026-08-05 08:00:00', 222, 'manual', 'болел')")
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
        "VALUES (111, 250, 'evening', '2026-08-06 20:00:00', 222, 'auto')")
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
        "VALUES (111, 260, 'morning', '2026-09-01 08:00:00', 222, 'manual')")
    conn.commit()
    conn.close()
    yield


def test_csv_child_forbidden():
    assert _client().get("/api/export/csv?period=all", headers=_auth(CHILD_ID)).status_code == 403


def test_csv_all_bom_and_columns():
    r = _client().get("/api/export/csv?period=all", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:3] == b"\xef\xbb\xbf"   # UTF-8 BOM
    text = r.content.decode("utf-8-sig")
    assert "Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил,Заметка,Источник" in text
    assert "болел" in text
    assert "авто" in text
    assert "# Статистика" in text


def test_csv_month_only_that_month():
    r = _client().get("/api/export/csv?period=2026-08", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert "2026-08-05" in text
    # September row excluded (do not assert on target "260" — the summary includes it)
    assert "2026-09-01" not in text


def test_csv_unknown_period_422():
    assert _client().get("/api/export/csv?period=2026-13",
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_csv_no_data_404():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    assert _client().get("/api/export/csv?period=all",
                         headers=_auth(PARENT_IDS[0])).status_code == 404


def test_export_periods():
    body = _client().get("/api/export/periods", headers=_auth(PARENT_IDS[0])).json()
    assert body["months"] == ["2026-08", "2026-09"]


def test_csv_cyrillic_filename_ok():
    r = _client_named("Ребёнок").get("/api/export/csv?period=all", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200, r.text
    cd = r.headers["content-disposition"]
    assert "filename*=utf-8''" in cd
    assert "attachment" in cd


def test_csv_filename_with_quote_newline_is_safe():
    r = _client_named('Ре"бёнок\nX').get("/api/export/csv?period=all", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200, r.text
    cd = r.headers["content-disposition"]
    assert "\n" not in cd and "\r" not in cd
    assert "filename*=utf-8''" in cd


def test_backup_returns_sqlite():
    r = _client().get("/api/backup", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in r.headers["content-disposition"]


def test_backup_child_forbidden():
    assert _client().get("/api/backup", headers=_auth(CHILD_ID)).status_code == 403

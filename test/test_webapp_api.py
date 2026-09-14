"""API Mini App через FastAPI TestClient (SP2a: чтение)."""
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

from fastapi.testclient import TestClient

from database import add_measurement, init_db
from web.api import create_app

TEST_DB = "test_peakflow.db"
BOT_TOKEN = "123456:ABC-DEF_token"
CHILD_ID = 111
PARENT_IDS = [222, 333]


def make_init_data(user_id: int, token: str = BOT_TOKEN, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    params = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id, "first_name": "Test"})}
    pairs = sorted(params.items())
    dcs = "\n".join(f"{k}={v}" for k, v in pairs)
    sig = hmac.new(
        hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(),
        dcs.encode(), hashlib.sha256,
    ).hexdigest()
    pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def _config():
    return SimpleNamespace(
        DB_PATH=TEST_DB, BOT_TOKEN=BOT_TOKEN, CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS,
        CHILD_NAME="Motya", TARGET_PEF=260,
    )


def _client(config=None) -> TestClient:
    return TestClient(create_app({"config": config or _config()}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


def _setup_db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)


def test_healthz_ok():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_path_404():
    assert _client().get("/nope").status_code == 404


def test_healthz_bot_down_returns_503():
    cfg = _config()
    r = TestClient(create_app({"config": cfg, "state": {"bot_ok": False}})).get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "bot down"}


def test_api_requires_init_data():
    assert _client().get("/api/me").status_code == 403


def test_api_invalid_signature_forbidden():
    r = _client().get("/api/me", headers={"X-Telegram-Init-Data": make_init_data(CHILD_ID, token="bad")})
    assert r.status_code == 403


def test_api_outsider_forbidden():
    assert _client().get("/api/me", headers=_auth(999)).status_code == 403


def test_api_child_role():
    _setup_db()
    body = _client().get("/api/me", headers=_auth(CHILD_ID)).json()
    assert body["role"] == "child"
    assert body["child_name"] == "Motya"
    assert body["target_pef"] == 260


def test_api_parent_role():
    _setup_db()
    body = _client().get("/api/me", headers=_auth(PARENT_IDS[0])).json()
    assert body["role"] == "parent"


def test_api_status_and_summary_empty():
    _setup_db()
    body = _client().get("/api/status", headers=_auth(CHILD_ID)).json()
    assert body["today"] == []
    assert body["last"] is None
    assert body["target_pef"] == 260
    summary = _client().get("/api/summary", headers=_auth(CHILD_ID)).json()
    assert summary["today"] == []


def test_api_history_pagination():
    _setup_db()
    for i in range(3):
        add_measurement(TEST_DB, 200 + i, "morning", CHILD_ID, PARENT_IDS[0])
    body = _client().get("/api/history?page=1&per_page=2", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2
    # measured_at has second precision: don't assert intra-page order
    assert {it["pef_value"] for it in body["items"]} <= {200, 201, 202}


def test_api_status_has_today():
    _setup_db()
    add_measurement(TEST_DB, 240, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/status", headers=_auth(CHILD_ID)).json()
    assert len(body["today"]) == 1
    assert body["last"]["pef_value"] == 240


def test_api_chart_current_month_excludes_auto_and_has_zones():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    add_measurement(TEST_DB, 180, "evening", CHILD_ID, CHILD_ID, source="auto")
    body = _client().get("/api/chart", headers=_auth(CHILD_ID)).json()
    assert body["target_pef"] == 260
    assert body["zones"]["green"] == 80
    assert body["zones"]["yellow"] == 60
    assert len(body["points"]) == 1
    assert body["points"][0]["pef"] == 250
    assert body["points"][0]["tod"] == "morning"


def test_api_chart_explicit_month_and_nav():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/chart?year=2020&month=1", headers=_auth(CHILD_ID)).json()
    assert body["month"] == "2020-01"
    assert body["points"] == []
    assert body["can_next"] is True
    assert body["can_prev"] is False


def test_api_stats():
    _setup_db()
    for i in range(3):
        add_measurement(TEST_DB, 200 + i * 10, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/stats", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 3
    assert body["avg"] == 210
    assert body["target_pef"] == 260


def test_api_stats_empty():
    _setup_db()
    body = _client().get("/api/stats", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 0


def test_api_weekly_shape():
    _setup_db()
    body = _client().get("/api/weekly?offset=0", headers=_auth(CHILD_ID)).json()
    assert isinstance(body["this_week"], list)
    assert isinstance(body["prev_week"], list)


def test_static_index_served():
    r = _client().get("/")
    assert r.status_code == 200
    assert "telegram-web-app.js" in r.text
    assert 'id="app"' in r.text


def test_api_not_shadowed_by_static():
    assert _client().get("/api/me").status_code == 403


def test_static_index_has_form_overlay():
    r = _client().get("/")
    assert 'id="form-overlay"' in r.text


def test_static_index_has_settings_screen():
    r = _client().get("/")
    assert 'id="screen-settings"' in r.text
    assert 'data-screen="settings"' in r.text
    assert 'id="tab-settings" hidden' in r.text

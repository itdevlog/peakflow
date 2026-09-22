"""API Mini App через FastAPI TestClient (SP2a: чтение)."""
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
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
        CHILD_NAME="Motya", TARGET_PEF=260, TZ_OFFSET=0,
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


@pytest.fixture(autouse=True)
def _ensure_schema():
    """Every test in this module gets a schema-initialized DB, independent of
    execution order (a standalone run must not fail on a missing members table)."""
    init_db(TEST_DB)
    yield


def test_api_stranger_forbidden_when_db_uninitialized(tmp_path):
    """An uninitialized DB must yield 403, not a 500 from a failed lookup."""
    cfg = _config()
    cfg.DB_PATH = str(tmp_path / "empty.db")
    sqlite3.connect(cfg.DB_PATH).close()  # valid file, no members table
    assert _client(cfg).get("/api/me", headers=_auth(999)).status_code == 403


def test_healthz_ok():
    _setup_db()
    r = _client().get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body


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


def test_api_me_exposes_zone_thresholds():
    """Client must colour zones from server config, not hard-coded values."""
    _setup_db()
    body = _client().get("/api/me", headers=_auth(CHILD_ID)).json()
    assert body["zones"] == {"green": 80, "yellow": 60}


def test_api_me_exposes_children_and_active_child():
    """SP3C: the Mini App needs the child list to render its selector."""
    _setup_db()
    body = _client().get("/api/me", headers=_auth(CHILD_ID)).json()
    assert isinstance(body["children"], list)
    assert body["children"], "env-configured child must be listed"
    assert body["active_child_id"] == CHILD_ID
    assert body["children"][0]["telegram_id"] == CHILD_ID


def test_api_parent_role():
    _setup_db()
    body = _client().get("/api/me", headers=_auth(PARENT_IDS[0])).json()
    assert body["role"] == "parent"


def test_api_status_empty():
    _setup_db()
    body = _client().get("/api/status", headers=_auth(CHILD_ID)).json()
    assert body["today"] == []
    assert body["last"] is None
    assert body["target_pef"] == 260


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
    assert "red" not in body["zones"], "dead ZONE_RED must not be exposed"
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


def test_static_app_js_wires_child_selector():
    """SP3C: app.js stores the child list and calls the active-child API."""
    path = os.path.join(os.path.dirname(__file__), "..", "web", "static", "app.js")
    with open(path, encoding="utf-8") as f:
        js = f.read()
    assert "state.activeChildId" in js
    assert "/api/active-child" in js
    assert "Добавьте ребёнка в боте" in js


class TestWebRolesFromDb:
    def test_member_of_new_family_gets_role_from_db(self):
        """SP3C: the family-#1 gate is gone; family #2 members are served."""
        from database import create_family_with_owner
        _setup_db()
        create_family_with_owner(TEST_DB, 999, "Новые")
        r = _client().get("/api/me", headers=_auth(999))
        assert r.status_code == 200
        assert r.json()["role"] == "parent"

    def test_family_one_member_still_allowed(self):
        """Family #1 members resolved from the DB are unaffected by the gate."""
        from database import add_member
        _setup_db()
        add_member(TEST_DB, 555, 1, "parent", "Мама")
        r = _client().get("/api/me", headers=_auth(555))
        assert r.status_code == 200
        assert r.json()["role"] == "parent"

    def test_env_fallback_still_allowed(self):
        """An .env-only family #1 user (no members row) keeps working."""
        _setup_db()
        r = _client().get("/api/me", headers=_auth(CHILD_ID))
        assert r.status_code == 200

    def test_stranger_forbidden(self):
        _setup_db()
        r = _client().get("/api/me", headers=_auth(888))
        assert r.status_code == 403


def _family_two(owner=999, child=700, name="Маша"):
    """Create family #2 with one child; returns its family id."""
    from database import add_member, create_family_with_owner
    fid = create_family_with_owner(TEST_DB, owner, "Новые")
    add_member(TEST_DB, child, fid, "child", name)
    return fid


class TestWebTenantScoping:
    """SP3C: Mini App data must follow the caller's family and active child."""

    def test_children_endpoint_lists_family_children(self):
        _setup_db()
        _family_two()
        body = _client().get("/api/children", headers=_auth(999)).json()
        assert [c["telegram_id"] for c in body["children"]] == [700]
        assert body["active_child_id"] == 700

    def test_me_exposes_children_and_active_child(self):
        _setup_db()
        _family_two()
        body = _client().get("/api/me", headers=_auth(999)).json()
        assert body["role"] == "parent"
        assert body["active_child_id"] == 700
        assert [c["telegram_id"] for c in body["children"]] == [700]
        assert body["child_name"] == "Маша"

    def test_children_isolated_from_family_one(self):
        _setup_db()
        _family_two()
        body = _client().get("/api/children", headers=_auth(999)).json()
        assert all(c["family_id"] != 1 for c in body["children"])

    def test_status_scoped_to_active_child(self):
        from database import add_measurement
        _setup_db()
        fid = _family_two()
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        body = _client().get("/api/status", headers=_auth(999)).json()
        assert body["today"], "family #2 must see its own measurement"
        assert all(m["child_id"] == 700 for m in body["today"])
        assert body["last"]["child_id"] == 700

    def test_history_scoped_to_active_child(self):
        from database import add_measurement
        _setup_db()
        fid = _family_two()
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        add_measurement(TEST_DB, 220, "evening", 700, 700, family_id=fid)
        body = _client().get("/api/history?page=1&per_page=50", headers=_auth(999)).json()
        assert body["total"] == 2
        assert all(it["child_id"] == 700 for it in body["items"])

    def test_stats_scoped_to_active_child(self):
        from database import add_measurement
        _setup_db()
        fid = _family_two()
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        body = _client().get("/api/stats", headers=_auth(999)).json()
        assert body["total"] == 1
        assert body["avg"] == 210

    def test_settings_scoped_to_active_child(self):
        from database import add_measurement
        _setup_db()
        fid = _family_two()
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        body = _client().get("/api/settings", headers=_auth(999)).json()
        assert body["total"] == 1
        assert body["child_name"] == "Маша"

    def test_active_child_switch_changes_data(self):
        from database import add_member, add_measurement
        _setup_db()
        fid = _family_two()
        add_member(TEST_DB, 701, fid, "child", "Петя")
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        add_measurement(TEST_DB, 230, "morning", 701, 701, family_id=fid)
        c = _client()
        body = c.get("/api/status", headers=_auth(999)).json()
        assert all(m["child_id"] == 700 for m in body["today"])
        r = c.put("/api/active-child", json={"child_id": 701}, headers=_auth(999))
        assert r.status_code == 200, r.text
        body = c.get("/api/status", headers=_auth(999)).json()
        assert all(m["child_id"] == 701 for m in body["today"])

    def test_active_child_switch_parent_only(self):
        _setup_db()
        _family_two()
        r = _client().put("/api/active-child", json={"child_id": 700}, headers=_auth(700))
        assert r.status_code == 403

    def test_active_child_rejects_foreign_child(self):
        _setup_db()
        _family_two()
        r = _client().put("/api/active-child", json={"child_id": 111}, headers=_auth(999))
        assert r.status_code == 404

    def test_backup_contains_only_active_family(self):
        from database import add_measurement
        _setup_db()
        fid = _family_two()
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        add_measurement(TEST_DB, 210, "morning", 700, 700, family_id=fid)
        r = _client().get("/api/backup", headers=_auth(999))
        assert r.status_code == 200, r.text
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(r.content)
            conn = sqlite3.connect(path)
            try:
                fams = [row[0] for row in conn.execute("SELECT id FROM families")]
                meas = [row[0] for row in conn.execute("SELECT family_id FROM measurements")]
            finally:
                conn.close()
        finally:
            os.remove(path)
        assert fams == [fid]
        assert meas == [fid]

    def test_add_notifies_only_family_parents(self):
        from database import add_member
        _setup_db()
        fid = _family_two()
        add_member(TEST_DB, 998, fid, "parent", "Папа")
        bot = SimpleNamespace(send_message=AsyncMock())
        client = TestClient(create_app({"config": _config(), "bot": bot}))
        r = client.post("/api/measurements", json={"pef": 210}, headers=_auth(700))
        assert r.status_code == 200, r.text
        sent = sorted(call.args[0] for call in bot.send_message.await_args_list)
        assert sent == [998, 999]

    def test_no_children_yields_empty_data(self):
        from database import create_family_with_owner
        _setup_db()
        fid = create_family_with_owner(TEST_DB, 999, "Новые")
        assert fid != 1
        c = _client()
        me = c.get("/api/me", headers=_auth(999)).json()
        assert me["active_child_id"] is None
        assert me["children"] == []
        assert c.get("/api/status", headers=_auth(999)).json()["today"] == []
        assert c.get("/api/stats", headers=_auth(999)).json()["total"] == 0
        assert c.get("/api/history", headers=_auth(999)).json()["total"] == 0


class TestExportChartIsolation:
    """SP3D: chart/export endpoints must never leak another family's data."""

    @staticmethod
    def _family_two():
        from database import add_member, add_measurement, create_family_with_owner
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        add_measurement(TEST_DB, 250, "morning", 700, 999, family_id=f2)
        add_measurement(TEST_DB, 111, "morning", CHILD_ID, CHILD_ID, family_id=1)
        return f2

    def test_chart_isolated(self):
        _setup_db()
        self._family_two()
        body = _client().get("/api/chart", headers=_auth(999)).json()
        assert [p["pef"] for p in body["points"]] == [250]

    def test_export_csv_isolated(self):
        import csv
        import io
        _setup_db()
        self._family_two()
        r = _client().get("/api/export/csv", headers=_auth(999))
        assert r.status_code == 200, r.text
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        pef_values = [row[3] for row in rows if len(row) == 9 and row[3].isdigit()]
        assert pef_values == ["250"], "family #2 CSV leaked family #1 data"

    def test_export_periods_isolated(self):
        _setup_db()
        self._family_two()
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, ?, 200, 'morning', "
            "'2020-01-05 08:00:00', ?, 'manual')",
            (CHILD_ID, CHILD_ID),
        )
        conn.commit()
        conn.close()
        body = _client().get("/api/export/periods", headers=_auth(999)).json()
        assert body["months"], "family #2 must still expose its own month"
        assert "2020-01" not in body["months"], "family #1 month leaked into periods"


class TestReportPdfApi:
    def _family_two_without_data(self):
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        return f2

    def test_report_pdf_ok(self):
        _setup_db()
        add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID, family_id=1)
        r = _client().get("/api/report/pdf?period=month", headers=_auth(222))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content[:5] == b"%PDF-"

    def test_report_pdf_bad_period(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=year", headers=_auth(222))
        assert r.status_code == 422

    def test_report_pdf_child_forbidden(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(CHILD_ID))
        assert r.status_code == 403

    def test_report_pdf_empty(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(222))
        assert r.status_code == 404

    def test_report_pdf_isolation_no_leak(self):
        _setup_db()
        # family #1 has a measurement this month; family #2 has a child but no data
        add_measurement(TEST_DB, 111, "morning", CHILD_ID, CHILD_ID, family_id=1)
        self._family_two_without_data()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(999))
        assert r.status_code == 404, "family #2 must not receive family #1 data"

    def test_report_pdf_family_two_own_data(self):
        _setup_db()
        f2 = self._family_two_without_data()
        add_measurement(TEST_DB, 250, "morning", 700, 999, family_id=f2)
        r = _client().get("/api/report/pdf?period=month", headers=_auth(999))
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_report_pdf_isolated_same_child_id(self):
        _setup_db()
        self._family_two_without_data()
        # Family #1 row that reuses family #2's child id — must not leak.
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 700, 200, 'morning', ?, 700, 'manual')",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.commit()
        conn.close()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(999))
        assert r.status_code == 404, "family #2 must not see family #1 data via a shared child id"

    def test_app_js_has_report_button(self):
        import pathlib
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "/api/report/pdf" in js and "data-report" in js


class TestGamificationApi:
    def test_gamification_ok(self):
        _setup_db()
        conn = sqlite3.connect(TEST_DB)
        for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, ?, 250, 'morning', ?, ?, 'manual')",
                (CHILD_ID, f"{day} 08:00:00", CHILD_ID))
        conn.commit()
        conn.close()
        body = _client().get("/api/gamification", headers=_auth(222)).json()
        assert body["total"] == 3
        assert body["streak_longest"] == 3
        assert len(body["achievements"]) == 6
        codes = {a["code"] for a in body["achievements"]}
        assert {"streak_7", "total_100"} <= codes
        assert all(a["unlocked"] is False for a in body["achievements"])

    def test_gamification_no_child(self):
        _setup_db()
        from database import create_family_with_owner
        create_family_with_owner(TEST_DB, 999, "B")  # no children
        r = _client().get("/api/gamification", headers=_auth(999))
        assert r.status_code == 404

    def test_gamification_isolation(self):
        _setup_db()
        # Family #2 child reuses family #1's child id; family #1 has 100 rows.
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
                "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/gamification", headers=_auth(999)).json()
        assert body["total"] == 0
        assert all(a["unlocked"] is False for a in body["achievements"])

    def test_app_js_has_gamification(self):
        import pathlib
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "/api/gamification" in js and "Серия" in js


class TestMetrics:
    def _metric_config(self, enabled=True, token=""):
        cfg = _config()
        cfg.METRICS_ENABLED = enabled
        cfg.METRICS_TOKEN = token
        return cfg

    def test_healthz_fields(self):
        _setup_db()
        body = _client().get("/healthz").json()
        assert body["status"] == "ok"
        assert "uptime_seconds" in body
        assert body["families"] >= 1
        assert body["children"] is not None
        assert body["measurements"] is not None

    def test_healthz_bot_down(self):
        _setup_db()
        app = create_app({"config": _config(), "state": {"bot_ok": False}})
        r = TestClient(app).get("/healthz")
        assert r.status_code == 503
        assert r.json() == {"status": "bot down"}

    def test_metrics_disabled_404(self):
        _setup_db()
        assert _client(self._metric_config(enabled=False)).get("/metrics").status_code == 404

    def test_metrics_enabled(self):
        _setup_db()
        c = _client(self._metric_config(enabled=True))
        c.get("/healthz")  # middleware counts a prior request
        r = c.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in r.text

    def test_metrics_token_required(self):
        _setup_db()
        c = _client(self._metric_config(enabled=True, token="secret"))
        assert c.get("/metrics").status_code == 401
        r = c.get("/metrics", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200

    def test_request_counted(self):
        import metrics
        metrics.reset()
        _setup_db()
        _client().get("/healthz")
        assert metrics.get_counter("http_requests_total", method="GET", status="200") >= 1

    def test_middleware_counts_unhandled_500(self):
        import metrics
        from unittest.mock import patch
        metrics.reset()
        _setup_db()
        c = TestClient(create_app({"config": _config()}), raise_server_exceptions=False)
        with patch("web.api.get_stats", side_effect=RuntimeError("boom")):
            r = c.get("/api/stats", headers=_auth(222))
        assert r.status_code == 500
        assert metrics.get_counter("http_requests_total", method="GET", status="500") >= 1

    def test_healthz_db_failure_nulls(self, tmp_path):
        cfg = _config()
        cfg.DB_PATH = str(tmp_path / "nodir" / "x.db")
        r = _client(cfg).get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["families"] is None
        assert body["children"] is None
        assert body["measurements"] is None


class TestChartNote:
    def test_chart_includes_note(self):
        _setup_db()
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source, note) VALUES (1, ?, 250, 'morning', ?, ?, "
            "'manual', 'болел')",
            (CHILD_ID, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), CHILD_ID))
        conn.commit()
        conn.close()
        body = _client().get("/api/chart", headers=_auth(CHILD_ID)).json()
        assert body["points"][0]["note"] == "болел"

    def test_chart_note_empty_string(self):
        _setup_db()
        add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID, family_id=1)
        body = _client().get("/api/chart", headers=_auth(CHILD_ID)).json()
        assert body["points"][0]["note"] == ""


class TestMiniAppChartUi:
    def _js(self):
        import pathlib
        return pathlib.Path("web/static/app.js").read_text(encoding="utf-8")

    def _html(self):
        import pathlib
        return pathlib.Path("web/static/index.html").read_text(encoding="utf-8")

    def test_controls_present(self):
        html = self._html()
        assert "chart-controls" in html
        assert 'data-chart-type="line"' in html
        assert 'data-chart-type="bars"' in html
        assert 'data-chart-type="points"' in html
        assert 'data-chart-range="week"' in html
        assert 'data-chart-range="month"' in html

    def test_state_and_helpers(self):
        js = self._js()
        for token in ("chartType", "chartRange", "state.months",
                      "visiblePoints", "syncChartControls", "redrawChart",
                      'type === "bars"', 'type === "points"', "cutoff.setDate"):
            assert token in js

    def test_tooltip_full(self):
        js = self._js()
        assert "pointZone" in js
        assert "p.note" in js
        assert "л/мин" in js

    def test_ux_items(self):
        js = self._js()
        html = self._html()
        assert "auth-hint" in html and "auth-hint" in js
        assert "Нет доступа" in js
        assert "state.months" in js
        assert "note-save" in js and "disabled" in js

    def test_compare_ui(self):
        js = self._js()
        html = self._html()
        assert "chart-compare" in html and "chart-compare" in js
        assert "chartCompare" in js
        assert "drawCompare" in js and "/api/chart/compare" in js
        assert "resetCompare" in js
        assert "cutoff.setDate" in js
        assert 'state.chartRange = "month"' in js
        assert "Нет данных для сравнения" in js


class TestDailyAverageSeries:
    def test_average_per_day(self):
        from report import daily_average_series
        rows = [
            {"measured_at": "2026-09-21 08:00:00", "pef_value": 250},
            {"measured_at": "2026-09-21 20:00:00", "pef_value": 270},
            {"measured_at": "2026-09-22 08:00:00", "pef_value": 260},
        ]
        assert daily_average_series(rows, ["2026-09-21", "2026-09-22", "2026-09-23"]) == [260.0, 260.0, None]

    def test_empty_rows(self):
        from report import daily_average_series
        assert daily_average_series([], ["2026-09-21"]) == [None]



class TestChartCompare:
    def _today(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date()

    def _rand(self, day, pef):
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, ?, ?, 'morning', ?, ?, 'manual')",
            (CHILD_ID, pef, f"{day} 08:00:00", CHILD_ID))
        conn.commit()
        conn.close()

    def test_week_compare(self):
        _setup_db()
        import report_pdf
        today = self._today()
        cur_start, cur_end = report_pdf.period_bounds("week", today)
        from datetime import date, timedelta
        prev_day = (date.fromisoformat(cur_start) - timedelta(days=1)).isoformat()
        next_day = (date.fromisoformat(cur_start) + timedelta(days=1)).isoformat()
        self._rand(cur_start, 240)
        self._rand(next_day, 260)
        self._rand(prev_day, 200)
        body = _client().get("/api/chart/compare?period=week", headers=_auth(CHILD_ID)).json()
        assert body["period"] == "week"
        assert body["labels"] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        assert len(body["current"]) == 7 and len(body["previous"]) == 7
        assert body["current"][0] == 240.0
        assert body["current"][1] == 260.0
        assert body["current"][2] is None
        assert body["previous"][6] == 200.0  # prev Sunday

    def test_month_alignment(self):
        _setup_db()
        body = _client().get("/api/chart/compare?period=month", headers=_auth(CHILD_ID)).json()
        assert body["period"] == "month"
        assert len(body["labels"]) == len(body["current"]) == len(body["previous"])
        assert body["labels"][0] == "1"
        assert set(body) == {"period", "labels", "current", "previous",
                             "target_pef", "zones", "title"}

    def test_compare_bad_period(self):
        _setup_db()
        assert _client().get("/api/chart/compare?period=year",
                             headers=_auth(CHILD_ID)).status_code == 422

    def test_compare_isolation(self):
        _setup_db()
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        # family #1 row reusing family #2's child id must not leak
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
            "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/chart/compare?period=month", headers=_auth(999)).json()
        assert all(v is None for v in body["current"])
        assert all(v is None for v in body["previous"])


class TestPwaInstall:
    def test_manifest_valid(self):
        import json
        import pathlib
        data = json.loads(pathlib.Path("web/static/manifest.json").read_text(encoding="utf-8"))
        assert data["name"] and data["short_name"]
        assert data["start_url"]
        assert data["display"] == "standalone"
        assert data["icons"] and data["icons"][0]["type"] == "image/svg+xml"

    def test_icon_svg(self):
        import pathlib
        svg = pathlib.Path("web/static/icon.svg").read_text(encoding="utf-8")
        assert svg.lstrip().startswith("<svg")

    def test_index_links_and_registration(self):
        import pathlib
        html = pathlib.Path("web/static/index.html").read_text(encoding="utf-8")
        assert 'rel="manifest"' in html and "/manifest.json" in html
        assert 'name="theme-color"' in html
        assert "/icon.svg" in html
        assert 'navigator.serviceWorker.register("/sw.js")' in html


class TestServiceWorker:
    def test_sw_source(self):
        import pathlib
        js = pathlib.Path("web/static/sw.js").read_text(encoding="utf-8")
        for token in ("peakflow-v", "/api/", "skipWaiting", "clients.claim",
                      "addAll", '"navigate"'):
            assert token in js, f"sw.js missing {token!r}"
        assert '"/healthz"' in js and '"/metrics"' in js
        assert 'mode === "navigate"' in js
        assert 'networkFirst("/index.html")' in js, "offline navigation must fall back to the app shell"
        assert 'networkFirst' in js, "static requests must revalidate (network-first), not cache-first"
        assert "caches.match(request).then" not in js, "cache-first static lookup leaves clients stale"


class TestAnalyticsApi:
    def test_fields_and_values(self):
        _setup_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(TEST_DB)
        for v in (260, 240):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, ?, ?, 'morning', ?, ?, 'manual')",
                (CHILD_ID, v, now, CHILD_ID))
        conn.commit()
        conn.close()
        body = _client().get("/api/analytics", headers=_auth(CHILD_ID)).json()
        assert set(body) == {"zones", "weekday", "trend", "notes"}
        assert body["zones"]["green"] == 2
        assert len(body["weekday"]) == 7
        assert body["trend"]["n"] == 1
        assert body["trend"]["daily"][0]["avg"] == 250.0

    def test_no_child_404(self):
        _setup_db()
        from database import create_family_with_owner
        create_family_with_owner(TEST_DB, 999, "B")
        assert _client().get("/api/analytics", headers=_auth(999)).status_code == 404

    def test_isolation(self):
        _setup_db()
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
            "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/analytics", headers=_auth(999)).json()
        assert sum(body["zones"].values()) == 0
        assert body["trend"]["n"] == 0

    def test_ui_tokens(self):
        import pathlib
        html = pathlib.Path("web/static/index.html").read_text(encoding="utf-8")
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "screen-analytics" in html and 'data-screen="analytics"' in html
        for token in ("loadAnalytics", "drawPie", "drawHeatmap", "drawTrend", "/api/analytics"):
            assert token in js

    def test_notes_ui(self):
        import pathlib
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "notesCard" in js and "Заметки" in js


class TestNotesAnalytics:
    def test_notes_field(self):
        _setup_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(TEST_DB)
        for pef, note in ((250, ""), (200, "болел")):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source, note) VALUES (1, ?, ?, 'morning', ?, ?, "
                "'manual', ?)", (CHILD_ID, pef, now, CHILD_ID, note))
        conn.commit()
        conn.close()
        body = _client().get("/api/analytics", headers=_auth(CHILD_ID)).json()
        assert "notes" in body
        assert body["notes"]["baseline"]["count"] == 1
        sick = next(g for g in body["notes"]["groups"] if g["key"] == "sick")
        assert sick["count"] == 1 and sick["delta"] == -50.0


class TestDebtMetricsWeb:
    def test_measurement_counts_metric(self):
        import metrics
        metrics.reset()
        _setup_db()
        r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
        assert r.status_code == 200, r.text
        assert metrics.get_counter("measurements_saved_total") >= 1

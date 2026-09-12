"""Тесты для семейного бота пикфлоуметрии."""
import os
import pytest

TEST_DB = "test_peakflow.db"

@pytest.fixture(autouse=True)
def setup_db():
    os.environ["DB_PATH"] = TEST_DB
    for ext in ["", "-wal", "-shm"]:
        p = TEST_DB + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    import sqlite3
    conn = sqlite3.connect(TEST_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            pef_value INTEGER NOT NULL, time_of_day TEXT NOT NULL,
            measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, added_by INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders_sent (
            date TEXT PRIMARY KEY, morning_reminder INTEGER DEFAULT 0,
            evening_reminder INTEGER DEFAULT 0, weekly_report INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )
    """)
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_pef', '260')")
    conn.commit()
    conn.close()

    yield

    for ext in ["", "-wal", "-shm"]:
        p = TEST_DB + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


class TestDatabase:
    def test_add_and_get_last(self):
        from database import add_measurement, get_last_measurement
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_last_measurement(TEST_DB, 111)
        assert last["pef_value"] == 250
        assert last["time_of_day"] == "morning"
        assert last["id"] == mid

    def test_edit_measurement(self):
        from database import add_measurement, edit_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_last_measurement(TEST_DB, 111)
        # edit checks user_id of the measurement record (= CHILD_ID = 111)
        ok = edit_measurement(TEST_DB, last["id"], 280, 111)
        assert ok
        updated = get_last_measurement(TEST_DB, 111)
        assert updated["pef_value"] == 280

    def test_edit_wrong_user(self):
        from database import add_measurement, edit_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_last_measurement(TEST_DB, 111)
        ok = edit_measurement(TEST_DB, last["id"], 280, 999)  # wrong user
        assert not ok

    def test_delete_measurement(self):
        from database import add_measurement, delete_measurement, get_all_measurements
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_all_measurements(TEST_DB, 111)[0]
        ok = delete_measurement(TEST_DB, last["id"], 111)
        assert ok
        assert len(get_all_measurements(TEST_DB, 111)) == 0

    def test_today_measurements(self):
        from database import add_measurement, get_today_measurements, has_today_measurement
        add_measurement(TEST_DB, 240, "morning", 111, 222)
        add_measurement(TEST_DB, 260, "evening", 111, 222)

        today = get_today_measurements(TEST_DB, 111)
        assert len(today) == 2
        assert has_today_measurement(TEST_DB, 111, "morning")
        assert has_today_measurement(TEST_DB, 111, "evening")

    def test_has_today_measurement_false(self):
        from database import has_today_measurement
        assert not has_today_measurement(TEST_DB, 111, "morning")

    def test_stats(self):
        from database import add_measurement, get_stats
        for val, tod in [(240, "morning"), (260, "evening"), (250, "morning"), (270, "evening")]:
            add_measurement(TEST_DB, val, tod, 111, 222)

        stats = get_stats(TEST_DB, 111)
        assert stats["total"] == 4
        assert stats["avg"] == 255
        assert stats["min"] == 240
        assert stats["max"] == 270
        assert stats["latest"] == 270
        assert stats["morning_avg"] == 245
        assert stats["evening_avg"] == 265
        assert stats["trend"] is None  # need >= 6

    def test_stats_with_trend(self):
        from database import add_measurement, get_stats
        for val in [200, 210, 220, 230, 240, 250]:
            add_measurement(TEST_DB, val, "morning", 111, 222)

        stats = get_stats(TEST_DB, 111)
        # last 3: 250, 240, 230 → avg 240
        # prev 3: 220, 210, 200 → avg 210
        assert stats["trend"] == 30

    def test_last_two_weeks(self):
        from database import add_measurement, get_last_two_weeks
        # Just test it doesn crash with empty data
        this_w, prev_w = get_last_two_weeks(TEST_DB, 111)
        assert this_w == []
        assert prev_w == []

    def test_reminder_tracking(self):
        from database import mark_reminder_sent, was_reminder_sent
        today = "2026-04-13"
        assert not was_reminder_sent(TEST_DB, today, "morning_missing")
        mark_reminder_sent(TEST_DB, today, "morning_missing")
        assert was_reminder_sent(TEST_DB, today, "morning_missing")

    def test_pagination(self):
        from database import add_measurement, get_measurements_paginated
        for i in range(15):
            add_measurement(TEST_DB, 200 + i, "morning", 111, 222)

        p1, total, pages = get_measurements_paginated(TEST_DB, 111, page=1, per_page=10)
        assert len(p1) == 10
        assert total == 15
        assert pages == 2

        p2, _, _ = get_measurements_paginated(TEST_DB, 111, page=2, per_page=10)
        assert len(p2) == 5


class TestConfig:
    def test_is_parent(self):
        from config import is_parent
        # These will be 0 in test env
        assert not is_parent(123)

    def test_is_child(self):
        from config import is_child
        assert not is_child(123)

    def test_get_effective_target(self):
        from config import get_effective_target
        t = get_effective_target()
        assert isinstance(t, int)
        assert t > 0

    def test_auto_time_of_day(self):
        from bot import auto_time_of_day
        tod = auto_time_of_day()
        assert tod in ("morning", "evening")

    def test_pef_zone_green(self):
        from bot import pef_zone
        z, name = pef_zone(220, 260)
        assert z == "🟢"

    def test_pef_zone_yellow(self):
        from bot import pef_zone
        z, name = pef_zone(180, 260)
        assert z == "🟡"

    def test_pef_zone_red(self):
        from bot import pef_zone
        z, name = pef_zone(100, 260)
        assert z == "🔴"

    def test_pct_of(self):
        from bot import pct_of
        assert pct_of(208, 260) == 80
        assert pct_of(130, 260) == 50


class TestEditDeleteExport:
    """Tests for parent edit/delete/export functionality."""

    def test_direct_sql_edit_by_id(self):
        """Test direct SQL update by measurement ID (parent override)."""
        import sqlite3
        from database import add_measurement, get_last_measurement
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        # Simulate parent edit: update by ID and user_id
        conn = sqlite3.connect(TEST_DB)
        cur = conn.execute(
            "UPDATE measurements SET pef_value = ? WHERE id = ? AND user_id = ?",
            (300, mid, 111)
        )
        conn.commit()
        conn.close()
        assert cur.rowcount > 0
        last = get_last_measurement(TEST_DB, 111)
        assert last["pef_value"] == 300

    def test_direct_sql_delete_by_id(self):
        """Test direct SQL delete by measurement ID (parent override)."""
        import sqlite3
        from database import add_measurement, get_all_measurements
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        assert len(get_all_measurements(TEST_DB, 111)) == 1
        conn = sqlite3.connect(TEST_DB)
        cur = conn.execute(
            "DELETE FROM measurements WHERE id = ? AND user_id = ?",
            (mid, 111)
        )
        conn.commit()
        conn.close()
        assert cur.rowcount > 0
        assert len(get_all_measurements(TEST_DB, 111)) == 0

    def test_csv_export_format(self):
        """Test CSV export content format."""
        import sqlite3
        from database import add_measurement, get_all_measurements
        for val, tod in [(240, "morning"), (260, "evening"), (250, "morning")]:
            add_measurement(TEST_DB, val, tod, 111, 222)

        measurements = get_all_measurements(TEST_DB, 111)
        assert len(measurements) == 3

        # Simulate CSV generation logic
        lines = ["Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил"]
        for m in reversed(measurements):
            ts = m["measured_at"].replace("T", " ")
            date_part = ts[:10]
            time_part = ts[11:16]
            pct = int((m["pef_value"] / 260) * 100)
            lines.append(f"{date_part},{time_part},{'Утро' if m['time_of_day'] == 'morning' else 'Вечер'},{m['pef_value']},{pct}%,{'Зелёная' if pct >= 80 else 'Жёлтая'},Кто-то")

        assert "Дата,Время,Период" in lines[0]
        assert len(lines) == 4  # header + 3 measurements
        # Oldest first after reversal
        assert "240" in lines[1]
        assert "260" in lines[-1]

    def test_settings_get_set(self):
        """Test get_setting and set_setting."""
        from database import get_setting, set_setting
        assert get_setting(TEST_DB, "target_pef") == "260"
        set_setting(TEST_DB, "target_pef", "300")
        assert get_setting(TEST_DB, "target_pef") == "300"

    def test_settings_default_exists(self):
        """Test that default target_pef is created."""
        from database import get_setting
        val = get_setting(TEST_DB, "target_pef")
        assert val == "260"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

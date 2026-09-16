"""Тесты для семейного бота пикфлоуметрии."""
import os
import sqlite3
import pytest

TEST_DB = "test_peakflow.db"

@pytest.fixture(autouse=True)
def setup_db():
    os.environ["DB_PATH"] = TEST_DB
    for ext in ["", "-wal", "-shm", "-journal"]:
        p = TEST_DB + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    import sqlite3
    from database import init_db
    init_db(TEST_DB)  # single source of schema truth (incl. migrations, index)
    conn = sqlite3.connect(TEST_DB)
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_pef', '260')")
    conn.commit()
    conn.close()

    yield

    for ext in ["", "-wal", "-shm", "-journal"]:
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

    def test_edit_measurement_as_parent(self):
        """Regression for bug #1: parent's 'Edit last' must work.

        edit_measurement used to require user_id == caller_id, but the
        record's user_id is always CHILD_ID, so parents could never edit.
        The bot passes CHILD_ID explicitly now.
        """
        from database import add_measurement, edit_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_last_measurement(TEST_DB, 111)
        # 999 is a parent; the record belongs to child 111
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

    def test_get_reminder_hours_defaults(self):
        from database import get_reminder_hours
        h = get_reminder_hours(TEST_DB)
        assert h == {"child_morning": 8, "child_evening": 20,
                     "parent_morning": 10, "parent_evening": 22}

    def test_get_reminder_hours_override(self):
        from database import get_reminder_hours, set_setting
        set_setting(TEST_DB, "reminder_child_morning", "7")
        set_setting(TEST_DB, "reminder_parent_evening", "23")
        h = get_reminder_hours(TEST_DB)
        assert h["child_morning"] == 7
        assert h["parent_evening"] == 23
        assert h["child_evening"] == 20  # untouched default

    def test_get_reminder_hours_garbage(self):
        from database import get_reminder_hours, set_setting
        set_setting(TEST_DB, "reminder_child_morning", "abc")
        set_setting(TEST_DB, "reminder_child_evening", "25")  # out of range
        h = get_reminder_hours(TEST_DB)
        assert h["child_morning"] == 8
        assert h["child_evening"] == 20

    def test_backup_db(self):
        """Backup copy opens and contains all rows."""
        import sqlite3
        from database import add_measurement, backup_db, init_db
        for v in (240, 250, 260):
            add_measurement(TEST_DB, v, "morning", 111, 222)
        dest = "/tmp/opencode/backup_test.db"
        backup_db(TEST_DB, dest)
        conn = sqlite3.connect(dest)
        n = conn.execute("SELECT COUNT(*) FROM measurements WHERE user_id=111").fetchone()[0]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        os.remove(dest)
        assert n == 3
        assert "settings" in tables

    def test_get_measurements_for_month(self):
        from database import add_measurement, get_measurements_for_month
        # insert with explicit measured_at via raw SQL to control months
        conn = sqlite3.connect(TEST_DB)
        for d, v in [("2026-08-05 08:00:00", 240), ("2026-08-20 20:00:00", 250),
                     ("2026-09-01 08:00:00", 260)]:
            conn.execute(
                "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
                "VALUES (111, ?, ?, ?, 222, 'manual')",
                (v, "morning" if "08:00" in d else "evening", d))
        conn.commit()
        conn.close()

        aug = get_measurements_for_month(TEST_DB, 111, 2026, 8)
        sep = get_measurements_for_month(TEST_DB, 111, 2026, 9)
        assert len(aug) == 2
        assert len(sep) == 1
        assert all("2026-08" in m["measured_at"] for m in aug)

    def test_get_available_months(self):
        from database import get_available_months
        conn = sqlite3.connect(TEST_DB)
        for d in ["2026-07-15 08:00:00", "2026-08-01 08:00:00",
                  "2026-08-31 20:00:00", "2026-09-10 08:00:00"]:
            conn.execute(
                "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
                "VALUES (111, 240, 'morning', ?, 222, 'manual')", (d,))
        conn.commit()
        conn.close()

        months = get_available_months(TEST_DB, 111)
        assert months == [(2026, 7), (2026, 8), (2026, 9)]

    def test_get_measurements_between(self):
        from database import get_measurements_between
        conn = sqlite3.connect(TEST_DB)
        for d in ["2026-08-01 08:00:00", "2026-08-15 08:00:00",
                  "2026-08-31 23:00:00", "2026-09-05 08:00:00"]:
            conn.execute(
                "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
                "VALUES (111, 240, 'morning', ?, 222, 'manual')", (d,))
        conn.commit()
        conn.close()

        rows = get_measurements_between(TEST_DB, 111, "2026-08-01", "2026-08-31")
        assert len(rows) == 3  # inclusive both ends

    def test_reminder_flags_not_reset_by_other_type(self):
        """Bug regression: marking evening/weekly must not reset morning flag."""
        from database import mark_reminder_sent, was_reminder_sent
        today = "2026-04-13"
        mark_reminder_sent(TEST_DB, today, "morning_missing")
        mark_reminder_sent(TEST_DB, today, "evening_missing")
        mark_reminder_sent(TEST_DB, today, "weekly")
        assert was_reminder_sent(TEST_DB, today, "morning_missing")
        assert was_reminder_sent(TEST_DB, today, "evening_missing")
        assert was_reminder_sent(TEST_DB, today, "weekly")

    def test_schema_version_is_set(self):
        """init_db must record a schema version via PRAGMA user_version."""
        from database import init_db, SCHEMA_VERSION
        init_db(TEST_DB)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 1

    def test_migrate_old_schema_without_version(self):
        """A legacy DB (user_version=0, old columns) migrates to current."""
        import sqlite3
        from database import init_db, SCHEMA_VERSION
        # legacy schema: no CHECK, has note, no source; no settings/index
        conn = sqlite3.connect(TEST_DB)
        conn.execute("DROP TABLE IF EXISTS measurements")
        conn.execute("DROP TABLE IF EXISTS reminders_sent")
        conn.execute("DROP TABLE IF EXISTS settings")
        conn.execute(
            "CREATE TABLE measurements ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "pef_value INTEGER NOT NULL, time_of_day TEXT NOT NULL, "
            "measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, added_by INTEGER, note TEXT)"
        )
        conn.execute(
            "CREATE TABLE reminders_sent (date TEXT PRIMARY KEY, "
            "morning_reminder INTEGER DEFAULT 0, evening_reminder INTEGER DEFAULT 0, "
            "weekly_report INTEGER DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at) "
            "VALUES (111, 240, 'morning', '2026-01-01 08:00:00')"
        )
        conn.commit()
        conn.close()

        init_db(TEST_DB)  # must migrate in place without losing the row

        conn = sqlite3.connect(TEST_DB)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
        conn.close()
        assert version == SCHEMA_VERSION
        assert count == 1
        assert "source" in cols

    def test_init_db_idempotent(self):
        from database import init_db, SCHEMA_VERSION
        init_db(TEST_DB)
        init_db(TEST_DB)  # second run must not fail
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.close()

    def test_index_created(self):
        """init_db must create index on (user_id, measured_at)."""
        from database import init_db
        init_db(TEST_DB)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_meas_user_time'"
        ).fetchall()
        conn.close()
        assert rows, "index idx_meas_user_time missing"

    def test_migration_new_columns(self):
        """init_db must add child-reminder, auto-fill flags and measurements.source."""
        from database import init_db
        init_db(TEST_DB)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)

        for col in ("child_morning_reminder", "child_evening_reminder",
                    "auto_morning", "auto_evening"):
            row = conn.execute(
                "SELECT %s FROM reminders_sent LIMIT 1" % col
            )
            row.fetchone()  # raises OperationalError if column missing

        row = conn.execute("SELECT source FROM measurements LIMIT 1")
        row.fetchone()
        conn.close()

    def test_measurement_source_column_exists_in_prod_schema(self):
        """The prod DB (old schema) must be migratable: source column addable."""
        import sqlite3
        from database import init_db
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
        conn.close()
        assert "source" in cols
        # default for old rows is 'manual'
        from database import add_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        m = get_last_measurement(TEST_DB, 111)
        assert m["source"] == "manual"

    def test_set_note(self):
        """set_note writes note to a measurement; wrong user → False."""
        from database import add_measurement, get_last_measurement, set_note
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        assert set_note(TEST_DB, mid, "болел", 111)
        m = get_last_measurement(TEST_DB, 111)
        assert m["note"] == "болел"
        assert not set_note(TEST_DB, mid, "x", 999)  # wrong user

    def test_add_measurement_with_source(self):
        from database import add_measurement, get_last_measurement
        add_measurement(TEST_DB, 240, "morning", 111, 222, source="auto")
        m = get_last_measurement(TEST_DB, 111)
        assert m["source"] == "auto"

    def test_get_last_of_tod_skips_auto(self):
        """Last measurement of a time-of-day must be a real (manual) one."""
        from database import add_measurement, get_last_of_tod
        add_measurement(TEST_DB, 200, "morning", 111, 222)  # manual, oldest
        add_measurement(TEST_DB, 210, "morning", 111, 222, source="auto")
        m = get_last_of_tod(TEST_DB, 111, "morning")
        assert m["pef_value"] == 200 and m["source"] == "manual"

    def test_get_last_of_tod_none(self):
        from database import get_last_of_tod
        assert get_last_of_tod(TEST_DB, 111, "morning") is None

    def test_has_today_measurement_skips_auto(self):
        """Auto-carry record must not count as 'measured today'."""
        from database import add_measurement, has_today_measurement
        add_measurement(TEST_DB, 240, "morning", 111, 222, source="auto")
        assert not has_today_measurement(TEST_DB, 111, "morning", skip_auto=True)
        assert has_today_measurement(TEST_DB, 111, "morning", skip_auto=False)

    def test_stats_ignore_auto(self):
        from database import add_measurement, get_stats
        add_measurement(TEST_DB, 100, "morning", 111, 222, source="auto")
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        s = get_stats(TEST_DB, 111)
        assert s["total"] == 1
        assert s["avg"] == 250

    def test_chart_data_ignores_auto(self):
        from database import add_measurement, get_measurements_for_chart
        add_measurement(TEST_DB, 100, "morning", 111, 222, source="auto")
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        rows = get_measurements_for_chart(TEST_DB, 111, days=30)
        assert len(rows) == 1 and rows[0]["pef_value"] == 250

    def test_replace_auto_measurement(self):
        """Manual measurement replaces today's auto record, not a duplicate."""
        from database import (add_measurement, get_today_measurements,
                              replace_auto_measurement, add_measurement as am)
        # yesterday-ish auto record for today's morning slot
        add_measurement(TEST_DB, 230, "morning", 111, 222, source="auto")
        ok = replace_auto_measurement(TEST_DB, 111, "morning", 245, 333)
        assert ok
        today = get_today_measurements(TEST_DB, 111)
        assert len(today) == 1, "must replace, not duplicate"
        assert today[0]["pef_value"] == 245
        assert today[0]["source"] == "manual"

    def test_replace_auto_measurement_no_auto(self):
        """Without an auto record, replace returns False (caller decides)."""
        from database import replace_auto_measurement
        assert not replace_auto_measurement(TEST_DB, 111, "morning", 245, 333)

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


    def test_kb_pef_tens_has_back_button(self):
        """Tens keyboard must have a Back button (user can escape the step)."""
        from bot import kb_pef_tens
        kb = kb_pef_tens()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "back" in callbacks

    def test_kb_pef_hundreds_layout(self):
        from bot import kb_pef_hundreds
        kb = kb_pef_hundreds()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert callbacks == ["h_1", "h_2", "h_3", "h_4", "h_5", "h_6", "back"]

    def test_scheduler_reminder_window(self):
        """Reminder must fire for any tick within the deadline minute (0 or 1).

        Regression for bug: 'minute == 0' missed ticks at 10:01 when the
        loop's 60s sleep skipped the exact 10:00 tick.
        """
        from bot import is_reminder_minute
        for minute in (0, 1):
            assert is_reminder_minute(minute), f"minute={minute} must be inside the window"
        for minute in (2, 3, 15, 30, 59):
            assert not is_reminder_minute(minute), f"minute={minute} must be outside the window"


    def test_kb_main_child_has_chart_and_stats(self):
        """Child menu must include chart and stats buttons."""
        from bot import kb_main
        kb = kb_main(is_parent_user=False)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "add" in callbacks
        assert "chart" in callbacks
        assert "stats" in callbacks

    def test_kb_main_parent_buttons(self):
        from bot import kb_main
        kb = kb_main(is_parent_user=True)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        for cb in ("add", "history", "chart", "summary", "weekly", "settings", "edit_last"):
            assert cb in callbacks, f"missing {cb}"

    def test_kb_settings_contains_new_items(self):
        """Settings screen must offer reminders and backup."""
        from bot import kb_settings
        kb = kb_settings(target=260)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "reminders" in callbacks
        assert "backup" in callbacks
        assert "change_target" in callbacks
        assert "export" in callbacks

    def test_kb_main_child(self):
        from bot import kb_main
        kb = kb_main(is_parent_user=True)
        assert kb is not None


class TestNotes:
    """Notes flow: ask after save, store text, truncate long text."""

    def _make_callback(self, uid=999):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def _make_state(self, data=None):
        from unittest.mock import AsyncMock, MagicMock
        st = MagicMock()
        st._data = dict(data or {})

        async def update_data(**kw):
            st._data.update(kw)

        async def get_data():
            return dict(st._data)

        st.update_data = update_data
        st.get_data = get_data

        async def get_state():
            return None

        async def set_state(s):
            st._state = s

        st.get_state = get_state
        st.set_state = set_state
        st._state = None
        st.clear = AsyncMock()
        return st

    def test_save_measurement_asks_note(self):
        """After saving, bot must ask about a note instead of jumping to menu."""
        import asyncio
        import bot
        from database import add_measurement
        from unittest.mock import AsyncMock, MagicMock, patch

        cb = self._make_callback()
        state = self._make_state({"input_context": "add"})
        cb.message.text = "болел"

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            callback.sent_text = text
            return MagicMock()

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "send_main_menu", new=AsyncMock()), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "add_measurement", return_value=1) as m_add:
            asyncio.run(bot._save_measurement(cb, state, 240, {"input_context": "add"}))
            assert hasattr(cb, "sent_text")
            assert "заметку" in cb.sent_text.lower()
            # state must not be cleared — we're waiting for the note answer
            state.clear.assert_not_called()

    def test_note_handler_saves_note(self):
        """Text during waiting_note → set_note with CHILD_ID."""
        import asyncio
        import bot
        from database import add_measurement, get_last_measurement
        from unittest.mock import AsyncMock, MagicMock, patch

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)
        mid = get_last_measurement(TEST_DB, bot.CHILD_ID)["id"]

        cb = self._make_callback()
        cb.message.text = "болел"
        state = self._make_state({"note_for_id": mid})

        msg = MagicMock()
        msg.text = "болел"
        msg.answer = AsyncMock()

        async def fake_answer(text=None, parse_mode=None, reply_markup=None, **kw):
            msg.last_text = text

        msg.answer.side_effect = fake_answer

        with patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.input_note(msg, state))
        m = get_last_measurement(TEST_DB, bot.CHILD_ID)
        assert m["note"] == "болел"
        state.clear.assert_called()

    def test_note_truncated_to_200(self):
        import asyncio
        import bot
        from database import add_measurement, get_last_measurement
        from unittest.mock import AsyncMock, MagicMock, patch

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)
        mid = get_last_measurement(TEST_DB, bot.CHILD_ID)["id"]

        msg = MagicMock()
        msg.text = "а" * 350
        msg.answer = AsyncMock()
        state = self._make_state({"note_for_id": mid})

        with patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.input_note(msg, state))
        m = get_last_measurement(TEST_DB, bot.CHILD_ID)
        assert len(m["note"]) <= 200


class TestScheduler:
    """Scheduler logic: child pings, auto-carry, escalation."""

    def _run_tick(self, hour, minute, mocked_bot):
        """Run one scheduler tick with faked time; return the send_message calls."""
        import asyncio
        import bot
        from unittest.mock import patch
        from datetime import datetime, timedelta, timezone

        tz = timezone(timedelta(hours=5))
        fake_now = datetime(2026, 9, 12, hour, minute, 5, tzinfo=tz)  # Saturday
        tod = "morning" if hour < 12 else "evening"

        async def fake_sleep(s):
            raise asyncio.CancelledError  # stop loop after first tick

        with patch.object(bot, "now_tz", return_value=fake_now), \
             patch.object(bot, "bot", mocked_bot), \
             patch.object(bot, "get_reminder_hours",
                          return_value={"child_morning": 8, "child_evening": 20,
                                        "parent_morning": 10, "parent_evening": 22}), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "is_reminder_minute", return_value=minute < 2), \
             patch("asyncio.sleep", side_effect=fake_sleep), \
             patch("asyncio.create_task", lambda coro: coro):
            try:
                asyncio.run(bot.scheduler_loop())
            except asyncio.CancelledError:
                pass
        return mocked_bot.send_message.call_args_list

    def _mocked_bot(self):
        from unittest.mock import AsyncMock, MagicMock
        mb = MagicMock()
        mb.send_message = AsyncMock()
        return mb

    def test_child_morning_ping(self):
        """08:00 tick without morning measurement → message to child."""
        import bot
        mb = self._mocked_bot()
        calls = self._run_tick(8, 0, mb)
        recipients = [c[0][0] for c in calls]
        assert bot.CHILD_ID in recipients

    def test_escalation_with_auto_carry(self):
        """10:00 tick, no measurement → parents informed about auto-carry."""
        from unittest.mock import patch
        import bot
        mb = self._mocked_bot()
        with patch.object(bot, "get_last_of_tod",
                           return_value={"pef_value": 240}):
            calls = self._run_tick(10, 0, mb)
        texts = [c[0][1] for c in calls]
        assert any("скорректируйте" in t.lower() for t in texts)

    def test_escalation_no_auto_when_no_history(self):
        """No previous measurement → no auto record, plain 'напомните' message."""
        from unittest.mock import patch
        import bot
        mb = self._mocked_bot()
        with patch.object(bot, "get_last_of_tod", return_value=None):
            calls = self._run_tick(10, 0, mb)
        texts = [c[0][1] for c in calls]
        assert any("напомните" in t.lower() for t in texts)

    def test_child_ping_suppressed_when_measured(self):
        """08:00 with morning measurement done → nothing to child."""
        mb = self._mocked_bot()

        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, timedelta, timezone
        import bot
        tz = timezone(timedelta(hours=5))
        fake_now = datetime(2026, 9, 12, 8, 0, 5, tzinfo=tz)

        async def fake_sleep(s):
            raise asyncio.CancelledError

        with patch.object(bot, "now_tz", return_value=fake_now), \
             patch.object(bot, "bot", mb), \
             patch.object(bot, "get_reminder_hours",
                          return_value={"child_morning": 8, "child_evening": 20,
                                        "parent_morning": 10, "parent_evening": 22}), \
             patch.object(bot, "has_today_measurement", return_value=True), \
             patch.object(bot, "is_reminder_minute", return_value=True), \
             patch("asyncio.sleep", side_effect=fake_sleep), \
             patch("asyncio.create_task", lambda coro: coro):
            try:
                asyncio.run(bot.scheduler_loop())
            except asyncio.CancelledError:
                pass
        recipients = [c[0][0] for c in mb.send_message.call_args_list]
        assert bot.CHILD_ID not in recipients


class TestRemindersScreen:
    """⏰ Reminders settings screen + hour input FSM."""

    def test_build_reminders_text(self):
        from bot import build_reminders_text
        hours = {"child_morning": 8, "child_evening": 20,
                 "parent_morning": 10, "parent_evening": 22}
        text = build_reminders_text(hours)
        assert "08:00" in text
        assert "20:00" in text
        assert "10:00" in text
        assert "22:00" in text

    def test_kb_reminders(self):
        from bot import kb_reminders
        kb = kb_reminders()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        for cb in ("rem_set_child_morning", "rem_set_child_evening",
                   "rem_set_parent_morning", "rem_set_parent_evening", "settings"):
            assert cb in callbacks, f"missing {cb}"

    def test_input_reminder_hour_saves(self):
        """Digit input during editing_reminder_hour → set_setting('reminder_<key>')."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.text = "7"
        msg.answer = AsyncMock()

        state = MagicMock()
        state_data = {"reminder_key": "child_morning"}

        async def get_data():
            return state_data

        async def get_state():
            return bot.Measurement.editing_reminder_hour

        state.get_data = get_data
        state.get_state = get_state
        state.clear = AsyncMock()

        with patch.object(bot, "_send_settings_from_message", new=AsyncMock()):
            asyncio.run(bot.input_reminder_hour(msg, state))

        from database import get_setting
        assert get_setting(TEST_DB, "reminder_child_morning") == "7"

    def test_input_reminder_hour_invalid(self):
        """25 → error message, setting NOT saved."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.text = "25"
        msg.last_text = None
        msg.answer = AsyncMock()

        async def fake_answer(text=None, **kw):
            msg.last_text = text

        msg.answer.side_effect = fake_answer

        state = MagicMock()

        async def get_data():
            return {"reminder_key": "child_morning"}

        state.get_data = get_data
        state.clear = AsyncMock()

        with patch.object(bot, "_send_settings_from_message", new=AsyncMock()):
            asyncio.run(bot.input_reminder_hour(msg, state))
        assert "0–23" in msg.last_text
        state.clear.assert_not_called()

    def test_auto_replaced_by_manual_measurement(self):
        """cb_add flow: auto record in today's slot gets replaced, not duplicated."""
        import asyncio
        import bot
        from database import (add_measurement, get_today_measurements,
                              has_today_measurement)
        from unittest.mock import AsyncMock, MagicMock, patch

        # auto record in today's morning slot
        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 0, source="auto")
        assert not has_today_measurement(TEST_DB, bot.CHILD_ID, "morning", skip_auto=True)

        cb = MagicMock()
        cb.from_user.id = bot.CHILD_ID
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()

        state = MagicMock()
        sd = {"hundreds": 2}

        async def get_data():
            return sd

        state.get_data = get_data
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()
        state.clear = AsyncMock()

        # simulate tens selection with auto record present
        with patch.object(bot, "auto_time_of_day", return_value="morning"), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._save_measurement(cb, state, 245, {"input_context": "add"}))

        today = get_today_measurements(TEST_DB, bot.CHILD_ID)
        assert len(today) == 1, "auto must be replaced, not duplicated"
        assert today[0]["pef_value"] == 245
        assert today[0]["source"] == "manual"


class TestChartMonths:
    """Chart month navigation + PNG download."""

    def test_parse_chart_month(self):
        from bot import parse_chart_month
        y, m = parse_chart_month("chart_2026-08")
        assert (y, m) == (2026, 8)
        assert parse_chart_month("chart_bad") is None
        assert parse_chart_month("chart") is None

    def test_kb_chart_nav(self):
        from bot import kb_chart_nav
        kb = kb_chart_nav(year=2026, month=8, can_next=False)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "chart_2026-07" in callbacks  # prev month
        assert "chart_dl_2026-08" in callbacks  # download PNG
        assert "back" in callbacks
        assert "chart_2026-09" not in callbacks  # no next for the past month? (Aug is past, next=Sep allowed only if <= current)
        # can_next=True shows next
        kb2 = kb_chart_nav(year=2026, month=8, can_next=True)
        callbacks2 = [btn.callback_data for row in kb2.inline_keyboard for btn in row]
        assert "chart_2026-09" in callbacks2

    def test_month_title(self):
        from bot import month_title
        assert "Август" in month_title(2026, 8)
        assert "Сентябрь" in month_title(2026, 9)

    def test_render_chart_png(self):
        """Renderer produces non-empty PNG bytes."""
        from datetime import datetime
        from bot import _render_chart_png
        rows = [
            {"pef_value": 240, "time_of_day": "morning", "measured_at": "2026-08-05 08:00:00"},
            {"pef_value": 250, "time_of_day": "evening", "measured_at": "2026-08-06 20:00:00"},
            {"pef_value": 260, "time_of_day": "morning", "measured_at": "2026-08-07 08:00:00"},
        ]
        png = _render_chart_png(rows, target=260, title="Test")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 1000


class TestExportAndBackup:
    """CSV period screen + backup button."""

    def test_kb_export_periods(self):
        from bot import kb_export_periods
        kb = kb_export_periods(months=[(2026, 6), (2026, 7), (2026, 8)])
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "export_all" in callbacks
        assert "csv_2026-08" in callbacks
        assert "csv_2026-07" in callbacks
        assert "settings" in callbacks

    def test_build_csv_content_month(self):
        """CSV for a month includes note + source columns and only that month."""
        import bot
        from database import add_measurement, get_measurements_for_month

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

        # CSV export includes auto records (marked in 'Source' column)
        rows = get_measurements_for_month(TEST_DB, 111, 2026, 8, include_auto=True)
        content = bot.build_csv_content(rows, target=260, include_summary=False)
        assert "Заметка" in content and "Источник" in content
        assert "болел" in content
        assert "авто" in content
        assert "260" not in content  # September row excluded

    def test_parse_csv_month(self):
        from bot import parse_csv_month
        assert parse_csv_month("csv_2026-08") == (2026, 8)
        assert parse_csv_month("csv_x") is None

    def test_backup_button_flow(self):
        """cb_backup sends a document with the DB copy."""
        import asyncio
        import bot
        from database import add_measurement
        from unittest.mock import AsyncMock, MagicMock, patch

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)

        cb = MagicMock()
        cb.from_user.id = bot.PARENT_IDS[0] if bot.PARENT_IDS else 999
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer_document = AsyncMock()

        with patch.object(bot, "answer_callback", new=AsyncMock()):
            asyncio.run(bot.cb_backup(cb))

        assert cb.message.answer_document.called
        doc = cb.message.answer_document.call_args[0][0]
        assert doc.filename.endswith(".db")
        # cleanup temp file if left
        import glob
        for f in glob.glob("backup_*.db"):
            os.remove(f)


class TestConfig:
    def test_is_parent(self):
        from config import is_parent
        # These will be 0 in test env
        assert not is_parent(123)

    def test_is_child(self):
        from config import is_child
        assert not is_child(123)

    def test_get_effective_target(self):
        from bot import get_effective_target
        t = get_effective_target()
        assert isinstance(t, int)
        assert t > 0

    def test_get_effective_target_from_db(self):
        from database import get_effective_target, set_setting
        set_setting(TEST_DB, "target_pef", "333")
        assert get_effective_target(TEST_DB, 260) == 333
        set_setting(TEST_DB, "target_pef", "0")
        assert get_effective_target(TEST_DB, 260) == 260

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

    def test_edit_last_save_uses_child_id(self):
        """Regression for bug #1: _save_edit_last must pass CHILD_ID to
        edit_measurement, not the caller's Telegram ID (a parent)."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from database import add_measurement, get_last_measurement

        add_measurement(TEST_DB, 250, "morning", 111, 222)
        last = get_last_measurement(TEST_DB, 111)

        callback = MagicMock()
        callback.from_user.id = 999           # parent
        callback.answer = AsyncMock()
        callback.message.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(return_value={"edit_id": last["id"]})
        state.clear = AsyncMock()

        with patch("bot.edit_measurement") as mock_edit:
            mock_edit.return_value = True
            import bot
            asyncio.run(
                bot._save_edit_last(callback, state, 280, {"edit_id": last["id"]})
            )
            # The record's user_id (CHILD_ID) must be used, not the parent's ID
            args = mock_edit.call_args[0]
            assert args[1] == last["id"]
            assert args[3] == bot.CHILD_ID

    def test_answer_callback_tolerates_double_answer(self):
        """Regression for bug #4: a second callback.answer() raises
        TelegramBadRequest ('query already answered') — answer_callback
        must swallow it so the screen is still delivered."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from aiogram.exceptions import TelegramBadRequest
        import bot

        callback = MagicMock()
        callback.answer = AsyncMock(
            side_effect=TelegramBadRequest(method="answer", message="query is too old")
        )
        callback.message = MagicMock()
        callback.message.delete = AsyncMock()

        asyncio.run(bot.answer_callback(callback))
        callback.message.delete.assert_called_once()

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

    def test_delete_confirm_requires_parent(self):
        """Regression for bug #5: delete confirmation must check rights.

        cb_delete checks is_parent, but the confirming handler didn't —
        a callback 'del_confirm_<id>' from a non-parent would delete.
        The measurement must belong to CHILD_ID (as in production), so the
        DELETE query actually targets it.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        import bot
        from database import add_measurement, get_all_measurements

        # In production all measurements belong to CHILD_ID
        add_measurement(TEST_DB, 250, "morning", bot.CHILD_ID, 222)
        mid = get_all_measurements(TEST_DB, bot.CHILD_ID)[0]["id"]

        callback = MagicMock()
        callback.data = f"del_confirm_{mid}"
        callback.from_user.id = 555           # NOT a parent, NOT the child
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        state = MagicMock()
        state.clear = AsyncMock()

        asyncio.run(
            bot.cb_delete_confirm(callback, state)
        )

        from database import get_last_measurement
        assert get_last_measurement(TEST_DB, bot.CHILD_ID) is not None, \
            "non-parent must not be able to delete"
        callback.answer.assert_called()  # user gets feedback either way

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
        assert "250" in lines[-1]  # newest measurement is last

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


class TestCallbackParsing:
    """Phase 1.3: unsafe int(callback.data) must not crash handlers."""

    def test_parse_callback_int_valid(self):
        from bot import parse_callback_int
        assert parse_callback_int("edit_42", "edit_") == 42
        assert parse_callback_int("del_confirm_7", "del_confirm_") == 7
        assert parse_callback_int("h_6", "h_") == 6
        assert parse_callback_int("t_00", "t_") == 0

    def test_parse_callback_int_invalid_returns_none(self):
        from bot import parse_callback_int
        assert parse_callback_int("edit_abc", "edit_") is None
        assert parse_callback_int("edit_", "edit_") is None
        assert parse_callback_int("", "edit_") is None
        assert parse_callback_int(None, "edit_") is None
        assert parse_callback_int("edit_1;2", "edit_") is None


class TestDatabaseIdHelpers:
    """Phase 2.1: measurement lookup/update/delete by id via database.py."""

    def test_get_measurement_by_id(self):
        from database import add_measurement, get_measurement_by_id
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        row = get_measurement_by_id(TEST_DB, mid)
        assert row["id"] == mid
        assert row["pef_value"] == 250
        assert get_measurement_by_id(TEST_DB, 999999) is None


class TestNoteTargeting:
    """The note must attach to the exact row written/replaced."""

    def test_note_targets_replaced_auto_record(self):
        """When an auto record is replaced, the note must attach to that row,
        not to some other/newer measurement."""
        import asyncio
        import bot
        from database import (add_measurement, get_last_of_tod,
                              get_measurement_by_id)
        from unittest.mock import AsyncMock, MagicMock, patch

        # an auto record in today's morning slot
        auto_mid = add_measurement(TEST_DB, 230, "morning", bot.CHILD_ID, 0, source="auto")
        # a newer manual evening record exists too
        add_measurement(TEST_DB, 270, "evening", bot.CHILD_ID, 222)

        cb = MagicMock()
        cb.from_user.id = 222
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        state = MagicMock()

        async def get_data():
            return {}

        state.get_data = get_data
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()
        state.clear = AsyncMock()

        with patch.object(bot, "respond", new=AsyncMock(return_value=MagicMock())), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(cb, state, 250, "morning"))

        row = get_measurement_by_id(TEST_DB, auto_mid)
        assert row["pef_value"] == 250
        assert row["source"] == "manual"
        # note_for_id must point at the replaced row
        kwargs = state.update_data.await_args.kwargs
        assert kwargs.get("note_for_id") == auto_mid


class TestNoSilentInversion:
    """Phase 1.1: _save_measurement must not silently flip morning↔evening."""

    def _make_callback(self):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.from_user.id = 999
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def _make_state(self):
        from unittest.mock import AsyncMock, MagicMock
        st = MagicMock()

        async def get_data():
            return {}

        async def noop(*a, **kw):
            return None

        st.get_data = get_data
        st.update_data = AsyncMock()
        st.set_state = AsyncMock()
        st.clear = AsyncMock()
        st.set_state = noop
        return st

    def test_occupied_slot_without_forced_asks_instead_of_inverting(self):
        """When the auto slot is already taken (real entry), do not write the
        opposite time of day silently — ask the user to choose."""
        import asyncio
        import bot
        from database import add_measurement
        from unittest.mock import AsyncMock, MagicMock, patch

        # real morning entry already exists today
        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)

        cb = self._make_callback()
        state = self._make_state()

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            callback.sent_text = text
            return MagicMock()

        with patch.object(bot, "auto_time_of_day", return_value="morning"), \
             patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._save_measurement(cb, state, 245, {}))

        # No silent evening write: still exactly one measurement today
        from database import get_today_measurements
        today = get_today_measurements(TEST_DB, bot.CHILD_ID)
        assert len(today) == 1
        # And the user was asked what to do
        assert hasattr(cb, "sent_text")

    def test_forced_tod_writes_that_slot(self):
        """Explicit forced_tod (repeat measurement) still writes directly."""
        import asyncio
        import bot
        from database import add_measurement, get_today_measurements
        from unittest.mock import AsyncMock, MagicMock, patch

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)

        cb = self._make_callback()
        state = self._make_state()

        with patch.object(bot, "respond", new=AsyncMock(return_value=MagicMock())), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._save_measurement(cb, state, 245, {"forced_tod": "morning"}))

        today = get_today_measurements(TEST_DB, bot.CHILD_ID)
        assert len(today) == 2


class TestFsmRecovery:
    """Phase 1.4: FSM must not silently swallow input; /cancel must work."""

    def _make_state(self, state_name, data=None):
        from unittest.mock import AsyncMock, MagicMock
        st = MagicMock()
        st._data = dict(data or {})
        st._state = state_name

        async def update_data(**kw):
            st._data.update(kw)

        async def get_data():
            return dict(st._data)

        async def get_state():
            return st._state

        async def set_state(s):
            st._state = s

        st.update_data = update_data
        st.get_data = get_data
        st.get_state = get_state
        st.set_state = set_state
        st.clear = AsyncMock()
        return st

    def test_cancel_command_clears_state(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 999
        msg.answer = AsyncMock()
        state = self._make_state("Measurement:pef_input_tens")

        with patch.object(bot, "send_main_menu", new=AsyncMock()), \
             patch.object(bot, "is_parent", return_value=True):
            asyncio.run(bot.cmd_cancel(msg, state))

        state.clear.assert_called()
        assert any("отмен" in str(c.args[0]).lower() for c in msg.answer.call_args_list)

    def test_invalid_text_during_pef_input_prompts(self):
        """Non-numeric text mid-input must get a hint, not silence."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "привет"
        msg.answer = AsyncMock()
        state = self._make_state("Measurement:pef_input_tens", {"hundreds": 2})

        asyncio.run(bot.catch_all(msg, state))

        msg.answer.assert_called()
        assert "кнопк" in str(msg.answer.call_args[0][0]).lower()

    def test_invalid_text_during_target_edit_prompts(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "abc"
        msg.answer = AsyncMock()
        state = self._make_state("Measurement:editing_target_pef")

        asyncio.run(bot.catch_all(msg, state))

        msg.answer.assert_called()
        assert "число" in str(msg.answer.call_args[0][0]).lower() or \
            "100" in str(msg.answer.call_args[0][0])


class TestMarkdownEscaping:
    """Phase 2.3: underscore/asterisk in CHILD_NAME must not break Markdown."""

    def test_status_block_escapes_name(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import patch

        monkeypatch.setattr(bot, "CHILD_NAME", "Ма_ша")
        with patch.object(bot, "get_today_measurements", return_value=[]), \
             patch.object(bot, "get_all_measurements", return_value=[]), \
             patch.object(bot, "get_effective_target", return_value=260):
            text = asyncio.run(bot.build_status_block())

        assert "Ма\\_ша" in text
        assert "Ма_ша" not in text.replace("Ма\\_ша", "")

    def test_display_name_stays_raw(self):
        """_user_display_name feeds CSV/plain contexts — must stay unescaped."""
        import bot
        from config import CHILD_ID, CHILD_NAME
        assert bot._user_display_name(CHILD_ID) == CHILD_NAME


class TestMenuButton:
    def test_menu_button_set_when_url(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "https://pick.example.com")
        monkeypatch.setattr(bot, "WEBAPP_PORT", 8080)
        mock = AsyncMock()
        monkeypatch.setattr(bot.bot, "set_chat_menu_button", mock)
        asyncio.run(bot._setup_menu_button())
        mock.assert_awaited_once()
        assert mock.await_args.kwargs["menu_button"].web_app.url == "https://pick.example.com"

    def test_menu_button_skipped_without_url(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "")
        mock = AsyncMock()
        monkeypatch.setattr(bot.bot, "set_chat_menu_button", mock)
        asyncio.run(bot._setup_menu_button())
        mock.assert_not_awaited()

    def test_menu_button_error_is_swallowed(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "https://pick.example.com")
        monkeypatch.setattr(bot, "WEBAPP_PORT", 8080)
        monkeypatch.setattr(bot.bot, "set_chat_menu_button",
                            AsyncMock(side_effect=RuntimeError("boom")))
        asyncio.run(bot._setup_menu_button())  # must not raise


class TestEventLoopOffload:
    """Phase 1.2: the CPU-bound chart render must run off the event loop."""

    def test_render_chart_async_offloads_to_thread(self):
        import asyncio
        import threading
        import bot
        from unittest.mock import patch

        main_thread = threading.current_thread().name
        seen = {}

        def fake_render(rows, target, title):
            seen["thread"] = threading.current_thread().name
            seen["args"] = (rows, target, title)
            return b"PNG"

        rows = [
            {"measured_at": "2026-08-05 08:00:00", "pef_value": 240, "time_of_day": "morning"},
            {"measured_at": "2026-08-06 20:00:00", "pef_value": 250, "time_of_day": "evening"},
        ]

        with patch.object(bot, "_render_chart_png", side_effect=fake_render):
            result = asyncio.run(bot._render_chart_png_async(rows, 260, "Test"))

        assert result == b"PNG"
        assert seen["args"] == (rows, 260, "Test")
        assert seen["thread"] != main_thread, "render must run in a worker thread"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

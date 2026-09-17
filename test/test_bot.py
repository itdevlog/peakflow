"""Тесты для семейного бота пикфлоуметрии."""
import os
import sqlite3
import pytest

TEST_DB = "test_peakflow.db"

@pytest.fixture(autouse=True)
def setup_db():
    os.environ["DB_PATH"] = TEST_DB
    for ext in ["", "-wal", "-shm", "-journal", ".v1.bak"]:
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
    conn.execute(
        "INSERT OR IGNORE INTO settings (family_id, key, value) VALUES (1, 'target_pef', '260')"
    )
    conn.commit()
    conn.close()

    yield

    for ext in ["", "-wal", "-shm", "-journal", ".v1.bak"]:
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
        assert not was_reminder_sent(TEST_DB, today, "morning_missing", 111)
        mark_reminder_sent(TEST_DB, today, "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, today, "morning_missing", 111)

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
        n = conn.execute("SELECT COUNT(*) FROM measurements WHERE child_id=111").fetchone()[0]
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
                "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source) "
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
                "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source) "
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
                "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source) "
                "VALUES (111, 240, 'morning', ?, 222, 'manual')", (d,))
        conn.commit()
        conn.close()

        rows = get_measurements_between(TEST_DB, 111, "2026-08-01", "2026-08-31")
        assert len(rows) == 3  # inclusive both ends

    def test_reminder_flags_not_reset_by_other_type(self):
        """Bug regression: marking evening/weekly must not reset morning flag."""
        from database import mark_reminder_sent, was_reminder_sent
        today = "2026-04-13"
        mark_reminder_sent(TEST_DB, today, "morning_missing", 111)
        mark_reminder_sent(TEST_DB, today, "evening_missing", 111)
        mark_reminder_sent(TEST_DB, today, "weekly", 111)
        assert was_reminder_sent(TEST_DB, today, "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, today, "evening_missing", 111)
        assert was_reminder_sent(TEST_DB, today, "weekly", 111)

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
        """init_db must create index on (family_id, child_id, measured_at)."""
        from database import init_db
        init_db(TEST_DB)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_meas_family_child_time'"
        ).fetchall()
        conn.close()
        assert rows, "index idx_meas_family_child_time missing"

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

    def test_replace_auto_measurement_returns_replaced_id(self):
        """The replaced row's id is returned so callers need no fragile re-query."""
        from database import (replace_auto_measurement, get_measurement_by_id,
                              add_measurement as am)
        am(TEST_DB, 200, "morning", 111, 222)  # unrelated row, id 1
        auto_mid = am(TEST_DB, 230, "morning", 111, 0, source="auto")
        am(TEST_DB, 270, "evening", 111, 222)  # newer unrelated row
        returned = replace_auto_measurement(TEST_DB, 111, "morning", 245, 333)
        assert returned is not True and returned is not False
        assert returned == auto_mid
        assert get_measurement_by_id(TEST_DB, auto_mid)["pef_value"] == 245

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


class TestRedZoneAuthorExclusion:
    """Phase 0.5: the parent who entered a red-zone value must not alarm
    themselves. Informational notify_added already excludes the author;
    the red-zone alert must do the same."""

    def test_red_zone_skips_author_parent(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        author = bot.PARENT_IDS[0]
        other = bot.PARENT_IDS[1]

        cb = MagicMock()
        cb.from_user.id = author
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(return_value={})
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            return MagicMock()

        sent = []

        async def fake_send(pid, text, **kwargs):
            sent.append(pid)

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "replace_auto_measurement", return_value=False), \
             patch.object(bot, "add_measurement", return_value=1), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_recent_measurements", return_value=[]), \
             patch.object(bot.bot, "send_message", side_effect=fake_send):
            asyncio.run(bot._persist_measurement(cb, state, 120, "morning"))

        assert other in sent, "the other parent must be alerted"
        assert author not in sent, "the author must not alert themselves"


class TestSchedulerTaskReference:
    """Phase 0.6: on_startup must keep a strong reference to the scheduler
    task, otherwise asyncio may garbage-collect it mid-flight and any crash
    goes unnoticed."""

    def test_startup_stores_task_reference(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import MagicMock

        tasks = []

        def fake_create_task(coro):
            coro.close()
            task = MagicMock()
            task.add_done_callback = MagicMock()
            tasks.append(task)
            return task

        async def fake_menu_button():
            return None

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)
        monkeypatch.setattr(bot, "_setup_menu_button", fake_menu_button)
        monkeypatch.setattr(bot, "_scheduler_task", None, raising=False)

        asyncio.run(bot.on_startup())

        assert bot._scheduler_task is tasks[0]
        tasks[0].add_done_callback.assert_called_once()


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


class TestSchedulerAlignment:
    """Phase 1.4: ticks must align to the minute boundary and a failed send
    must not permanently swallow the reminder (flag only set on success)."""

    def test_seconds_until_next_minute(self):
        from datetime import datetime, timedelta, timezone
        import bot

        tz = timezone(timedelta(hours=5))
        assert bot.seconds_until_next_minute(datetime(2026, 9, 12, 8, 0, 0, tzinfo=tz)) == 60
        assert bot.seconds_until_next_minute(datetime(2026, 9, 12, 8, 0, 30, tzinfo=tz)) == 30
        assert bot.seconds_until_next_minute(datetime(2026, 9, 12, 8, 0, 59, tzinfo=tz)) >= 1

    def test_ping_flag_not_set_when_send_fails(self):
        """A blocking failure from the child must allow retry on the next tick."""
        import asyncio
        import bot
        from unittest.mock import patch

        async def failing_send(*a, **kw):
            raise RuntimeError("blocked")

        marked = []
        with patch.object(bot, "was_reminder_sent", return_value=False), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "mark_reminder_sent", side_effect=lambda *a: marked.append(a)) as m, \
             patch.object(bot.bot, "send_message", side_effect=failing_send):
            asyncio.run(bot._maybe_ping_child("morning", {"child_morning": 8}, 8, 0, "2026-09-12"))

        m.assert_not_called()
        assert marked == []

    def test_ping_flag_set_on_success(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        marked = []
        with patch.object(bot, "was_reminder_sent", return_value=False), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "mark_reminder_sent", side_effect=lambda *a: marked.append(a)), \
             patch.object(bot.bot, "send_message", new=AsyncMock()):
            asyncio.run(bot._maybe_ping_child("morning", {"child_morning": 8}, 8, 0, "2026-09-12"))

        assert marked, "flag must be set after a successful send"

    def test_escalation_flag_not_set_when_all_sends_fail(self):
        import asyncio
        import bot
        from unittest.mock import patch

        async def failing_send(*a, **kw):
            raise RuntimeError("blocked")

        marked = []
        with patch.object(bot, "was_reminder_sent", return_value=False), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "get_last_of_tod", return_value={"pef_value": 240}), \
             patch.object(bot, "add_measurement", return_value=1), \
             patch.object(bot, "mark_reminder_sent", side_effect=lambda *a: marked.append(a)), \
             patch.object(bot.bot, "send_message", side_effect=failing_send):
            asyncio.run(bot._escalate_parents("morning", {"parent_morning": 10}, 10, 0, "2026-09-12"))

        assert not any(a[2] == "morning_missing" for a in marked if len(a) >= 3), \
            "escalation flag must not be set if no parent got the message"


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

    def test_chart_download_routed_not_as_month(self):
        """Regression: 'chart_dl_YYYY-MM' must route to cb_chart_download,
        not be swallowed by cb_chart_month (which matches 'chart_' prefix)."""
        import asyncio
        from types import SimpleNamespace
        import bot

        handlers = {getattr(h.callback, "__name__", ""): h
                    for h in bot.router.callback_query.handlers}
        month_h = handlers["cb_chart_month"]
        dl_h = handlers["cb_chart_download"]

        def matches(h, data):
            ev = SimpleNamespace(data=data, text=None, from_user=None)

            async def run():
                return await h.filters[0].call(ev)
            return bool(asyncio.run(run()))

        payload = "chart_dl_2026-08"
        assert matches(dl_h, payload) is True
        assert not matches(month_h, payload), \
            "month handler must not capture the download callback"
        assert matches(month_h, "chart_2026-08") is True

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
            "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source, note) "
            "VALUES (111, 240, 'morning', '2026-08-05 08:00:00', 222, 'manual', 'болел')")
        conn.execute(
            "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source) "
            "VALUES (111, 250, 'evening', '2026-08-06 20:00:00', 222, 'auto')")
        conn.execute(
            "INSERT INTO measurements (child_id, pef_value, time_of_day, measured_at, added_by, source) "
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
            "UPDATE measurements SET pef_value = ? WHERE id = ? AND child_id = ?",
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
            "DELETE FROM measurements WHERE id = ? AND child_id = ?",
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


class TestSettingsFamilyScope:
    def test_settings_isolated_by_family(self):
        from database import set_setting, get_setting
        set_setting(TEST_DB, "target_pef", "300", family_id=1)
        set_setting(TEST_DB, "target_pef", "400", family_id=2)
        assert get_setting(TEST_DB, "target_pef", family_id=1) == "300"
        assert get_setting(TEST_DB, "target_pef", family_id=2) == "400"

    def test_effective_target_per_family(self):
        from database import set_setting, get_effective_target
        set_setting(TEST_DB, "target_pef", "333", family_id=1)
        assert get_effective_target(TEST_DB, 260, family_id=1) == 333
        assert get_effective_target(TEST_DB, 260, family_id=2) == 260

    def test_reminder_hours_per_family(self):
        from database import set_setting, get_reminder_hours
        set_setting(TEST_DB, "reminder_child_morning", "6", family_id=1)
        set_setting(TEST_DB, "reminder_child_morning", "9", family_id=2)
        assert get_reminder_hours(TEST_DB, family_id=1)["child_morning"] == 6
        assert get_reminder_hours(TEST_DB, family_id=2)["child_morning"] == 9


class TestRemindersFamilyScope:
    def test_reminders_isolated_by_child(self):
        from database import mark_reminder_sent, was_reminder_sent
        mark_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert not was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 222)

    def test_flags_not_reset_by_other_type_same_child(self):
        from database import mark_reminder_sent, was_reminder_sent
        mark_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        mark_reminder_sent(TEST_DB, "2026-09-17", "evening_missing", 111)
        mark_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)

    def test_child_id_is_required(self):
        import inspect
        from database import mark_reminder_sent, was_reminder_sent
        for fn in (mark_reminder_sent, was_reminder_sent):
            p = inspect.signature(fn).parameters["child_id"]
            assert p.default is inspect.Parameter.empty, \
                f"{fn.__name__}: child_id must be required (no default)"

    def test_migration_preserves_v1_flags(self):
        """v1 reminders_sent(date PK) migrates to (child_id, date) keeping flags.

        Historical rows are attributed to the configured active child when
        ``CHILD_ID`` is set (Task 5), else to the unknown child ``0``.
        """
        import sqlite3
        import config
        from database import init_db
        conn = sqlite3.connect(TEST_DB)
        conn.execute("DROP TABLE IF EXISTS reminders_sent")
        conn.execute(
            "CREATE TABLE reminders_sent (date TEXT PRIMARY KEY, "
            "morning_reminder INTEGER DEFAULT 0, weekly_report INTEGER DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO reminders_sent (date, morning_reminder, weekly_report) "
            "VALUES ('2026-01-01', 1, 1)"
        )
        conn.commit()
        conn.close()

        init_db(TEST_DB)

        conn = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(reminders_sent)")]
        row = conn.execute(
            "SELECT child_id, morning_reminder, weekly_report FROM reminders_sent "
            "WHERE date = '2026-01-01'"
        ).fetchone()
        conn.close()
        assert "child_id" in cols
        expected_child = getattr(config, "CHILD_ID", 0) or 0
        assert row == (expected_child, 1, 1)


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

    def test_pick_tod_writes_into_chosen_slot(self):
        """End-to-end for cb_pick_tod: user's explicit choice persists."""
        import asyncio
        import bot
        from database import add_measurement, get_today_measurements
        from unittest.mock import AsyncMock, MagicMock, patch

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)

        cb = self._make_callback()
        cb.data = "pick_tod_evening"

        state = MagicMock()
        store = {"pending_pef": 255}

        async def get_data():
            return dict(store)

        async def update_data(**kw):
            store.update(kw)

        async def noop(*a, **kw):
            return None

        state.get_data = get_data
        state.update_data = update_data
        state.set_state = noop
        state.clear = AsyncMock()

        with patch.object(bot, "respond", new=AsyncMock(return_value=MagicMock())), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.cb_pick_tod(cb, state))

        today = get_today_measurements(TEST_DB, bot.CHILD_ID)
        assert len(today) == 2
        evening = [m for m in today if m["time_of_day"] == "evening"]
        assert len(evening) == 1
        assert evening[0]["pef_value"] == 255

    def test_pick_tod_without_pending_value_alerts(self):
        """A pick_tod callback with no pending value must not write anything."""
        import asyncio
        import bot
        from database import add_measurement, get_today_measurements
        from unittest.mock import AsyncMock, MagicMock

        add_measurement(TEST_DB, 240, "morning", bot.CHILD_ID, 222)

        cb = self._make_callback()
        cb.data = "pick_tod_morning"
        cb.answer = AsyncMock()

        state = MagicMock()

        async def get_data():
            return {}

        state.get_data = get_data
        state.update_data = AsyncMock()
        state.clear = AsyncMock()

        asyncio.run(bot.cb_pick_tod(cb, state))

        cb.answer.assert_awaited()
        assert cb.answer.await_args.kwargs.get("show_alert") is True
        assert len(get_today_measurements(TEST_DB, bot.CHILD_ID)) == 1


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
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "привет"
        msg.answer = AsyncMock()
        state = self._make_state("Measurement:pef_input_tens", {"hundreds": 2})

        with patch.object(bot, "is_parent", return_value=True):
            asyncio.run(bot.catch_all(msg, state))

        msg.answer.assert_called()
        assert "кнопк" in str(msg.answer.call_args[0][0]).lower()

    def test_invalid_text_during_target_edit_prompts(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "abc"
        msg.answer = AsyncMock()
        state = self._make_state("Measurement:editing_target_pef")

        with patch.object(bot, "is_parent", return_value=True):
            asyncio.run(bot.catch_all(msg, state))

        msg.answer.assert_called()
        assert "число" in str(msg.answer.call_args[0][0]).lower() or \
            "100" in str(msg.answer.call_args[0][0])

    def test_stranger_text_gets_no_status(self):
        """A non-family user must not receive the child's status block."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock

        msg = MagicMock()
        msg.from_user.id = 424242  # stranger
        msg.text = "привет"
        msg.answer = AsyncMock()
        state = self._make_state(None)

        asyncio.run(bot.catch_all(msg, state))

        # Either an access warning or silence — but never the status block.
        for call in msg.answer.call_args_list:
            assert "Целевая" not in str(call.args[0])


class TestMarkdownEscaping:
    """Phase 2.3: underscore/asterisk in CHILD_NAME must not break Markdown."""

    def test_status_block_escapes_name(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import patch

        monkeypatch.setattr(bot, "CHILD_NAME", "Ма_ша")
        with patch.object(bot, "get_today_measurements", return_value=[]), \
             patch.object(bot, "get_recent_measurements", return_value=[]), \
             patch.object(bot, "get_effective_target", return_value=260):
            text = asyncio.run(bot.build_status_block())

        assert "Ма\\_ша" in text
        assert "Ма_ша" not in text.replace("Ма\\_ша", "")

    def test_display_name_stays_raw(self):
        """_user_display_name feeds CSV/plain contexts — must stay unescaped."""
        import bot
        from config import CHILD_ID, CHILD_NAME
        assert bot._user_display_name(CHILD_ID) == CHILD_NAME

    def test_history_line_escapes_note(self):
        """A note with Markdown chars must be escaped in the history line."""
        from bot import _history_line
        m = {"pef_value": 240, "time_of_day": "morning",
             "measured_at": "2026-08-05 08:00:00", "added_by": 222,
             "note": "болел_сильно *слабость*"}
        line = _history_line(m, 260)
        assert "болел\\_сильно" in line
        assert "\\*слабость\\*" in line


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


class TestRecentMeasurements:
    """Phase 1.2: status/diff only needs the last few rows, not full history."""

    def test_returns_latest_n_desc(self):
        from database import add_measurement, get_recent_measurements
        for v in (200, 210, 220, 230):
            add_measurement(TEST_DB, v, "morning", 111, 222)
        rows = get_recent_measurements(TEST_DB, 111, 2)
        assert [r["pef_value"] for r in rows] == [230, 220]

    def test_excludes_auto_and_other_users(self):
        from database import add_measurement, get_recent_measurements
        add_measurement(TEST_DB, 999, "morning", 111, 222)
        add_measurement(TEST_DB, 180, "evening", 111, 0, source="auto")
        add_measurement(TEST_DB, 500, "morning", 999, 222)
        rows = get_recent_measurements(TEST_DB, 111, 5)
        assert [r["pef_value"] for r in rows] == [999]

    def test_empty(self):
        from database import get_recent_measurements
        assert get_recent_measurements(TEST_DB, 111, 2) == []


class TestAddOrReplaceAtomic:
    """Phase 0.3: adding a measurement must be a single atomic transaction.

    check-then-insert across separate connections is a TOCTOU race (two
    requests can both see the slot free). add_or_replace_measurement does the
    check, optional auto-replace and insert under one BEGIN IMMEDIATE.
    """

    def test_creates_measurement(self):
        from database import add_or_replace_measurement, get_all_measurements
        mid, status = add_or_replace_measurement(TEST_DB, 240, "morning", 111, 222)
        assert status == "ok"
        assert mid
        rows = get_all_measurements(TEST_DB, 111)
        assert len(rows) == 1 and rows[0]["pef_value"] == 240

    def test_duplicate_slot_without_force_returns_exists(self):
        from database import add_or_replace_measurement, get_all_measurements
        first_id, _ = add_or_replace_measurement(TEST_DB, 240, "morning", 111, 222)
        dup_id, status = add_or_replace_measurement(TEST_DB, 250, "morning", 111, 222)
        assert status == "exists"
        assert dup_id == first_id
        assert len(get_all_measurements(TEST_DB, 111)) == 1

    def test_force_allows_second_measurement(self):
        from database import add_or_replace_measurement, get_all_measurements
        add_or_replace_measurement(TEST_DB, 240, "morning", 111, 222)
        mid, status = add_or_replace_measurement(TEST_DB, 250, "morning", 111, 222, force=True)
        assert status == "ok"
        assert len(get_all_measurements(TEST_DB, 111)) == 2

    def test_replaces_today_auto_record(self):
        from database import (add_or_replace_measurement, add_measurement,
                              get_all_measurements)
        auto_id = add_measurement(TEST_DB, 180, "morning", 111, 0, source="auto")
        mid, status = add_or_replace_measurement(TEST_DB, 250, "morning", 111, 222)
        assert status == "ok"
        assert mid == auto_id
        rows = get_all_measurements(TEST_DB, 111, include_auto=True)
        assert len(rows) == 1
        assert rows[0]["pef_value"] == 250 and rows[0]["source"] == "manual"

    def test_concurrent_requests_create_one_row(self):
        import threading
        from database import add_or_replace_measurement, get_all_measurements

        barrier = threading.Barrier(2)
        results = []

        def worker(value):
            barrier.wait()
            results.append(add_or_replace_measurement(TEST_DB, value, "morning", 111, 222))

        threads = [threading.Thread(target=worker, args=(v,)) for v in (240, 250)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(status for _, status in results) == ["exists", "ok"]
        assert len(get_all_measurements(TEST_DB, 111)) == 1


class TestHistoryPageClamp:
    """Phase 1.6: a stale page number (after deletions) must not render
    an empty history screen — it clamps to the last available page."""

    def test_stale_page_falls_back_to_last(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        calls = []

        def fake_paginated(db, uid, page=1, per_page=10):
            calls.append(page)
            if page > 2:
                return [], 3, 2
            return [{"id": 1, "pef_value": 240, "time_of_day": "morning",
                     "measured_at": "2026-09-01 08:00:00", "added_by": 222,
                     "note": None, "source": "manual"}], 3, 2

        cb = MagicMock()
        cb.from_user.id = 222
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.delete = AsyncMock()
        cb.message.answer = AsyncMock()

        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "get_measurements_paginated", side_effect=fake_paginated), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "respond", side_effect=fake_respond):
            asyncio.run(bot._show_history(cb, page=99))

        assert 2 in calls, "out-of-range page must be clamped to total_pages"
        assert "История" in sent.get("text", "")


class TestSessionClose:
    """Phase 1.8: bot.session must be closed when polling stops, with or
    without the web server (otherwise aiohttp leaks an unclosed session)."""

    def test_session_closed_without_webapp(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock

        closed = AsyncMock()
        monkeypatch.setattr(bot, "WEBAPP_PORT", 0)
        monkeypatch.setattr(bot.dp, "start_polling", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.bot.session, "close", closed)

        asyncio.run(bot.run_async())

        closed.assert_awaited()


class TestReminderHourValidation:
    """Phase 1.12: parent escalation must come after the child ping, never at
    the same minute (parent_morning > child_morning, parent_evening > child_evening)."""

    def test_valid_ordering(self):
        from database import validate_reminder_hours
        assert validate_reminder_hours(
            {"child_morning": 8, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
        ) is None

    def test_equal_hours_rejected(self):
        from database import validate_reminder_hours
        err = validate_reminder_hours(
            {"child_morning": 8, "child_evening": 20, "parent_morning": 8, "parent_evening": 22}
        )
        assert err

    def test_parent_before_child_rejected(self):
        from database import validate_reminder_hours
        err = validate_reminder_hours(
            {"child_morning": 8, "child_evening": 20, "parent_morning": 7, "parent_evening": 21}
        )
        assert err

    def test_bot_handler_rejects_equal_parent_morning(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.text = "8"
        msg.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(return_value={"reminder_key": "parent_morning"})
        state.clear = AsyncMock()

        with patch.object(bot, "get_reminder_hours",
                          return_value={"child_morning": 8, "child_evening": 20,
                                        "parent_morning": 10, "parent_evening": 22}), \
             patch.object(bot, "set_setting", new=AsyncMock()) as m_set:
            asyncio.run(bot.input_reminder_hour(msg, state))

        m_set.assert_not_called()
        msg.answer.assert_awaited()
        assert "позже" in msg.answer.await_args.args[0].lower() or \
               "больше" in msg.answer.await_args.args[0].lower()


class TestDiffSameTod:
    """Phase 1.10: the "change" must compare against the previous measurement
    of the *same* time of day (morning↔morning), not just the previous row."""

    def test_previous_same_tod(self):
        from database import add_measurement, get_previous_of_tod
        add_measurement(TEST_DB, 240, "morning", 111, 222)
        add_measurement(TEST_DB, 200, "evening", 111, 222)
        last_id = add_measurement(TEST_DB, 250, "morning", 111, 222)
        prev = get_previous_of_tod(TEST_DB, 111, "morning", last_id)
        assert prev["pef_value"] == 240

    def test_previous_excludes_auto(self):
        from database import add_measurement, get_previous_of_tod
        add_measurement(TEST_DB, 240, "morning", 111, 222)
        add_measurement(TEST_DB, 999, "morning", 111, 0, source="auto")
        last_id = add_measurement(TEST_DB, 250, "morning", 111, 222)
        prev = get_previous_of_tod(TEST_DB, 111, "morning", last_id)
        assert prev["pef_value"] == 240

    def test_none_when_no_previous_real(self):
        from database import add_measurement, get_previous_of_tod
        add_measurement(TEST_DB, 999, "morning", 111, 0, source="auto")
        last_id = add_measurement(TEST_DB, 250, "morning", 111, 222)
        assert get_previous_of_tod(TEST_DB, 111, "morning", last_id) is None


class TestSingletonLock:
    """Phase 0.1: the lock must stay held after acquire_lock() returns.

    Regression: acquire_lock() returned only the fd (an int); the file object
    was dropped and its refcount hit zero at function exit, closing the fd and
    releasing the flock — so a second bot instance could start.
    """

    def test_lock_survives_after_acquire_returns(self, tmp_path, monkeypatch):
        import gc
        import fcntl
        import bot

        lock_path = str(tmp_path / "peakflow.lock")
        monkeypatch.setattr(bot, "LOCK_FILE", lock_path)

        handle = bot.acquire_lock()
        gc.collect()  # would drop a dangling file object and release the lock

        other = open(lock_path, "w")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            other.close()

        bot.release_lock(handle)

    def test_second_acquire_is_refused(self, tmp_path, monkeypatch):
        import bot

        lock_path = str(tmp_path / "peakflow.lock")
        monkeypatch.setattr(bot, "LOCK_FILE", lock_path)

        handle = bot.acquire_lock()
        try:
            with pytest.raises(SystemExit):
                bot.acquire_lock()
        finally:
            bot.release_lock(handle)


class TestRoleChecks:
    """Phase 0.2: export/summary/weekly callbacks must require a parent.

    A child could receive or forward a keyboard with these callbacks and pull
    the full CSV or family summary. Every parent-only handler must reject a
    non-parent caller at execution time (show_alert), not just hide the button.
    """

    NON_PARENT = 555

    def _callback(self, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = self.NON_PARENT
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def _state(self):
        from unittest.mock import AsyncMock, MagicMock
        st = MagicMock()
        st.clear = AsyncMock()
        return st

    @pytest.mark.parametrize("handler,data", [
        ("cb_export", "export"),
        ("cb_export_all", "export_all"),
        ("cb_export_month", "csv_2026-09"),
        ("cb_summary", "summary"),
        ("cb_weekly", "weekly"),
    ])
    def test_parent_only_handler_rejects_non_parent(self, handler, data):
        import asyncio
        import bot

        cb = self._callback(data)
        asyncio.run(getattr(bot, handler)(cb))
        cb.message.answer.assert_not_called()
        cb.answer.assert_awaited()

    def test_edit_last_rejects_non_parent(self):
        """B7: the 'fix last' button leaks into the add-flow for a child."""
        import asyncio
        import bot

        cb = self._callback("edit_last")
        asyncio.run(bot.cb_edit_last(cb, self._state()))
        cb.message.answer.assert_not_called()
        cb.answer.assert_awaited()


class TestChartFigureCleanup:
    """Phase 1.5: a render failure must not leak the matplotlib figure."""

    def test_figure_closed_on_render_error(self):
        import bot
        from unittest.mock import MagicMock, patch

        rows = [
            {"measured_at": "2026-08-05 08:00:00", "pef_value": 240, "time_of_day": "morning"},
            {"measured_at": "2026-08-06 20:00:00", "pef_value": 250, "time_of_day": "evening"},
        ]

        fig = MagicMock()
        fig.savefig.side_effect = RuntimeError("boom")
        ax = MagicMock()

        closed = []
        with patch.object(bot.plt, "subplots", return_value=(fig, ax)), \
             patch.object(bot.plt, "close", side_effect=lambda f=None: closed.append(f)):
            with pytest.raises(RuntimeError, match="boom"):
                bot._render_chart_png(rows, 260, "Test")

        assert fig in closed, "figure must be closed even when rendering raises"


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


class TestFamiliesAndMembers:
    def test_create_and_get_family(self):
        from database import create_family, get_family
        fid = create_family(TEST_DB, "Ивановы")
        fam = get_family(TEST_DB, fid)
        assert fam["name"] == "Ивановы"
        assert get_family(TEST_DB, 99999) is None

    def test_add_and_get_member(self):
        from database import create_family, add_member, get_member
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        m = get_member(TEST_DB, 222)
        assert m["family_id"] == fid and m["role"] == "parent" and m["name"] == "Олег"
        assert get_member(TEST_DB, 555) is None

    def test_add_member_is_upsert(self):
        from database import create_family, add_member, get_member
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        add_member(TEST_DB, 222, fid, "parent", "Олег Петров")
        assert get_member(TEST_DB, 222)["name"] == "Олег Петров"

    def test_list_family_children(self):
        from database import create_family, add_member, list_family_children
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 111, fid, "child", "Маша")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        add_member(TEST_DB, 333, fid, "child", "Петя")
        kids = list_family_children(TEST_DB, fid)
        assert sorted(k["name"] for k in kids) == ["Маша", "Петя"]

    def test_default_family_constants(self):
        import database
        assert database.DEFAULT_FAMILY_ID == 1
        assert database.SCHEMA_VERSION == 2


class TestMeasurementV2:
    def test_child_id_column_and_family_scope(self):
        import sqlite3
        from database import init_db, add_measurement
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
        conn.close()
        assert "child_id" in cols and "user_id" not in cols
        assert "family_id" in cols

    def test_measurements_are_isolated_by_family(self):
        from database import add_measurement, get_all_measurements
        add_measurement(TEST_DB, 240, "morning", 111, 222, family_id=1)
        add_measurement(TEST_DB, 300, "morning", 111, 222, family_id=2)
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=1)] == [240]
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=2)] == [300]

    def test_add_and_get_last_default_family(self):
        from database import add_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        m = get_last_measurement(TEST_DB, 111)
        assert m["pef_value"] == 250 and m["family_id"] == 1 and m["child_id"] == 111

    def test_has_today_isolated_by_family(self):
        from database import add_measurement, has_today_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222, family_id=1)
        assert has_today_measurement(TEST_DB, 111, "morning", family_id=1)
        assert not has_today_measurement(TEST_DB, 111, "morning", family_id=2)

    def test_paginated_isolated_by_family(self):
        from database import add_measurement, get_measurements_paginated
        add_measurement(TEST_DB, 250, "morning", 111, 222, family_id=1)
        add_measurement(TEST_DB, 300, "morning", 111, 222, family_id=2)
        items, total, pages = get_measurements_paginated(TEST_DB, 111, 1, 10, family_id=1)
        assert total == 1 and items[0]["pef_value"] == 250

    def test_measurements_index_created(self):
        import sqlite3
        from database import init_db
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_meas_family_child_time'"
        ).fetchall()
        conn.close()
        assert rows, "index idx_meas_family_child_time missing"


class TestMigrationV2:
    def _make_v1_db(self):
        import sqlite3
        from database import init_db
        for ext in ["", "-wal", "-shm", "-journal"]:
            import os
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, pef_value INTEGER NOT NULL, "
            "time_of_day TEXT NOT NULL, measured_at TIMESTAMP, added_by INTEGER, note TEXT)"
        )
        conn.execute(
            "CREATE TABLE reminders_sent (date TEXT PRIMARY KEY, morning_reminder INTEGER DEFAULT 0, "
            "evening_reminder INTEGER DEFAULT 0, weekly_report INTEGER DEFAULT 0)"
        )
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, "
                     "role TEXT, child_name TEXT, age INTEGER, target_pef INTEGER, created_at TIMESTAMP, "
                     "reminder_morning INTEGER, reminder_evening INTEGER)")
        conn.execute("INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by) "
                     "VALUES (111, 240, 'morning', '2026-01-01 08:00:00', 222)")
        conn.execute("INSERT INTO users (user_id, first_name, role, child_name) VALUES (111, 'Маша', 'child', 'Маша')")
        conn.execute("INSERT INTO users (user_id, first_name, role) VALUES (222, 'Олег', 'parent')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('target_pef', '300')")
        conn.execute("INSERT INTO reminders_sent (date, morning_reminder) VALUES ('2026-01-01', 1)")
        conn.commit()
        conn.close()

    def test_migration_preserves_measurements_and_settings(self):
        import sqlite3
        from database import init_db, SCHEMA_VERSION
        self._make_v1_db()
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        row = conn.execute("SELECT family_id, child_id, pef_value FROM measurements").fetchone()
        setting = conn.execute("SELECT value FROM settings WHERE key='target_pef' AND family_id=1").fetchone()
        conn.close()
        assert version == SCHEMA_VERSION == 2
        assert row == (1, 111, 240)
        assert setting[0] == "300"

    def test_migration_seeds_family_from_legacy_users(self):
        from database import init_db, get_member, get_family
        self._make_v1_db()
        init_db(TEST_DB)
        assert get_family(TEST_DB, 1) is not None
        assert get_member(TEST_DB, 111)["role"] == "child"
        assert get_member(TEST_DB, 222)["role"] == "parent"

    def test_migration_unions_legacy_users_and_config(self, monkeypatch):
        """Config CHILD_ID/PARENT_IDS seed even when legacy users exist."""
        import config
        from database import init_db, get_member
        self._make_v1_db()
        monkeypatch.setattr(config, "CHILD_ID", 1727847144)
        monkeypatch.setattr(config, "PARENT_IDS", [35641953, 704630847])
        monkeypatch.setattr(config, "CHILD_NAME", "Матвей")
        init_db(TEST_DB)

        # Legacy members are still present with their roles.
        assert get_member(TEST_DB, 111)["role"] == "child"
        assert get_member(TEST_DB, 222)["role"] == "parent"
        # Config members are seeded too (union, not either/or).
        assert get_member(TEST_DB, 1727847144)["role"] == "child"
        assert get_member(TEST_DB, 1727847144)["name"] == "Матвей"
        assert get_member(TEST_DB, 35641953)["role"] == "parent"
        assert get_member(TEST_DB, 704630847)["role"] == "parent"

    def test_migration_config_role_wins_over_stale_legacy(self, monkeypatch):
        """Config lists an ID as parent; stale legacy users calls it child.

        Config (env) is authoritative for every ID it lists, because legacy
        `users` can be stale (prod: 35641953 owns 126 measurements but is
        recorded as a child).
        """
        import config
        from database import init_db, get_member
        self._make_v1_db()
        # Add the stale legacy row: 35641953 marked as child.
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO users (user_id, first_name, role) VALUES (35641953, 'Матвей', 'child')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(config, "CHILD_ID", 1727847144)
        monkeypatch.setattr(config, "PARENT_IDS", [35641953, 704630847])
        monkeypatch.setattr(config, "CHILD_NAME", "Motya")
        init_db(TEST_DB)
        assert get_member(TEST_DB, 35641953)["role"] == "parent"
        assert get_member(TEST_DB, 704630847)["role"] == "parent"
        assert get_member(TEST_DB, 1727847144)["role"] == "child"

    def test_migration_legacy_only_member_kept(self, monkeypatch):
        """A legacy member absent from config is seeded with its legacy role."""
        import config
        from database import init_db, get_member
        self._make_v1_db()
        # 333 is legacy-only and not listed in config.
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO users (user_id, first_name, role) VALUES (333, 'Бабушка', 'parent')"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(config, "CHILD_ID", 1727847144)
        monkeypatch.setattr(config, "PARENT_IDS", [35641953])
        monkeypatch.setattr(config, "CHILD_NAME", "Motya")
        init_db(TEST_DB)
        assert get_member(TEST_DB, 333)["role"] == "parent"
        assert get_member(TEST_DB, 333)["name"] == "Бабушка"

    def test_migration_is_idempotent(self):
        from database import init_db
        self._make_v1_db()
        init_db(TEST_DB)
        init_db(TEST_DB)  # must not fail or duplicate
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        assert conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM families").fetchone()[0] == 1
        conn.close()

    def test_backup_created_before_migration(self, tmp_path):
        import os
        from unittest.mock import patch
        import database
        self._make_v1_db()
        with patch.object(database, "backup_db") as m:
            database.init_db(TEST_DB)
            m.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

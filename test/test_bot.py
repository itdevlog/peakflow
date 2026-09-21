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

    def test_backup_db(self, tmp_path):
        """Backup copy opens and contains all rows."""
        import sqlite3
        from database import add_measurement, backup_db, init_db
        for v in (240, 250, 260):
            add_measurement(TEST_DB, v, "morning", 111, 222)
        dest = str(tmp_path / "backup_test.db")
        backup_db(TEST_DB, dest)
        conn = sqlite3.connect(dest)
        n = conn.execute("SELECT COUNT(*) FROM measurements WHERE child_id=111").fetchone()[0]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
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

    def test_red_zone_skips_author_parent(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        # _family_parents reads members from the DB (seeded from .env), so patch
        # it directly: in CI there is no .env and config defaults to
        # PARENT_IDS=[0,0], where author and other would collide.
        author, other = 222, 333

        async def fake_parents(member, family_id):
            return [author, other]

        monkeypatch.setattr(bot, "_family_parents", fake_parents)

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


class TestMultiFamilyScheduler:
    """SP3D Task 4: one scheduler tick must serve every family, with each
    family's own reminder hours and no cross-family notifications."""

    @staticmethod
    def _mocked_bot():
        from unittest.mock import AsyncMock, MagicMock
        mb = MagicMock()
        mb.send_message = AsyncMock()
        return mb

    @staticmethod
    def _make_family(child_id, child_name, parent_id, **hours):
        from database import create_family, add_member, set_setting
        fid = create_family(TEST_DB, f"family-{child_id}")
        add_member(TEST_DB, child_id, fid, "child", child_name)
        add_member(TEST_DB, parent_id, fid, "parent", "Родитель")
        for key, value in hours.items():
            set_setting(TEST_DB, f"reminder_{key}", str(value), family_id=fid)
        return fid

    def _run_tick(self, families, hour, minute, mocked_bot):
        """One scheduler tick with faked time; returns send_message calls."""
        import asyncio
        import bot
        from unittest.mock import patch
        from datetime import datetime, timedelta, timezone

        tz = timezone(timedelta(hours=5))
        # 2026-09-13 is a Sunday (weekly report day).
        fake_now = datetime(2026, 9, 13, hour, minute, 5, tzinfo=tz)

        async def fake_sleep(_s):
            raise asyncio.CancelledError  # stop loop after first tick

        with patch.object(bot, "now_tz", return_value=fake_now), \
             patch.object(bot, "list_families",
                          return_value=[{"id": fid} for fid in families],
                          create=True), \
             patch.object(bot, "bot", mocked_bot), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "is_reminder_minute", return_value=minute < 2), \
             patch.object(bot, "get_last_of_tod", return_value=None), \
             patch("asyncio.sleep", side_effect=fake_sleep), \
             patch("asyncio.create_task", lambda coro: coro):
            try:
                asyncio.run(bot.scheduler_loop())
            except asyncio.CancelledError:
                pass
        return mocked_bot.send_message.call_args_list

    def test_list_families_returns_all(self):
        from database import list_families
        f2 = self._make_family(700, "Маша", 701)
        ids = [f["id"] for f in list_families(TEST_DB)]
        assert 1 in ids, "seeded default family must be listed"
        assert f2 in ids

    def test_two_families_pinged_at_own_hours(self):
        f2 = self._make_family(700, "Маша", 701, child_morning=8)
        f3 = self._make_family(800, "Петя", 801, child_morning=9)

        at8 = [c[0][0] for c in self._run_tick([f2, f3], 8, 0, self._mocked_bot())]
        assert 700 in at8, "family 2's child must be pinged at its own 08:00"
        assert 800 not in at8, "family 3's child must not be pinged at 08:00"

        at9 = [c[0][0] for c in self._run_tick([f2, f3], 9, 0, self._mocked_bot())]
        assert 800 in at9, "family 3's child must be pinged at its own 09:00"
        assert 700 not in at9, "family 2's child must not be pinged at 09:00"

    def test_escalation_only_notifies_its_own_family(self):
        f2 = self._make_family(700, "Маша", 701, child_morning=8, parent_morning=10)
        f3 = self._make_family(800, "Петя", 801, child_morning=8, parent_morning=11)

        recipients = [c[0][0]
                      for c in self._run_tick([f2, f3], 10, 0, self._mocked_bot())]
        assert 701 in recipients, "family 2's parent must be escalated at 10:00"
        assert 801 not in recipients, "family 3's parent must not get family 2's escalation"

    def test_weekly_report_delivered_per_family(self):
        from database import add_measurement
        f2 = self._make_family(700, "Маша", 701)
        f3 = self._make_family(800, "Петя", 801)
        add_measurement(TEST_DB, 250, "morning", 700, 701, family_id=f2)
        add_measurement(TEST_DB, 240, "evening", 700, 701, family_id=f2)
        add_measurement(TEST_DB, 300, "morning", 800, 801, family_id=f3)
        add_measurement(TEST_DB, 310, "evening", 800, 801, family_id=f3)

        by_recipient = {}
        for c in self._run_tick([f2, f3], 21, 0, self._mocked_bot()):
            by_recipient.setdefault(c[0][0], []).append(c[0][1])

        assert any("Маша" in t for t in by_recipient.get(701, [])), "family 2 report"
        assert any("Петя" in t for t in by_recipient.get(801, [])), "family 3 report"
        assert not any("Петя" in t for t in by_recipient.get(701, [])), "no leak into f2"
        assert not any("Маша" in t for t in by_recipient.get(801, [])), "no leak into f3"

    def test_weekly_report_sent_per_child_within_a_family(self):
        """Weekly is per child (controller ruling): a family with two children
        gets one report per child, delivered to that family's parents only."""
        from database import add_measurement, add_member
        f2 = self._make_family(700, "Маша", 701)
        add_member(TEST_DB, 710, f2, "child", "Саша")
        f3 = self._make_family(800, "Петя", 801)
        for child_id in (700, 710):
            add_measurement(TEST_DB, 250, "morning", child_id, 701, family_id=f2)
            add_measurement(TEST_DB, 240, "evening", child_id, 701, family_id=f2)
        add_measurement(TEST_DB, 300, "morning", 800, 801, family_id=f3)
        add_measurement(TEST_DB, 310, "evening", 800, 801, family_id=f3)

        by_recipient = {}
        for c in self._run_tick([f2, f3], 21, 0, self._mocked_bot()):
            by_recipient.setdefault(c[0][0], []).append(c[0][1])

        f2_reports = by_recipient.get(701, [])
        assert len(f2_reports) == 2, "one weekly report per child of the family"
        assert sum("Маша" in t for t in f2_reports) == 1, "child 700's report"
        assert sum("Саша" in t for t in f2_reports) == 1, "child 710's report"
        assert not any("Петя" in t for t in f2_reports), "no cross-family leak"

        f3_reports = by_recipient.get(801, [])
        assert len(f3_reports) == 1, "family 3 has a single child"
        assert "Петя" in f3_reports[0]
        assert not any("Маша" in t or "Саша" in t for t in f3_reports)


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


class TestMeasurementByIdChildScope:
    """SP3D: optional child_id narrows a by-id lookup to the active child."""

    def test_child_scope_hides_sibling(self):
        from database import (create_family_with_owner, add_member,
                              add_measurement, get_measurement_by_id)
        fid = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, fid, "child", "Маша")
        add_member(TEST_DB, 701, fid, "child", "Петя")
        mid_a = add_measurement(TEST_DB, 240, "morning", 700, 500, family_id=fid)
        assert get_measurement_by_id(TEST_DB, mid_a, family_id=fid, child_id=700)["pef_value"] == 240
        assert get_measurement_by_id(TEST_DB, mid_a, family_id=fid, child_id=701) is None

    def test_no_child_id_keeps_family_behavior(self):
        from database import add_measurement, get_measurement_by_id
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        assert get_measurement_by_id(TEST_DB, mid)["pef_value"] == 250


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

    def test_display_name_stays_raw(self, monkeypatch):
        """_user_display_name feeds CSV/plain contexts — must stay unescaped."""
        import bot
        # CI has no .env (CHILD_ID=0, PARENT_IDS=[0,0]); make roles explicit.
        monkeypatch.setattr(bot, "is_child", lambda uid: uid == 111)
        monkeypatch.setattr(bot, "is_parent", lambda uid: uid in (222, 333))
        monkeypatch.setattr(bot, "CHILD_NAME", "Ма_ша")
        assert bot._user_display_name(111) == "Ма_ша"

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
        assert database.SCHEMA_VERSION == 5


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
        assert version == SCHEMA_VERSION == 5
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

    def test_needs_migration_true_on_locked_db(self):
        """A busy/locked DB must trigger a backup rather than be skipped (F1)."""
        from database import _needs_migration

        class LockedProbe:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("database is locked")

        assert _needs_migration(LockedProbe()) is True

    def test_needs_migration_false_on_malformed_file(self):
        """A non-SQLite file is not migration-worthy (F1, no crash)."""
        import os
        from database import _needs_migration
        for ext in ["", "-wal", "-shm", "-journal"]:
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)
        with open(TEST_DB, "wb") as f:
            f.write(b"this is not a sqlite database")
        probe = sqlite3.connect(TEST_DB)
        try:
            assert _needs_migration(probe) is False
        finally:
            probe.close()

    def test_rebuild_preserves_existing_family_id(self):
        """v1 measurements that already carry family_id keep it (F3)."""
        import os
        import sqlite3
        from database import init_db
        for ext in ["", "-wal", "-shm", "-journal"]:
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "family_id INTEGER, user_id INTEGER NOT NULL, pef_value INTEGER NOT NULL, "
            "time_of_day TEXT NOT NULL, measured_at TIMESTAMP, added_by INTEGER, note TEXT)"
        )
        conn.execute(
            "INSERT INTO measurements (family_id, user_id, pef_value, time_of_day, measured_at) "
            "VALUES (7, 111, 240, 'morning', '2026-01-01 08:00:00')"
        )
        conn.commit()
        conn.close()
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        row = conn.execute("SELECT family_id, child_id FROM measurements").fetchone()
        conn.close()
        assert row == (7, 111)


class TestFamilyIsolation:
    def test_two_families_do_not_see_each_other(self):
        from database import (create_family, add_measurement,
                              set_setting, mark_reminder_sent,
                              get_all_measurements, get_stats, get_setting,
                              get_today_measurements,
                              was_reminder_sent)
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        add_measurement(TEST_DB, 240, "morning", 111, 111, family_id=f1)
        add_measurement(TEST_DB, 300, "morning", 111, 111, family_id=f2)
        set_setting(TEST_DB, "target_pef", "240", family_id=f1)
        set_setting(TEST_DB, "target_pef", "400", family_id=f2)
        mark_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)

        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=f1)] == [240]
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=f2)] == [300]
        assert get_stats(TEST_DB, 111, family_id=f1)["total"] == 1
        assert get_setting(TEST_DB, "target_pef", family_id=f1) == "240"
        assert get_setting(TEST_DB, "target_pef", family_id=f2) == "400"
        assert was_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)
        assert get_today_measurements(TEST_DB, 111, family_id=f2)[0]["pef_value"] == 300

    def test_delete_only_touches_own_family(self):
        from database import create_family, add_measurement, delete_measurement, get_all_measurements
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        mid1 = add_measurement(TEST_DB, 240, "morning", 111, 111, family_id=f1)
        mid2 = add_measurement(TEST_DB, 300, "morning", 111, 111, family_id=f2)
        assert delete_measurement(TEST_DB, mid2, 111, family_id=f1) is False
        assert len(get_all_measurements(TEST_DB, 111, family_id=f2)) == 1
        assert delete_measurement(TEST_DB, mid1, 111, family_id=f1) is True


class TestInvites:
    def test_schema_version_is_5(self):
        import database
        assert database.SCHEMA_VERSION == 5

    def test_create_and_get_invite(self):
        from database import create_family, create_invite, get_invite
        fid = create_family(TEST_DB, "Семья")
        token = create_invite(TEST_DB, fid, "parent")
        inv = get_invite(TEST_DB, token)
        assert inv["family_id"] == fid and inv["role"] == "parent"
        assert get_invite(TEST_DB, "nope") is None

    def test_child_card_has_name(self):
        from database import create_family, create_invite, list_child_cards
        fid = create_family(TEST_DB, "Семья")
        create_invite(TEST_DB, fid, "child", "Маша")
        cards = list_child_cards(TEST_DB, fid)
        assert len(cards) == 1 and cards[0]["name"] == "Маша"

    def test_tokens_are_unique(self):
        from database import create_family, create_invite
        fid = create_family(TEST_DB, "Семья")
        tokens = {create_invite(TEST_DB, fid, "child", f"c{i}") for i in range(20)}
        assert len(tokens) == 20

    def test_delete_invite_invalidates(self):
        from database import create_family, create_invite, get_invite, delete_invite
        fid = create_family(TEST_DB, "Семья")
        token = create_invite(TEST_DB, fid, "parent")
        assert delete_invite(TEST_DB, token, fid) is True
        assert get_invite(TEST_DB, token) is None
        assert delete_invite(TEST_DB, token, fid) is False

    def test_delete_invite_scoped_to_family(self):
        """F2: a token cannot be deleted through a different family's scope."""
        from database import create_family, create_invite, get_invite, delete_invite
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        token = create_invite(TEST_DB, f2, "child", "Маша")
        assert delete_invite(TEST_DB, token, f1) is False
        assert get_invite(TEST_DB, token) is not None
        assert delete_invite(TEST_DB, token, f2) is True
        assert get_invite(TEST_DB, token) is None

    def test_regenerate_family_invite_replaces(self):
        from database import (create_family, create_invite, get_family_invite,
                              regenerate_family_invite, get_invite)
        fid = create_family(TEST_DB, "Семья")
        old = create_invite(TEST_DB, fid, "parent")
        new = regenerate_family_invite(TEST_DB, fid)
        assert new != old
        assert get_invite(TEST_DB, old) is None
        assert get_family_invite(TEST_DB, fid)["token"] == new

    def test_get_family_invite_none_when_absent(self):
        from database import create_family, get_family_invite
        fid = create_family(TEST_DB, "Семья")
        assert get_family_invite(TEST_DB, fid) is None


class TestRegistrationAccessors:
    def test_create_family_with_owner(self):
        from database import create_family_with_owner, get_member, get_family_invite
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        m = get_member(TEST_DB, 500)
        assert m["family_id"] == fid and m["role"] == "parent" and m["name"] == "Ивановы"
        assert get_family_invite(TEST_DB, fid) is not None

    def test_create_family_idempotent(self):
        from database import create_family_with_owner, get_member
        fid1 = create_family_with_owner(TEST_DB, 500, "Ивановы")
        fid2 = create_family_with_owner(TEST_DB, 500, "Другое")
        assert fid1 == fid2
        assert get_member(TEST_DB, 500)["family_id"] == fid1

    def test_join_parent_invite(self):
        from database import create_family_with_owner, get_family_invite, join_by_invite, get_member
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = get_family_invite(TEST_DB, fid)["token"]
        res = join_by_invite(TEST_DB, token, 600)
        assert res == {"family_id": fid, "role": "parent", "name": "Родитель"}
        assert get_member(TEST_DB, 600)["role"] == "parent"

    def test_join_child_card_uses_card_name(self):
        from database import (create_family_with_owner, create_invite,
                              join_by_invite, get_member, get_family)
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = create_invite(TEST_DB, fid, "child", "Маша")
        res = join_by_invite(TEST_DB, token, 700)
        assert res["role"] == "child" and res["name"] == "Маша"
        assert get_member(TEST_DB, 700)["name"] == "Маша"

    def test_join_unknown_token_returns_none(self):
        from database import join_by_invite
        assert join_by_invite(TEST_DB, "nope", 700) is None

    def test_join_is_idempotent(self):
        from database import create_family_with_owner, get_family_invite, join_by_invite, get_member
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = get_family_invite(TEST_DB, fid)["token"]
        join_by_invite(TEST_DB, token, 600)
        join_by_invite(TEST_DB, token, 600)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        n = conn.execute("SELECT COUNT(*) FROM members WHERE telegram_id = 600").fetchone()[0]
        conn.close()
        assert n == 1


class TestMemberMiddleware:
    def test_role_from_member(self):
        import bot
        assert bot._role({"role": "parent"}, 999) == "parent"
        assert bot._role({"role": "child"}, 999) == "child"

    def test_role_fallback_to_env_for_family_one(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: uid == 222)
        monkeypatch.setattr(bot, "is_child", lambda uid: uid == 111)
        assert bot._role(None, 222) == "parent"
        assert bot._role(None, 111) == "child"
        assert bot._role(None, 555) == "unknown"

    def test_middleware_injects_member(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        captured = {}

        async def handler(event, data):
            captured.update(data)
            return "ok"

        event = MagicMock()
        event.from_user.id = 111
        with patch.object(bot, "get_member", return_value={"role": "child", "family_id": 1}):
            result = asyncio.run(bot.MemberMiddleware()(handler, event, {}))

        assert result == "ok"
        assert captured["member"] == {"role": "child", "family_id": 1}

    def test_middleware_none_when_no_user(self):
        import asyncio
        import bot
        from unittest.mock import MagicMock

        captured = {}

        async def handler(event, data):
            captured.update(data)

        event = MagicMock()
        event.from_user = None
        asyncio.run(bot.MemberMiddleware()(handler, event, {}))
        assert captured["member"] is None


class TestFamilyTwoAccessNoGate:
    """SP3C Task 6: the interim family-#1 gate is removed.

    The middleware only injects the caller's member row; all data paths are
    tenant-scoped, so family #2 members reach their own menu/data. Family #1
    and the .env fallback (member=None) stay unchanged.
    """

    @staticmethod
    def _run(event):
        import asyncio
        import bot
        called = []

        async def handler(ev, data):
            called.append(True)
            return "ok"

        asyncio.run(bot.MemberMiddleware()(handler, event, {}))
        return called

    @staticmethod
    def _message(uid, text):
        from unittest.mock import AsyncMock, MagicMock
        msg = MagicMock()
        msg.from_user.id = uid
        msg.text = text
        msg.answer = AsyncMock()
        return msg

    @staticmethod
    def _callback(uid, data):
        from aiogram import types
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock(spec=types.CallbackQuery)
        # spec() hides pydantic fields (from_user/data) from dir(); lift the
        # restriction so the mock still behaves like a CallbackQuery.
        cb._mock_methods = None
        cb.from_user.id = uid
        cb.data = data
        cb.answer = AsyncMock()
        return cb

    def _patch_member(self, family_id):
        from unittest.mock import patch
        import bot
        return patch.object(bot, "get_member",
                            return_value={"role": "parent", "family_id": family_id})

    def test_new_family_message_reaches_handler(self):
        msg = self._message(999, "status")
        with self._patch_member(2):
            assert self._run(msg) == [True]

    def test_new_family_callback_reaches_handler(self):
        cb = self._callback(999, "settings")
        with self._patch_member(2):
            assert self._run(cb) == [True]

    def test_new_family_registration_callbacks_pass_through(self):
        for data in ("reg_create", "reg_join"):
            with self._patch_member(2):
                assert self._run(self._callback(999, data)) == [True], data

    def test_family_one_member_still_reaches_handler(self):
        with self._patch_member(1):
            assert self._run(self._message(111, "status")) == [True]

    def test_member_none_still_reaches_handler(self):
        from unittest.mock import patch
        import bot
        with patch.object(bot, "get_member", return_value=None):
            assert self._run(self._message(111, "status")) == [True]

    def test_cmd_start_family_two_shows_tenant_menu(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(999, "/start")
        state = AsyncMock()
        with patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cmd_start(
                msg, state, member={"role": "parent", "family_id": 2}))
        menu.assert_awaited()
        state.clear.assert_awaited()

    def test_cmd_cancel_family_two_shows_tenant_menu(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(999, "/cancel")
        state = AsyncMock()
        state.get_state = AsyncMock(return_value=None)
        with patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cmd_cancel(
                msg, state, member={"role": "parent", "family_id": 2}))
        menu.assert_awaited()

    def test_catch_all_family_two_shows_tenant_menu(self):
        """catch_all must again render the menu for family != 1."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(999, "привет")
        state = AsyncMock()
        state.get_state = AsyncMock(return_value=None)
        with patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.catch_all(
                msg, state, member={"role": "parent", "family_id": 2}))
        menu.assert_awaited()

    def test_lookalike_commands_reach_family_two_menu(self):
        """Lookalike commands fall through to catch_all's tenant menu."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        state = AsyncMock()
        state.get_state = AsyncMock(return_value=None)
        for text in ("/startxyz", "/start@otherbot", "/cancel@otherbot"):
            msg = self._message(999, text)
            with patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
                asyncio.run(bot.catch_all(
                    msg, state, member={"role": "parent", "family_id": 2}))
            assert menu.await_count == 1, text

    def test_send_main_menu_family_two_builds_status(self):
        """The menu now renders the *tenant-scoped* status for family #2."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(999, None)
        with patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="STATUS")) as status, \
             patch.object(bot, "count_family_children", return_value=0):
            asyncio.run(bot.send_main_menu(
                msg, 999, member={"role": "parent", "family_id": 2}))
        status.assert_awaited()
        assert msg.answer.await_args.args[0] == "STATUS"

    def test_send_main_menu_family_one_still_builds_status(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(999, None)
        with patch.object(bot, "get_member",
                          return_value={"role": "parent", "family_id": 1}), \
             patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="STATUS")) as status:
            asyncio.run(bot.send_main_menu(
                msg, 999, member={"role": "parent", "family_id": 1}))
        status.assert_awaited()
        assert msg.answer.await_args.args[0] == "STATUS"

    def test_send_main_menu_member_none_unchanged(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._message(111, None)
        with patch.object(bot, "get_member", return_value=None), \
             patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="STATUS")) as status:
            asyncio.run(bot.send_main_menu(msg, 111, member=None))
        status.assert_awaited()


class TestHandlerRolesFromDb:
    """Task 4 (SP3B): runtime role checks must use the member row from the DB.

    A parent of a new family (whose Telegram id is absent from .env) must pass
    parent-only gates via ``member``, while an unknown user (member=None and
    not in .env) is rejected. Existing callers without ``member`` keep working
    through the .env fallback inside ``_role``.
    """

    def _cb(self, uid, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_new_family_parent_allowed_via_member(self):
        """A parent of a new family (not in .env) may open settings."""
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(999, "settings")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_all_measurements", return_value=[]):
            asyncio.run(bot.cb_settings(cb, member={"role": "parent", "family_id": 2}))

        assert "Настройки" in sent.get("text", "")

    def test_unknown_user_rejected(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(999, "settings")
        with patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_settings(cb, member=None))

        cb.message.answer.assert_not_called()
        cb.answer.assert_awaited()

    def test_child_member_rejected_on_parent_handler(self):
        """A DB child must not reach a parent-only handler (export)."""
        import asyncio
        import bot

        cb = self._cb(999, "export")
        asyncio.run(bot.cb_export(cb, member={"role": "child", "family_id": 2}))

        cb.message.answer.assert_not_called()
        cb.answer.assert_awaited()

    def test_send_main_menu_uses_member_role_for_keyboard(self):
        """The main-menu keyboard must reflect the DB role, not only .env."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        def labels(markup):
            return [b.text for row in markup.inline_keyboard for b in row]

        parent_msg = MagicMock()
        parent_msg.answer = AsyncMock()
        parent_msg.from_user = MagicMock()
        parent_msg.from_user.id = 999

        child_msg = MagicMock()
        child_msg.answer = AsyncMock()
        child_msg.from_user = MagicMock()
        child_msg.from_user.id = 999

        with patch.object(bot, "build_status_block", new=AsyncMock(return_value="status")):
            asyncio.run(bot.send_main_menu(parent_msg, 999,
                                           member={"role": "parent", "family_id": 1}))
            asyncio.run(bot.send_main_menu(child_msg, 999,
                                           member={"role": "child", "family_id": 1}))

        parent_labels = labels(parent_msg.answer.call_args.kwargs["reply_markup"])
        child_labels = labels(child_msg.answer.call_args.kwargs["reply_markup"])
        assert "⚙️ Настройки" in parent_labels
        assert "📈 Моя статистика" in child_labels

    def test_input_note_forwards_member_to_main_menu(self):
        """The text-note path must hand the DB member to the main menu.

        Otherwise a parent of a new family (not in .env) falls back to the
        child keyboard after saving a note.
        """
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "спорт"
        msg.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(return_value={"note_for_id": 1})
        state.clear = AsyncMock()

        calls = []

        async def fake_menu(message_or_callback, user_id, member=None):
            calls.append(member)

        with patch.object(bot, "send_main_menu", side_effect=fake_menu), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "set_note", return_value=True):
            asyncio.run(bot.input_note(
                msg, state, member={"role": "parent", "family_id": 2}))

        assert calls == [{"role": "parent", "family_id": 2}], \
            "input_note must forward member to send_main_menu"

    def test_input_note_no_child_sends_single_hint(self):
        """SP3C Task 6: the no-child branch must not double-prompt.

        ``_no_child_reply`` already carries the hint and the «Дети» button, so
        the handler returns without an extra main-menu (status) message.
        """
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "спорт"
        msg.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(return_value={"note_for_id": 1})
        state.clear = AsyncMock()

        with patch.object(bot, "_no_child_reply", new=AsyncMock()) as hint, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.input_note(
                msg, state, member={"role": "parent", "family_id": 2}))

        hint.assert_awaited()
        menu.assert_not_awaited()
        state.clear.assert_awaited()


class TestRegistrationFlow:
    """Task 5 (SP3B): self-service registration and invite deep-links.

    An unknown user must see the registration screen; an already-registered
    user must never be silently moved to another family by an invite token —
    neither through the deep-link nor through the code-entry FSM.
    """

    def _make_state(self, state_name=None, data=None):
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

    def _msg(self, uid, text):
        from unittest.mock import AsyncMock, MagicMock
        msg = MagicMock()
        msg.from_user.id = uid
        msg.text = text
        msg.answer = AsyncMock()
        return msg

    @staticmethod
    def _sent(msg):
        return " ".join(str(c.args[0]) for c in msg.answer.await_args_list if c.args)

    def test_unknown_user_sees_registration(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(999, "/start")
        state = self._make_state()

        with patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cmd_start(msg, state, member=None))

        sent = self._sent(msg)
        assert "семью" in sent.lower() or "код" in sent.lower()

    def test_deep_link_joins(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "/start TOKEN123")
        state = self._make_state()

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 2, "role": "child", "name": "Маша"}) as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.cmd_start(msg, state, member=None))
        m.assert_called_once_with(bot.DB_PATH, "TOKEN123", 700)

    def test_deep_link_does_not_move_existing_member(self):
        """A registered user following a deep-link must not be reassigned."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "/start TOKEN123")
        state = self._make_state()

        with patch.object(bot, "join_by_invite") as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cmd_start(
                msg, state, member={"role": "child", "family_id": 1}))

        m.assert_not_called()
        menu.assert_awaited()
        assert "уже в семь" in self._sent(msg).lower()

    def test_deep_link_unknown_token_shows_error_and_registration(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "/start BADTOKEN")
        state = self._make_state()

        with patch.object(bot, "join_by_invite", return_value=None), \
             patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.cmd_start(msg, state, member=None))

        sent = self._sent(msg).lower()
        assert "не найден" in sent or "найд" in sent
        assert "семью" in sent or "код" in sent

    def test_deep_link_join_new_family_shows_menu(self):
        """A family-#2 invitee now reaches the tenant-scoped main menu."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "/start TOKEN123")
        state = self._make_state()

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 2, "role": "parent", "name": "Мама"}), \
             patch.object(bot, "get_member",
                          return_value={"role": "parent", "family_id": 2}), \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cmd_start(msg, state, member=None))

        menu.assert_awaited()

    def test_deep_link_join_family_one_still_shows_menu(self):
        """Joining family #1 keeps the existing menu behaviour."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "/start TOKEN123")
        state = self._make_state()
        calls = []

        async def fake_menu(message_or_callback, user_id, member=None):
            calls.append(member)

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 1, "role": "parent", "name": "Мама"}), \
             patch.object(bot, "get_member",
                          return_value={"role": "parent", "family_id": 1}), \
             patch.object(bot, "send_main_menu", side_effect=fake_menu):
            asyncio.run(bot.cmd_start(msg, state, member=None))

        assert calls == [{"role": "parent", "family_id": 1}], \
            "cmd_start must re-read the member after joining the family"

    def test_input_invite_code_does_not_move_existing_member(self):
        """The code-entry path must refuse to reassign a registered user."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "TOKEN123")
        state = self._make_state("Registration:entering_invite_code")

        with patch.object(bot, "join_by_invite") as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.input_invite_code(
                msg, state, member={"role": "parent", "family_id": 2}))

        m.assert_not_called()
        menu.assert_awaited()
        assert "уже в семь" in self._sent(msg).lower()

    def test_input_invite_code_joins_unknown_user(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "TOKEN123")
        state = self._make_state("Registration:entering_invite_code")

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 2, "role": "child", "name": "Маша"}) as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.input_invite_code(msg, state, member=None))

        m.assert_called_once_with(bot.DB_PATH, "TOKEN123", 700)
        state.clear.assert_awaited()

    def test_input_invite_code_unknown_token_keeps_retrying(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "NOPE")
        state = self._make_state("Registration:entering_invite_code")

        with patch.object(bot, "join_by_invite", return_value=None), \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.input_invite_code(msg, state, member=None))

        state.clear.assert_not_awaited()
        menu.assert_not_awaited()
        assert "не найден" in self._sent(msg).lower()

    def test_input_family_name_creates_family_and_shows_invite(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "Ивановы")
        state = self._make_state("Registration:entering_family_name")

        with patch.object(bot, "create_family_with_owner", return_value=5) as m, \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "PARENTTOK"}), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.input_family_name(msg, state, member=None))

        m.assert_called_once_with(bot.DB_PATH, 700, "Ивановы")
        state.clear.assert_awaited()
        assert "PARENTTOK" in self._sent(msg)

    def test_input_family_name_new_family_renders_tenant_menu(self):
        """A freshly created family #2 now renders its own menu/status."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "Ивановы")
        state = self._make_state("Registration:entering_family_name")

        with patch.object(bot, "create_family_with_owner", return_value=2), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "PARENTTOK"}), \
             patch.object(bot, "get_member",
                          return_value={"role": "parent", "family_id": 2}), \
             patch.object(bot, "count_family_children", return_value=0), \
             patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="STATUS")) as status:
            asyncio.run(bot.input_family_name(msg, state, member=None))

        status.assert_awaited()
        assert "STATUS" in self._sent(msg)

    def test_input_invite_code_new_family_renders_tenant_menu(self):
        """A freshly joined family #2 now renders its own menu/status."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "TOKEN123")
        state = self._make_state("Registration:entering_invite_code")

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 2, "role": "child", "name": "Маша"}), \
             patch.object(bot, "get_member",
                          return_value={"role": "child", "family_id": 2}), \
             patch.object(bot, "count_family_children", return_value=0), \
             patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="STATUS")) as status:
            asyncio.run(bot.input_invite_code(msg, state, member=None))

        status.assert_awaited()
        assert "STATUS" in self._sent(msg)

    def test_input_family_name_blank_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(700, "   ")
        state = self._make_state("Registration:entering_family_name")

        with patch.object(bot, "create_family_with_owner") as m:
            asyncio.run(bot.input_family_name(msg, state, member=None))

        m.assert_not_called()
        state.clear.assert_not_awaited()
        assert "название" in self._sent(msg).lower()

    def test_registration_states_have_hints(self):
        import bot
        for s in ("Registration:entering_family_name",
                  "Registration:entering_invite_code",
                  "Registration:adding_child_name"):
            assert s in bot._FSM_HINTS

    def test_unknown_text_during_registration_hints(self):
        """Unknown text mid-registration must get a hint, not the no-access wall."""
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(700, "привет")
        state = self._make_state("Registration:entering_invite_code")

        with patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.catch_all(msg, state, member=None))

        msg.answer.assert_called()
        assert "cancel" in self._sent(msg).lower() or "код" in self._sent(msg).lower()


class TestRegistrationCancelRouting:
    """Regression: /cancel must reach cmd_cancel during registration states.

    Routed through the REAL aiogram Dispatcher/Router (``dp.feed_update`` with a
    mocked ``Bot`` session), so the routing order — not just a direct handler
    call — is exercised. Previously the bare-``F.text`` registration handlers
    were registered before ``cmd_cancel`` and swallowed the command, creating a
    junk family named "/cancel".
    """

    def _update(self, uid, text):
        from datetime import datetime, timezone
        from aiogram.types import Update, Message, User, Chat

        return Update(update_id=1, message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name="T"),
            text=text,
        ))

    def _feed(self, state_name, text="/cancel"):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch
        from aiogram import Bot
        from aiogram.fsm.storage.base import StorageKey

        async def run():
            b = Bot(token="123456:TESTTOKEN", session=AsyncMock())
            b.session = AsyncMock(return_value=None)
            key = StorageKey(bot_id=b.id, chat_id=700, user_id=700)
            await bot.dp.storage.set_state(key, state_name)
            with patch.object(bot, "DB_PATH", TEST_DB):
                await bot.dp.feed_update(b, self._update(700, text))
            return await bot.dp.storage.get_state(key)

        return asyncio.run(run())

    def test_cancel_in_family_name_does_not_create_family(self):
        import bot
        from unittest.mock import patch

        with patch.object(bot, "create_family_with_owner") as create:
            state = self._feed("Registration:entering_family_name")
        create.assert_not_called()
        assert state is None

    def test_cancel_in_invite_code_does_not_join(self):
        import bot
        from unittest.mock import patch

        with patch.object(bot, "join_by_invite") as join:
            state = self._feed("Registration:entering_invite_code")
        join.assert_not_called()
        assert state is None

    def test_lookalike_commands_reach_family_two_menu_via_dispatcher(self):
        """/start@otherbot, /cancel@otherbot and /startxyz now reach the menu.

        Through the real dispatcher, so aiogram's own Command filter is in play:
        these updates fall through to catch_all, which renders the tenant menu.
        """
        import asyncio
        import bot
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from aiogram import Bot
        from aiogram.fsm.storage.base import StorageKey

        async def run(text):
            b = Bot(token="123456:TESTTOKEN", session=AsyncMock())
            b.session = AsyncMock(return_value=None)
            # aiogram's Command filter calls bot.me() to validate @mentions.
            b._me = SimpleNamespace(username="someother_bot")
            key = StorageKey(bot_id=b.id, chat_id=700, user_id=700)
            await bot.dp.storage.set_state(key, None)

            with patch.object(bot, "DB_PATH", TEST_DB), \
                 patch.object(bot, "get_member",
                              return_value={"role": "parent", "family_id": 2}), \
                 patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
                await bot.dp.feed_update(b, self._update(700, text))
            return menu

        for text in ("/start@otherbot", "/cancel@otherbot", "/startxyz"):
            asyncio.run(run(text)).assert_awaited()

    def test_family_one_bare_start_shows_menu_via_dispatcher(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch
        from aiogram import Bot

        async def run():
            b = Bot(token="123456:TESTTOKEN", session=AsyncMock())
            b.session = AsyncMock(return_value=None)
            with patch.object(bot, "DB_PATH", TEST_DB), \
                 patch.object(bot, "get_member",
                              return_value={"role": "child", "family_id": 1}), \
                 patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
                await bot.dp.feed_update(b, self._update(700, "/start"))
            return menu

        asyncio.run(run()).assert_awaited()


class TestFamilyManagement:
    """Task 6 (SP3B): «Участники» / «Дети» management screens.

    Parent-only screens reachable from the settings keyboard: the members
    screen lists joined children and the parent invite code (with
    regeneration); the children screen lists child invite cards and allows
    adding/deleting them.
    """

    def _cb(self, uid, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def _state(self, data=None):
        from unittest.mock import AsyncMock, MagicMock
        st = MagicMock()
        st.get_data = AsyncMock(return_value=dict(data or {}))
        st.update_data = AsyncMock()
        st.set_state = AsyncMock()
        st.clear = AsyncMock()
        return st

    def _msg(self, uid, text):
        from unittest.mock import AsyncMock, MagicMock
        msg = MagicMock()
        msg.from_user.id = uid
        msg.text = text
        msg.answer = AsyncMock()
        return msg

    @staticmethod
    def _sent(msg):
        return " ".join(str(c.args[0]) for c in msg.answer.await_args_list if c.args)

    def test_kb_settings_has_family_buttons(self):
        from bot import kb_settings
        kb = kb_settings(target=260)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "members" in callbacks
        assert "children" in callbacks

    def test_members_screen_shows_invite(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "members")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "ABC", "role": "parent"}), \
             patch.object(bot, "list_family_children", return_value=[]), \
             patch.object(bot, "list_child_cards", return_value=[]):
            asyncio.run(bot.cb_members(cb, member={"role": "parent", "family_id": 2}))

        assert "ABC" in sent.get("text", "")

    def test_members_screen_lists_joined_children(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "members")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "ABC"}), \
             patch.object(bot, "list_family_children",
                          return_value=[{"name": "Маша"}]):
            asyncio.run(bot.cb_members(cb, member={"role": "parent", "family_id": 2}))

        assert "Маша" in sent.get("text", "")

    def test_members_screen_child_rejected(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        cb = self._cb(500, "members")
        respond = AsyncMock()
        with patch.object(bot, "respond", new=respond):
            asyncio.run(bot.cb_members(cb, member={"role": "child", "family_id": 2}))

        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_regen_invite_shows_new_token(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "regen_invite")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent.setdefault("texts", []).append(text)

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "regenerate_family_invite", return_value="NEWTOK") as regen, \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "NEWTOK"}), \
             patch.object(bot, "list_family_children", return_value=[]):
            asyncio.run(bot.cb_regen_invite(cb, member={"role": "parent", "family_id": 2}))

        regen.assert_called_once_with(bot.DB_PATH, 2)
        assert any("NEWTOK" in t for t in sent.get("texts", []))

    def test_children_screen_lists_cards(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "children")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text
            sent["kb"] = kb

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "list_child_cards",
                          return_value=[{"name": "Маша", "token": "TOK"}]):
            asyncio.run(bot.cb_children(cb, member={"role": "parent", "family_id": 2}))

        assert "Маша" in sent.get("text", "")
        callbacks = [b.callback_data for row in sent["kb"].inline_keyboard for b in row]
        assert "add_child" in callbacks
        assert "del_child_TOK" in callbacks

    def test_children_screen_child_rejected(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        cb = self._cb(500, "children")
        respond = AsyncMock()
        with patch.object(bot, "respond", new=respond):
            asyncio.run(bot.cb_children(cb, member={"role": "child", "family_id": 2}))

        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_cb_add_child_sets_fsm_state(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "add_child")
        state = self._state()
        with patch.object(bot, "answer_callback"), \
             patch.object(bot, "kb_back", return_value=None):
            asyncio.run(bot.cb_add_child(cb, state,
                                         member={"role": "parent", "family_id": 2}))

        state.update_data.assert_awaited()
        assert state.update_data.await_args.kwargs.get("family_id") == 2
        assert state.set_state.await_args.args[0] == bot.Registration.adding_child_name

    def test_add_child_creates_card(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(500, "Маша")
        state = self._state({"family_id": 2})

        with patch.object(bot, "create_invite", return_value="TOK") as c:
            asyncio.run(bot.input_child_name(
                msg, state, member={"role": "parent", "family_id": 2}))

        c.assert_called_once_with(bot.DB_PATH, 2, "child", "Маша")
        assert "TOK" in self._sent(msg)
        state.clear.assert_awaited()

    def test_add_child_blank_name_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(500, "   ")
        state = self._state({"family_id": 2})

        with patch.object(bot, "create_invite") as c, \
             patch.object(bot, "kb_back", return_value=None):
            asyncio.run(bot.input_child_name(
                msg, state, member={"role": "parent", "family_id": 2}))

        c.assert_not_called()
        state.clear.assert_not_awaited()
        assert "имя" in self._sent(msg).lower()

    def test_del_child_deletes_and_redraws(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "del_child_TOK")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "delete_invite", return_value=True) as d, \
             patch.object(bot, "list_child_cards", return_value=[]):
            asyncio.run(bot.cb_del_child(cb, member={"role": "parent", "family_id": 2}))

        d.assert_called_once_with(bot.DB_PATH, "TOK", 2)
        cb.answer.assert_awaited()

    def test_del_child_malformed_token_alerts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "del_child_")

        with patch.object(bot, "delete_invite") as d:
            asyncio.run(bot.cb_del_child(cb, member={"role": "parent", "family_id": 2}))

        d.assert_not_called()
        cb.answer.assert_awaited()

    def test_del_child_child_rejected(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "del_child_TOK")
        with patch.object(bot, "delete_invite") as d:
            asyncio.run(bot.cb_del_child(cb, member={"role": "child", "family_id": 2}))

        d.assert_not_called()
        cb.answer.assert_awaited()

    def test_del_child_cannot_delete_other_family_card(self):
        """F2: a family-A parent cannot delete a family-B child card."""
        import asyncio
        import bot
        from unittest.mock import patch
        from database import create_family, create_invite, get_invite, list_child_cards

        family_a = create_family(TEST_DB, "A")
        family_b = create_family(TEST_DB, "B")
        token_b = create_invite(TEST_DB, family_b, "child", "Маша")

        cb = self._cb(500, f"del_child_{token_b}")
        with patch.object(bot, "DB_PATH", TEST_DB), \
             patch.object(bot, "respond"):
            asyncio.run(bot.cb_del_child(
                cb, member={"role": "parent", "family_id": family_a}))

        assert get_invite(TEST_DB, token_b) is not None
        assert len(list_child_cards(TEST_DB, family_b)) == 1
        cb.answer.assert_awaited()

    def test_members_no_member_env_parent_prompts(self):
        """An env-parent with no members row must not crash on member['family_id']."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        cb = self._cb(500, "members")
        respond = AsyncMock()
        with patch.object(bot, "respond", new=respond), \
             patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_members(cb, member=None))

        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_children_no_member_env_parent_prompts(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        cb = self._cb(500, "children")
        respond = AsyncMock()
        with patch.object(bot, "respond", new=respond), \
             patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_children(cb, member=None))

        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_add_child_no_member_env_parent_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "add_child")
        state = self._state()
        with patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_add_child(cb, state, member=None))

        state.set_state.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_del_child_no_member_env_parent_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "del_child_TOK")
        with patch.object(bot, "delete_invite") as d, \
             patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_del_child(cb, member=None))

        d.assert_not_called()
        cb.answer.assert_awaited()

    def test_regen_invite_no_member_env_parent_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "regen_invite")
        with patch.object(bot, "regenerate_family_invite") as r, \
             patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_regen_invite(cb, member=None))

        r.assert_not_called()
        cb.answer.assert_awaited()

    def test_input_child_name_no_member_env_parent_prompts(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(500, "Маша")
        state = self._state({"family_id": 2})
        with patch.object(bot, "create_invite") as c, \
             patch.object(bot, "is_parent", return_value=True), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.input_child_name(msg, state, member=None))

        c.assert_not_called()
        state.clear.assert_awaited()
        assert self._sent(msg)

    def test_guard_keeps_unknown_user_rejected(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        cb = self._cb(500, "members")
        respond = AsyncMock()
        with patch.object(bot, "respond", new=respond), \
             patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_members(cb, member=None))

        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_members_screen_raw_token_in_code_span(self):
        """The token inside backticks must be raw (no Markdown backslashes)."""
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "members")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "AB_CD"}), \
             patch.object(bot, "list_family_children", return_value=[]):
            asyncio.run(bot.cb_members(cb, member={"role": "parent", "family_id": 2}))

        assert "`AB_CD`" in sent.get("text", "")
        assert "AB\\_CD" not in sent.get("text", "")

    def test_regen_invite_raw_token_in_code_span(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "regen_invite")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "regenerate_family_invite", return_value="NEW_EN"), \
             patch.object(bot, "get_family_invite", return_value=None), \
             patch.object(bot, "list_family_children", return_value=[]):
            asyncio.run(bot.cb_regen_invite(cb, member={"role": "parent", "family_id": 2}))

        assert "`NEW_EN`" in sent.get("text", "")
        assert "NEW\\_EN" not in sent.get("text", "")

    def test_add_child_raw_token_in_code_span(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = self._msg(500, "Маша")
        state = self._state({"family_id": 2})
        with patch.object(bot, "create_invite", return_value="TOK_EN"), \
             patch.object(bot, "list_child_cards", return_value=[]):
            asyncio.run(bot.input_child_name(
                msg, state, member={"role": "parent", "family_id": 2}))

        sent = self._sent(msg)
        assert "`TOK_EN`" in sent
        assert "TOK\\_EN" not in sent

    def test_input_family_name_raw_token_in_code_span(self):
        """Task 5's parent invite display must also keep the token raw."""
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch

        msg = self._msg(700, "Ивановы")
        state = self._state()

        with patch.object(bot, "create_family_with_owner", return_value=5), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "PA_RE"}), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.input_family_name(msg, state, member=None))

        sent = self._sent(msg)
        assert "`PA_RE`" in sent
        assert "PA\\_RE" not in sent

    def test_del_child_routes_to_family_handler(self):
        """Regression: `del_child_<token>` must reach cb_del_child.

        The generic measurement handler matches `startswith("del_")`, which
        also matches `del_child_...`; it is registered earlier, so without
        correct ordering the delete-child callback is swallowed and the token
        is parsed as a measurement id.
        """
        import asyncio
        import bot
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock, patch
        from aiogram import Bot
        from aiogram.types import Update, CallbackQuery, Message, Chat, User

        upd = Update(update_id=1, callback_query=CallbackQuery(
            id="1", from_user=User(id=999, is_bot=False, first_name="P"),
            chat_instance="ci", data="del_child_TOK",
            message=Message(message_id=1, date=datetime.now(timezone.utc),
                            chat=Chat(id=999, type="private"), text="x")))

        async def run():
            b = Bot(token="123456:TESTTOKEN", session=AsyncMock())
            b.session = AsyncMock(return_value=None)
            with patch.object(bot, "DB_PATH", TEST_DB), \
                 patch.object(bot, "get_member",
                              MagicMock(return_value={"role": "parent", "family_id": 1})), \
                 patch.object(bot, "delete_invite",
                              MagicMock(return_value=True)) as d, \
                 patch.object(bot, "list_child_cards", MagicMock(return_value=[])):
                await bot.dp.feed_update(b, upd)
            return d

        d = asyncio.run(run())
        d.assert_called_once_with(TEST_DB, "TOK", 1)


class TestActiveChild:
    def test_schema_v5(self):
        import database
        assert database.SCHEMA_VERSION == 5

    def test_active_child_column(self):
        import sqlite3
        from database import init_db
        init_db(TEST_DB)
        c = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in c.execute("PRAGMA table_info(members)")]
        c.close()
        assert "active_child_id" in cols

    def test_set_active_child_validates_same_family(self):
        from database import create_family_with_owner, add_member, set_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        f2 = create_family_with_owner(TEST_DB, 501, "B")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f2, "child", "Петя")
        assert set_active_child(TEST_DB, 500, 700) is True
        assert get_member(TEST_DB, 500)["active_child_id"] == 700
        assert set_active_child(TEST_DB, 500, 701) is False  # чужой ребёнок
        assert set_active_child(TEST_DB, 500, 999) is False

    def test_resolve_active_child_for_child_member(self):
        from database import create_family_with_owner, add_member, resolve_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 700)) == 700

    def test_resolve_active_child_defaults_to_first(self):
        from database import create_family_with_owner, add_member, resolve_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f1, "child", "Петя")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) in (700, 701)

    def test_resolve_active_child_none_without_children(self):
        from database import create_family_with_owner, resolve_active_child, get_member
        create_family_with_owner(TEST_DB, 500, "A")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) is None

    def test_resolve_active_child_respects_selection(self):
        from database import (create_family_with_owner, add_member, set_active_child,
                              resolve_active_child, get_member)
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f1, "child", "Петя")
        set_active_child(TEST_DB, 500, 701)
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) == 701

    def test_resolve_active_child_uses_preloaded_children(self, monkeypatch):
        """F2: callers may pass the family's children to avoid a duplicate query."""
        from database import create_family_with_owner, add_member, resolve_active_child, get_member
        import database
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f1, "child", "Петя")
        monkeypatch.setattr(database, "list_family_children",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("extra query")))
        children = [{"telegram_id": 701, "family_id": f1, "role": "child", "name": "Петя"}]
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500), children) == 701

    def test_count_family_children(self):
        from database import create_family_with_owner, add_member, count_family_children
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 222, f1, "parent", "Олег")
        assert count_family_children(TEST_DB, f1) == 1


class TestTenantContext:
    def test_ctx_fallback_env(self, monkeypatch):
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_ID", 111)
        assert asyncio.run(bot._ctx(None)) == (bot.DEFAULT_FAMILY_ID, 111)

    def test_ctx_child_self(self):
        import asyncio, bot
        assert asyncio.run(bot._ctx({"role": "child", "telegram_id": 700, "family_id": 2})) == (2, 700)

    def test_ctx_parent_uses_resolve(self, monkeypatch):
        import asyncio, bot
        from unittest.mock import patch
        with patch.object(bot, "resolve_active_child", return_value=700):
            assert asyncio.run(bot._ctx({"role": "parent", "telegram_id": 500, "family_id": 2})) == (2, 700)

    def test_child_name_fallback(self, monkeypatch):
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        assert asyncio.run(bot._child_name(None, None)) == "Motya"

    def test_family_parents_env_fallback(self, monkeypatch):
        import asyncio, bot
        import sqlite3
        monkeypatch.setattr(bot, "PARENT_IDS", [222, 333])
        # DB members take precedence (multi-family); env is the legacy fallback
        # only for the default family when it has no parent rows.
        conn = sqlite3.connect(TEST_DB)
        conn.execute("DELETE FROM members WHERE family_id = ?",
                     (bot.DEFAULT_FAMILY_ID,))
        conn.commit()
        conn.close()
        assert asyncio.run(bot._family_parents(None, 1)) == [222, 333]


class TestTenantAwareCore:
    def test_status_block_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import patch
        seen = {}
        def fake_today(db, child_id, family_id=bot.DEFAULT_FAMILY_ID):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return []
        with patch.object(bot, "get_today_measurements", side_effect=fake_today), \
             patch.object(bot, "get_recent_measurements", return_value=[]), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700):
            asyncio.run(bot.build_status_block(member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert seen.get("child_id") == 700
        assert seen.get("family_id") == 2

    def test_add_measurement_uses_context(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        state = MagicMock()
        state.get_data = AsyncMock(return_value={"input_context": "add"})
        state.update_data = AsyncMock(); state.set_state = AsyncMock(); state.clear = AsyncMock()
        calls = {}
        def fake_add(db, pef, tod, child_id, added_by, source="manual", family_id=1):
            calls["child_id"] = child_id; calls["family_id"] = family_id; return 1
        with patch.object(bot, "respond", new=AsyncMock()), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "add_measurement", side_effect=fake_add), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_previous_of_tod", return_value=None), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(cb, state, 250, "morning",
                                                 member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert calls.get("child_id") == 700 and calls.get("family_id") == 2

    def test_send_main_menu_forwards_member_to_status(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        msg = MagicMock()
        msg.from_user = MagicMock(); msg.from_user.id = 999
        msg.answer = AsyncMock()
        member = {"role": "parent", "telegram_id": 999, "family_id": 1}
        with patch.object(bot, "get_member",
                          return_value={"role": "parent", "family_id": 1}), \
             patch.object(bot, "build_status_block",
                          new=AsyncMock(return_value="S")) as status:
            asyncio.run(bot.send_main_menu(msg, 999, member=member))
        status.assert_awaited_once_with(member)

    def test_no_child_reply_hint_and_button(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock
        msg = MagicMock(); msg.answer = AsyncMock()
        asyncio.run(bot._no_child_reply(
            msg, {"role": "parent", "telegram_id": 500, "family_id": 2}))
        text = msg.answer.await_args.args[0]
        kb = msg.answer.await_args.kwargs["reply_markup"]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "ребёнк" in text.lower()
        assert "children" in cbs


class TestTenantAwareViews:
    """SP3C Task 4: history/chart/summary/stats/week use the active child."""

    PARENT = {"role": "parent", "telegram_id": 500, "family_id": 2}

    @staticmethod
    def _cb(uid=500, data=None):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user = MagicMock()
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.answer_photo = AsyncMock()
        cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_history_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb()
        seen = {}

        def fake_pag(db, child_id, page=1, per_page=10, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return [], 0, 1

        with patch.object(bot, "get_measurements_paginated", side_effect=fake_pag), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot._show_history(cb, 1, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_summary_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb()
        seen = {}

        def fake_today(db, child_id, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return []

        with patch.object(bot, "get_today_measurements", side_effect=fake_today), \
             patch.object(bot, "get_stats", return_value={"total": 0}), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot.cb_summary(cb, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_stats_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb()
        seen = {}

        def fake_stats(db, child_id, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return {"total": 0}

        with patch.object(bot, "get_stats", side_effect=fake_stats), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot.cb_stats(cb, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_chart_download_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(data="chart_dl_2026-08")
        seen = {}
        rows = [
            {"pef_value": 240, "time_of_day": "morning", "measured_at": "2026-08-05 08:00:00"},
            {"pef_value": 250, "time_of_day": "evening", "measured_at": "2026-08-06 20:00:00"},
        ]

        def fake_month(db, child_id, year, month, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return rows

        with patch.object(bot, "get_measurements_for_month", side_effect=fake_month), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "_render_chart_png_async", new=AsyncMock(return_value=b"png")), \
             patch.object(bot, "answer_callback", new=AsyncMock()):
            asyncio.run(bot.cb_chart_download(cb, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_weekly_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        msg = MagicMock()
        msg.answer = AsyncMock()
        seen = {}

        def fake_weeks(db, child_id, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return [], []

        with patch.object(bot, "get_last_two_weeks", side_effect=fake_weeks), \
             patch.object(bot, "resolve_active_child", return_value=700):
            asyncio.run(bot._send_weekly_report(msg, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2


class TestTenantAwareOps:
    """SP3C Task 5: settings/export/scheduler use the family context."""

    PARENT = {"role": "parent", "telegram_id": 500, "family_id": 2}

    @staticmethod
    def _cb(uid=500, data=None):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user = MagicMock()
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_measurement_notifies_family_parents(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 700; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        state = MagicMock(); state.get_data = AsyncMock(return_value={})
        state.update_data = AsyncMock(); state.set_state = AsyncMock()
        sent = []

        async def fake_parents(member, family_id):
            return [222]

        async def fake_send(pid, text, **kw):
            sent.append(pid)

        with patch.object(bot, "respond", new=AsyncMock()), \
             patch.object(bot, "replace_auto_measurement", return_value=False), \
             patch.object(bot, "add_measurement", return_value=1), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_previous_of_tod", return_value=None), \
             patch.object(bot, "_family_parents", side_effect=fake_parents), \
             patch.object(bot, "_ctx", new=AsyncMock(return_value=(2, 700))), \
             patch.object(bot.bot, "send_message", side_effect=fake_send), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(
                cb, state, 240, "morning",
                member={"role": "child", "telegram_id": 700, "family_id": 2}))
        assert 222 in sent

    def test_settings_uses_family(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(data="settings")
        seen = {}

        def fake_all(db, child_id, include_auto=False, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return []

        with patch.object(bot, "get_all_measurements", side_effect=fake_all), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot.cb_settings(cb, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_reminders_uses_family(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(data="reminders")
        seen = {}

        def fake_hours(db, family_id=1):
            seen["family_id"] = family_id
            return {"child_morning": 8, "child_evening": 20,
                    "parent_morning": 10, "parent_evening": 22}

        with patch.object(bot, "get_reminder_hours", side_effect=fake_hours), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot.cb_reminders(cb, member=dict(self.PARENT)))
        assert seen.get("family_id") == 2

    def test_export_all_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(data="export_all")
        seen = {}

        def fake_between(db, child_id, start, end, family_id=1):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return []

        with patch.object(bot, "get_measurements_between", side_effect=fake_between), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "answer_callback", new=AsyncMock()):
            asyncio.run(bot.cb_export_all(cb, member=dict(self.PARENT)))
        assert seen.get("child_id") == 700 and seen.get("family_id") == 2

    def test_change_target_uses_family(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = self._cb(data="change_target")
        state = MagicMock(); state.set_state = AsyncMock()
        seen = {}

        def fake_target(family_id=1):
            seen["family_id"] = family_id
            return 260

        with patch.object(bot, "get_effective_target", side_effect=fake_target), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot.cb_change_target(cb, state, member=dict(self.PARENT)))
        assert seen.get("family_id") == 2

    def test_input_target_uses_family(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        msg = MagicMock(); msg.text = "300"; msg.answer = AsyncMock()
        state = MagicMock(); state.clear = AsyncMock()
        calls = []

        def fake_set(db, key, value, family_id=1):
            calls.append({"key": key, "family_id": family_id})

        with patch.object(bot, "set_setting", side_effect=fake_set), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "_send_settings_from_message", new=AsyncMock()):
            asyncio.run(bot.input_target(msg, state, member=dict(self.PARENT)))
        assert calls and calls[0]["family_id"] == 2

    def test_escalation_uses_family_parents(self):
        import asyncio, bot
        from unittest.mock import patch
        sent = []

        async def fake_parents(member, family_id):
            return [222]

        async def fake_send(pid, text, **kw):
            sent.append(pid)

        with patch.object(bot, "was_reminder_sent", return_value=False), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "get_last_of_tod", return_value=None), \
             patch.object(bot, "mark_reminder_sent", return_value=None), \
             patch.object(bot, "_family_parents", side_effect=fake_parents), \
             patch.object(bot.bot, "send_message", side_effect=fake_send):
            asyncio.run(bot._escalate_parents(
                "morning",
                {"child_morning": 8, "child_evening": 20,
                 "parent_morning": 10, "parent_evening": 22},
                10, 0, "2026-09-12"))
        assert 222 in sent


class TestBackupScopedToFamily:
    """F1: the DB backup must contain only the caller's family."""

    def test_backup_family_db_contains_only_that_family(self):
        import sqlite3
        import os
        from database import (create_family, add_member, add_measurement,
                              set_setting, create_invite, mark_reminder_sent,
                              backup_family_db)
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        add_measurement(TEST_DB, 300, "morning", 700, 700, family_id=f2)
        add_measurement(TEST_DB, 240, "morning", 111, 111, family_id=f1)
        set_setting(TEST_DB, "target_pef", "400", family_id=f2)
        set_setting(TEST_DB, "target_pef", "240", family_id=f1)
        create_invite(TEST_DB, f2, "parent")
        mark_reminder_sent(TEST_DB, "2026-09-17", "weekly", 700)

        dest = TEST_DB + ".fam2.bak"
        try:
            backup_family_db(TEST_DB, dest, f2)
            conn = sqlite3.connect(dest)
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute(
                "SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
            assert conn.execute(
                "SELECT pef_value FROM measurements").fetchone()[0] == 300
            assert conn.execute(
                "SELECT COUNT(*) FROM measurements WHERE family_id = ?",
                (f1,)).fetchone()[0] == 0
            assert [r[0] for r in conn.execute(
                "SELECT id FROM families ORDER BY id")] == [f2]
            assert [r[0] for r in conn.execute(
                "SELECT telegram_id FROM members")] == [700]
            assert [r[0] for r in conn.execute(
                "SELECT family_id FROM settings")] == [f2]
            assert [r[0] for r in conn.execute(
                "SELECT family_id FROM invites")] == [f2]
            assert [r[0] for r in conn.execute(
                "SELECT child_id FROM reminders_sent")] == [700]
            conn.close()
        finally:
            for ext in ["", "-wal", "-shm"]:
                p = dest + ext
                if os.path.exists(p):
                    os.remove(p)

    def test_cb_backup_uses_family_scoped_backup(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.data = "backup"
        cb.from_user = MagicMock(); cb.from_user.id = 500
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        seen = {}

        def fake_backup(db, dest, family_id):
            seen["family_id"] = family_id

        with patch.object(bot, "backup_family_db", side_effect=fake_backup), \
             patch.object(bot, "_ctx", new=AsyncMock(return_value=(2, 700))), \
             patch.object(bot, "get_all_measurements", return_value=[]), \
             patch.object(bot, "answer_callback", new=AsyncMock()), \
             patch.object(bot, "os") as m_os:
            m_os.path.exists.return_value = False
            asyncio.run(bot.cb_backup(cb, member={"role": "parent",
                                                  "telegram_id": 500,
                                                  "family_id": 2}))
        assert seen.get("family_id") == 2


class TestChildNameLookup:
    """F2: _child_name resolves the active child's stored name."""

    def test_child_name_for_family_member(self):
        import asyncio, bot
        from database import create_family, add_member
        f2 = create_family(TEST_DB, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        name = asyncio.run(bot._child_name(
            {"role": "parent", "telegram_id": 500, "family_id": f2}, 700))
        assert name == "Маша"

    def test_child_name_none_member_uses_env(self, monkeypatch):
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        assert asyncio.run(bot._child_name(None, 111)) == "Motya"

    def test_child_name_explicit_skips_lookup(self):
        import asyncio, bot
        from unittest.mock import patch
        with patch.object(bot, "_db") as m_db:
            name = asyncio.run(bot._child_name(
                {"role": "parent", "telegram_id": 500, "family_id": 2},
                700, child_name="Петя"))
        assert name == "Петя"
        m_db.assert_not_called()

    def test_child_name_missing_member_falls_back(self):
        import asyncio, bot
        name = asyncio.run(bot._child_name(
            {"role": "parent", "telegram_id": 500, "family_id": 2}, 999999))
        assert name == "Ребёнок"

    def test_child_name_known_member_without_child_does_not_leak_env(self, monkeypatch):
        """F1: a family-2 parent with no active child must not see family #1's name."""
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        name = asyncio.run(bot._child_name(
            {"role": "parent", "telegram_id": 500, "family_id": 2}, None))
        assert name == "Ребёнок"

    def test_settings_text_without_child_name_does_not_leak_env(self, monkeypatch):
        """F1: no secondary env fallback in the settings screen."""
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        assert "Motya" not in bot.build_settings_text(260, 0, None)
        member = {"role": "parent", "telegram_id": 500, "family_id": 2}
        name = asyncio.run(bot._child_name(member, None))
        assert "Motya" not in bot.build_settings_text(260, 0, name)

    def test_display_name_known_child_does_not_leak_env(self, monkeypatch):
        """Trivial: a known child author must not be labelled with env CHILD_NAME."""
        import bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        member = {"role": "child", "telegram_id": 700, "family_id": 2}
        assert bot._user_display_name(700, member) == "Ребёнок"
        assert bot._user_display_name(700, member, "Маша") == "Маша"


class TestAuthorFromMembers:
    """SP3D Task 2: author role/name come from `members`; env only when no member."""

    @staticmethod
    def _m(added_by):
        return {"pef_value": 240, "time_of_day": "morning",
                "measured_at": "2026-08-05 08:00:00", "added_by": added_by,
                "note": None, "source": "manual"}

    def test_history_line_marks_db_parent(self):
        from bot import _history_line
        line = _history_line(
            self._m(500), 260,
            member={"role": "parent", "family_id": 2, "telegram_id": 999},
            author_roles={500: "parent"})
        assert "👨" in line

    def test_history_line_marks_db_child(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: True)  # env says parent
        line = bot._history_line(
            self._m(700), 260,
            member={"role": "parent", "family_id": 2, "telegram_id": 999},
            author_roles={700: "child"})
        assert "👶" in line

    def test_history_line_env_fallback_without_member(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: uid == 222)
        assert "👨" in bot._history_line(self._m(222), 260)
        assert "👶" in bot._history_line(self._m(500), 260)

    def test_history_line_member_context_ignores_env(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: True)
        line = bot._history_line(
            self._m(500), 260,
            member={"role": "parent", "family_id": 2, "telegram_id": 999},
            author_roles={})
        assert "👶" in line

    def test_format_history_lines_threads_roles(self):
        from bot import _format_history_lines
        lines = _format_history_lines(
            [self._m(500), self._m(700)], 260,
            member={"role": "parent", "family_id": 2, "telegram_id": 999},
            author_roles={500: "parent", 700: "child"})
        assert "👨" in lines[0] and "👶" in lines[1]

    def test_user_display_name_uses_member_name(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: False)
        members_map = {500: {"role": "parent", "name": "Олег"}}
        assert bot._user_display_name(500, members_map=members_map) == "Олег"

    def test_user_display_name_unknown_member_does_not_use_env(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: True)
        members_map = {500: {"role": "parent", "name": "Олег"}}
        assert bot._user_display_name(999, members_map=members_map) == "Кто-то"

    def test_user_display_name_without_context_falls_back_env(self, monkeypatch):
        import bot
        # CI has no .env (CHILD_ID=0 collides with PARENT_IDS=[0,0]).
        monkeypatch.setattr(bot, "is_child", lambda uid: uid == 111)
        monkeypatch.setattr(bot, "is_parent", lambda uid: uid in (222, 333))
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        assert bot._user_display_name(111) == "Motya"

    def test_csv_export_uses_member_names(self, monkeypatch):
        import bot
        from database import add_member, add_measurement, create_family, get_all_measurements
        monkeypatch.setattr(bot, "CHILD_NAME", "EnvChild")
        f2 = create_family(TEST_DB, "Вторая")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        add_member(TEST_DB, 500, f2, "parent", "Олег")
        add_measurement(TEST_DB, 250, "morning", 700, 500, family_id=f2)
        rows = get_all_measurements(TEST_DB, 700, family_id=f2)
        content = bot.build_csv_content(
            rows, target=260, include_summary=False,
            child_id=700, child_name="Маша", family_id=f2)
        assert "Олег" in content
        assert "EnvChild" not in content

    def test_show_history_family2_marks_parent_author(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        from database import add_member, create_family
        f2 = create_family(TEST_DB, "Вторая")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        add_member(TEST_DB, 500, f2, "parent", "Олег")
        row = {"id": 1, "pef_value": 240, "time_of_day": "morning",
               "measured_at": "2026-09-01 08:00:00", "added_by": 500,
               "note": None, "source": "manual"}
        cb = MagicMock()
        cb.from_user.id = 500
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.delete = AsyncMock()
        cb.message.answer = AsyncMock()
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        member = {"telegram_id": 500, "family_id": f2, "role": "parent",
                  "name": "Олег", "active_child_id": 700}
        with patch.object(bot, "get_measurements_paginated",
                          return_value=([row], 1, 1)), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "respond", side_effect=fake_respond):
            asyncio.run(bot._show_history(cb, page=1, member=member))

        assert "👨" in sent.get("text", "")


class TestChildSelector:
    """SP3C Task 6: parents pick the active child when the family has >1."""

    def _cb(self, uid, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_main_menu_has_child_button_when_multi(self):
        import bot
        kb = bot.kb_main(is_parent_user=True, show_child_button=True)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "pick_child" in cbs

    def test_main_menu_no_child_button_otherwise(self):
        import bot
        for is_parent, show in ((True, False), (False, True)):
            kb = bot.kb_main(is_parent_user=is_parent, show_child_button=show)
            cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
            assert "pick_child" not in cbs

    def test_set_child_sets_active(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.data = "set_child_700"; cb.from_user.id = 500
        cb.answer = AsyncMock(); cb.message = MagicMock()
        cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        with patch.object(bot, "set_active_child", return_value=True) as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cb_set_child(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        m.assert_called_once_with(bot.DB_PATH, 500, 700)
        menu.assert_awaited()

    def test_set_child_rejected_for_child_member(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.data = "set_child_700"; cb.from_user.id = 700
        cb.answer = AsyncMock()
        with patch.object(bot, "set_active_child") as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cb_set_child(
                cb, member={"role": "child", "telegram_id": 700, "family_id": 2}))
        m.assert_not_called()
        menu.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_set_child_malformed_payload_alerts(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.data = "set_child_abc"; cb.from_user.id = 500
        cb.answer = AsyncMock()
        with patch.object(bot, "set_active_child") as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cb_set_child(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        m.assert_not_called()
        menu.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_pick_child_lists_children_with_set_callbacks(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(500, "pick_child")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["kb"] = kb

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "list_family_children",
                          return_value=[{"telegram_id": 700, "name": "Маша"},
                                        {"telegram_id": 701, "name": "Петя"}]), \
             patch.object(bot, "_ctx", new=AsyncMock(return_value=(2, 700))):
            asyncio.run(bot.cb_pick_child(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))

        cbs = [b.callback_data for row in sent["kb"].inline_keyboard for b in row]
        assert "set_child_700" in cbs and "set_child_701" in cbs

    def test_pick_child_rejected_for_child_member(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        cb = self._cb(700, "pick_child")
        with patch.object(bot, "respond", new=AsyncMock()) as respond:
            asyncio.run(bot.cb_pick_child(
                cb, member={"role": "child", "telegram_id": 700, "family_id": 2}))
        respond.assert_not_awaited()
        cb.answer.assert_awaited()

    def test_children_screen_marks_active_child(self):
        import asyncio, bot
        from unittest.mock import patch
        cb = self._cb(500, "children")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "list_child_cards", return_value=[]), \
             patch.object(bot, "list_family_children",
                          return_value=[{"telegram_id": 700, "name": "Маша"},
                                        {"telegram_id": 701, "name": "Петя"}]), \
             patch.object(bot, "resolve_active_child", return_value=701):
            asyncio.run(bot.cb_children(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))

        assert "✅" in sent.get("text", "")
        assert "Петя" in sent.get("text", "")


class TestGateRemoved:
    """SP3C Task 6: the 2B interim gate must be fully removed from bot.py."""

    def test_no_reg_soon_in_source(self):
        import pathlib
        assert "REG_SOON_MESSAGE" not in pathlib.Path("bot.py").read_text()

    def test_no_reg_soon_symbol(self):
        import bot
        assert not hasattr(bot.MemberMiddleware, "REG_SOON_MESSAGE")
        assert not hasattr(bot, "_is_reg_command")


class TestMigrationDryRun:
    """SP3D: dry-run reports a migration without touching the source DB."""

    def test_dry_run_reports_without_touching_source(self):
        from database import add_measurement
        from scripts.migration_dry_run import dry_run
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        before = open(TEST_DB, "rb").read()
        report = dry_run(TEST_DB)
        after = open(TEST_DB, "rb").read()
        assert before == after, "dry_run modified the source database"
        assert "measurements" in report["tables"]
        assert report["tables"]["measurements"] == 1
        assert report["version_before"] == report["version_after"]

    def test_dry_run_reports_orphan_child_ids(self):
        from database import add_measurement
        from scripts.migration_dry_run import dry_run
        add_measurement(TEST_DB, 250, "morning", 987654, 222)
        report = dry_run(TEST_DB)
        assert 987654 in report["orphan_child_ids"]

    def test_dry_run_migrates_a_copy_of_a_legacy_db(self, tmp_path):
        from database import SCHEMA_VERSION
        from scripts.migration_dry_run import dry_run
        legacy = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, pef_value INTEGER, time_of_day TEXT, "
            "measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, added_by INTEGER, "
            "note TEXT, source TEXT DEFAULT 'manual')"
        )
        conn.execute(
            "INSERT INTO measurements (user_id, pef_value, time_of_day) "
            "VALUES (111, 245, 'morning')"
        )
        conn.commit()
        conn.close()
        before = open(legacy, "rb").read()
        report = dry_run(legacy)
        after = open(legacy, "rb").read()
        assert before == after, "dry_run modified the source database"
        assert report["version_before"] < SCHEMA_VERSION
        assert report["version_after"] == SCHEMA_VERSION
        assert report["tables"]["measurements"] == 1

    @staticmethod
    def _legacy_measurements_table(conn):
        conn.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, pef_value INTEGER, time_of_day TEXT, "
            "measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, added_by INTEGER, "
            "note TEXT, source TEXT DEFAULT 'manual')"
        )

    def test_dry_run_sees_committed_rows_in_a_hot_wal(self, tmp_path):
        """A row committed to WAL but not yet checkpointed must be reported."""
        from scripts.migration_dry_run import dry_run
        db = str(tmp_path / "hot.db")
        writer = sqlite3.connect(db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            self._legacy_measurements_table(writer)
            writer.execute(
                "INSERT INTO measurements (user_id, pef_value, time_of_day) "
                "VALUES (111, 245, 'morning')"
            )
            writer.commit()
            assert os.path.exists(db + "-wal"), "setup: expected a hot WAL"
            before_bytes = open(db, "rb").read()
            before_sidecars = {p for p in (db + "-wal", db + "-shm") if os.path.exists(p)}
            report = dry_run(db)
            assert report["tables"]["measurements"] == 1, "hot WAL row was dropped"
            assert open(db, "rb").read() == before_bytes
            after_sidecars = {p for p in (db + "-wal", db + "-shm") if os.path.exists(p)}
            assert after_sidecars == before_sidecars, "dry_run touched source sidecars"
        finally:
            writer.close()

    def test_dry_run_creates_no_sidecar_next_to_clean_source(self, tmp_path):
        from scripts.migration_dry_run import dry_run
        db = str(tmp_path / "clean.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        self._legacy_measurements_table(conn)
        conn.commit()
        conn.close()  # last close checkpoints, so the source is clean
        assert not os.path.exists(db + "-wal")
        assert not os.path.exists(db + "-shm")
        before = open(db, "rb").read()
        dry_run(db)
        assert open(db, "rb").read() == before, "dry_run modified the source database"
        assert not os.path.exists(db + "-wal"), "dry_run created a -wal beside the source"
        assert not os.path.exists(db + "-shm"), "dry_run created a -shm beside the source"

    @staticmethod
    def _snapshot_hot_wal(db):
        """Build ``db`` as a WAL database with a committed-but-uncheckpointed
        row and **no open connections**: the triple is snapshotted off a live
        copy so ``-wal``/``-shm`` outlive the writer, while ``db``'s own close
        never checkpoints."""
        import shutil
        live = db + ".live"
        writer = sqlite3.connect(live)
        writer.execute("PRAGMA journal_mode=WAL")
        TestMigrationDryRun._legacy_measurements_table(writer)
        writer.execute(
            "INSERT INTO measurements (user_id, pef_value, time_of_day) "
            "VALUES (111, 245, 'morning')"
        )
        writer.commit()
        shutil.copyfile(live, db)
        shutil.copyfile(live + "-wal", db + "-wal")
        shutil.copyfile(live + "-shm", db + "-shm")
        writer.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(live + suffix):
                os.remove(live + suffix)

    def test_dry_run_as_only_connection_leaves_hot_wal_source_untouched(self, tmp_path):
        """Regression: a read-write connect would checkpoint the source when
        dry_run's connection (the only one) closes, rewriting the main file and
        deleting ``-wal``/``-shm``."""
        from scripts.migration_dry_run import dry_run
        db = str(tmp_path / "only.db")
        self._snapshot_hot_wal(db)
        paths = (db, db + "-wal", db + "-shm")
        assert all(os.path.exists(p) for p in paths), "setup: expected a hot WAL triple"
        before = {p: open(p, "rb").read() for p in paths}

        report = dry_run(db)

        assert report["tables"]["measurements"] == 1, "hot WAL row was dropped"
        after = {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in paths}
        assert after == before, "dry_run modified the source database or its sidecars"


class TestReportPdf:
    def test_settings_has_report_button(self):
        import bot
        cbs = [b.callback_data for row in bot.kb_settings(260).inline_keyboard for b in row]
        assert "report" in cbs

    def test_report_period_keyboard(self):
        import bot
        cbs = [b.callback_data for row in bot.kb_report_periods().inline_keyboard for b in row]
        assert {"report_week", "report_month", "report_quarter"} <= set(cbs)

    def test_cb_report_parent_only(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock(); cb.from_user.id = 700; cb.answer = AsyncMock()
        asyncio.run(bot.cb_report(cb, member={"role": "child", "telegram_id": 700,
                                              "family_id": 1}))
        cb.answer.assert_awaited_once()
        args, kwargs = cb.answer.call_args
        assert "родителей" in args[0]
        assert kwargs.get("show_alert") is True

    def test_cb_report_period_parent_only(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock(); cb.from_user.id = 700; cb.data = "report_month"
        cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer_document = AsyncMock()
        asyncio.run(bot.cb_report_period(cb, member={"role": "child", "telegram_id": 700,
                                                     "family_id": 1}))
        cb.answer.assert_awaited_once()
        args, kwargs = cb.answer.call_args
        assert "родителей" in args[0]
        assert kwargs.get("show_alert") is True
        cb.message.answer_document.assert_not_awaited()

    def test_cb_report_period_sends_pdf(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.data = "report_month"
        cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        member = {"role": "parent", "telegram_id": 500, "family_id": 1}
        with patch.object(bot, "get_measurements_between", return_value=[{"pef_value": 250}]) as between, \
             patch.object(bot, "resolve_active_child", return_value=111), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_member", return_value={"name": "Motya"}), \
             patch.object(bot, "_build_report_pdf_async",
                          new=AsyncMock(return_value=b"%PDF-1.4")):
            asyncio.run(bot.cb_report_period(cb, member=member))
        cb.message.answer_document.assert_awaited()
        args, kwargs = between.call_args
        assert 111 in args, "report must query the active child"
        assert kwargs.get("family_id") == 1, "report must be scoped to the family"

    def test_cb_report_period_empty_alerts(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.data = "report_month"
        cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        member = {"role": "parent", "telegram_id": 500, "family_id": 1}
        with patch.object(bot, "get_measurements_between", return_value=[]), \
             patch.object(bot, "resolve_active_child", return_value=111), \
             patch.object(bot, "_build_report_pdf_async",
                          new=AsyncMock(return_value=b"%PDF-1.4")):
            asyncio.run(bot.cb_report_period(cb, member=member))
        cb.answer.assert_awaited()
        cb.message.answer_document.assert_not_awaited()


class TestAchievements:
    def test_schema_v5(self):
        import database
        assert database.SCHEMA_VERSION == 5

    def test_achievements_table(self):
        from database import init_db
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        assert "achievements" in names

    def test_unlock_is_idempotent(self):
        from database import init_db, unlock_achievements
        init_db(TEST_DB)
        first = unlock_achievements(TEST_DB, 111, {"streak_7", "total_100"}, "2026-09-21")
        second = unlock_achievements(TEST_DB, 111, {"streak_7"}, "2026-09-22")
        assert first == {"streak_7", "total_100"}
        assert second == set()

    def test_get_achievements(self):
        from database import init_db, unlock_achievements, get_achievements
        init_db(TEST_DB)
        unlock_achievements(TEST_DB, 111, {"streak_7"}, "2026-09-21")
        assert get_achievements(TEST_DB, 111) == {"streak_7": "2026-09-21"}

    def test_get_measurement_dates_distinct_sorted(self):
        from database import init_db, get_measurement_dates
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        for ts in ("2026-09-02 08:00:00", "2026-09-01 08:00:00",
                   "2026-09-01 20:00:00"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', ?, 222, 'manual')",
                (ts,))
        conn.commit()
        conn.close()
        assert get_measurement_dates(TEST_DB, 111) == ["2026-09-01", "2026-09-02"]

    def test_count_measurements_includes_auto(self):
        from database import init_db, count_measurements
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        for src in ("manual", "auto"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', "
                "'2026-09-01 08:00:00', 222, ?)", (src,))
        conn.commit()
        conn.close()
        assert count_measurements(TEST_DB, 111) == 2

    def test_backfill_inserts_earned_without_bot(self):
        from database import (init_db, add_member, _backfill_achievements,
                              get_connection, get_achievements)
        init_db(TEST_DB)
        add_member(TEST_DB, 111, 1, "child", "Motya")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', "
                "'2026-09-01 08:00:00', 222, 'manual')")
        conn.commit()
        conn.close()
        c = get_connection(TEST_DB)
        _backfill_achievements(c)
        c.commit()
        c.close()
        ach = get_achievements(TEST_DB, 111)
        assert "total_100" in ach
        assert "streak_7" not in ach  # все замеры в один день

    def test_backfill_runs_only_on_upgrade(self):
        from database import (init_db, add_member, get_connection,
                              get_achievements)
        init_db(TEST_DB)
        add_member(TEST_DB, 111, 1, "child", "Motya")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', "
                "'2026-09-01 08:00:00', 222, 'manual')")
        conn.execute("DELETE FROM achievements")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()
        init_db(TEST_DB)  # upgrade 4 -> 5 must backfill
        assert "total_100" in get_achievements(TEST_DB, 111)
        # A new child added AFTER the upgrade must NOT be backfilled by init_db.
        add_member(TEST_DB, 555, 1, "child", "Petya")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 555, 250, 'morning', "
                "'2026-09-02 08:00:00', 222, 'manual')")
        conn.commit()
        conn.close()
        init_db(TEST_DB)  # already v5 -> no backfill
        assert get_achievements(TEST_DB, 555) == {}

    def test_backfill_ignores_malformed_dates(self):
        from database import (init_db, add_member, get_connection,
                              _backfill_achievements)
        init_db(TEST_DB)
        add_member(TEST_DB, 111, 1, "child", "Motya")
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', NULL, 222, 'manual')")
        conn.commit()
        conn.close()
        c = get_connection(TEST_DB)
        _backfill_achievements(c)  # must not raise
        c.commit()
        c.close()

    def test_build_achievements_text_marks_unlocked(self):
        from bot import build_achievements_text
        text = build_achievements_text(7, 100)
        assert "7 дней подряд" in text and "100 замеров" in text
        assert "30 дней подряд" in text
        # streak_7 unlocked, streak_30 locked with progress 7/30
        assert "7/30" in text

    def test_kb_main_has_achievements(self):
        import bot
        for is_parent in (True, False):
            cbs = [b.callback_data for row in bot.kb_main(is_parent).inline_keyboard for b in row]
            assert "achievements" in cbs

    def test_cb_achievements_scoped(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        seen = {}
        def fake_dates(db, child_id, family_id=1):
            seen["child_id"] = child_id; seen["family_id"] = family_id
            return ["2026-09-20", "2026-09-19"]
        with patch.object(bot, "get_measurement_dates", side_effect=fake_dates), \
             patch.object(bot, "count_measurements", return_value=2), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()) as resp:
            asyncio.run(bot.cb_achievements(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert seen == {"child_id": 700, "family_id": 2}
        resp.assert_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

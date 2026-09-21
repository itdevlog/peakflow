import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import TZ_OFFSET
import gamification

# Часовой пояс — единый источник (config.py читает TZ_OFFSET из .env)
_TZ = timezone(timedelta(hours=TZ_OFFSET))

DEFAULT_FAMILY_ID = 1

# Версия схемы БД (PRAGMA user_version). 5 = геймификация (achievements).
SCHEMA_VERSION = 5


def _now():
    """Текущее время в настроенном часовом поясе."""
    return datetime.now(_TZ)


def _today_str():
    return _now().strftime("%Y-%m-%d")


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


_MEASUREMENTS_V2_DDL = """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        family_id INTEGER NOT NULL DEFAULT 1,
        child_id INTEGER NOT NULL,
        pef_value INTEGER NOT NULL,
        time_of_day TEXT NOT NULL CHECK(time_of_day IN ('morning', 'evening', 'unknown')),
        measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        added_by INTEGER,
        note TEXT,
        source TEXT DEFAULT 'manual'
    )
"""


def _rebuild_measurements_v2(conn):
    """Ensure measurements is v2 (fresh DB → create; v1 → rebuild)."""
    cols = _table_columns(conn, "measurements")
    if not cols:                      # fresh DB: create v2 directly
        conn.execute(_MEASUREMENTS_V2_DDL)
        return
    if "child_id" in cols and "family_id" in cols:
        return                        # already v2
    conn.execute("ALTER TABLE measurements RENAME TO measurements_v1")
    conn.execute(_MEASUREMENTS_V2_DDL)
    if "child_id" in cols:
        child_expr = "child_id"
    elif "user_id" in cols:
        child_expr = "user_id"
    else:
        raise ValueError(
            "cannot rebuild measurements: neither child_id nor user_id present"
        )
    family_expr = "COALESCE(family_id, ?)" if "family_id" in cols else "?"
    tod = "time_of_day" if "time_of_day" in cols else "'unknown'"
    added_by = "added_by" if "added_by" in cols else "NULL"
    note = "note" if "note" in cols else "NULL"
    source = "COALESCE(source, 'manual')" if "source" in cols else "'manual'"
    conn.execute(
        f"INSERT INTO measurements (id, family_id, child_id, pef_value, time_of_day, "
        f"measured_at, added_by, note, source) "
        f"SELECT id, {family_expr}, {child_expr}, pef_value, {tod}, measured_at, "
        f"{added_by}, {note}, {source} FROM measurements_v1",
        (DEFAULT_FAMILY_ID,)
    )
    conn.execute("DROP TABLE measurements_v1")


def _rebuild_settings_v2(conn):
    """v1->v2: settings(key PK) -> settings(family_id, key) PK."""
    cols = _table_columns(conn, "settings")
    if "family_id" in cols:
        return
    if cols:
        conn.execute("ALTER TABLE settings RENAME TO settings_v1")
    conn.execute("""
        CREATE TABLE settings (
            family_id INTEGER NOT NULL DEFAULT 1,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (family_id, key)
        )
    """)
    if cols:
        conn.execute(
            "INSERT INTO settings (family_id, key, value) SELECT ?, key, value FROM settings_v1",
            (DEFAULT_FAMILY_ID,)
        )
        conn.execute("DROP TABLE settings_v1")


_REMINDER_FLAGS = ("morning_reminder", "evening_reminder", "weekly_report",
                   "child_morning_reminder", "child_evening_reminder",
                   "auto_morning", "auto_evening")


def _rebuild_reminders_v2(conn):
    cols = _table_columns(conn, "reminders_sent")
    if "child_id" in cols:
        return
    if cols:
        conn.execute("ALTER TABLE reminders_sent RENAME TO reminders_v1")
    flags = ",\n        ".join(f"{f} INTEGER DEFAULT 0" for f in _REMINDER_FLAGS)
    conn.execute(f"""CREATE TABLE reminders_sent (
        child_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
        {flags}, PRIMARY KEY (child_id, date))""")
    if cols:
        present = [f for f in _REMINDER_FLAGS if f in cols]
        sel = ", ".join(present)
        import config
        seed_child = getattr(config, "CHILD_ID", 0) or 0
        conn.execute(
            f"INSERT INTO reminders_sent (child_id, date{', ' + sel if sel else ''}) "
            f"SELECT ?, date{', ' + sel if sel else ''} FROM reminders_v1",
            (seed_child,))
        conn.execute("DROP TABLE reminders_v1")


def _seed_default_family(conn):
    """Create family #1 and seed members from config *and* legacy users.

    Config (``.env``: ``CHILD_ID`` / ``PARENT_IDS`` / ``CHILD_NAME``) is
    authoritative for every ID it lists, because legacy ``users`` can be stale
    (prod: 35641953 owns 126 measurements yet is recorded there as a child).
    So config members are upserted first, then legacy ``users`` rows fill only
    the IDs config did not already cover (``INSERT OR IGNORE``). Idempotent:
    returns early when family #1 already exists.
    """
    existing = conn.execute(
        "SELECT id FROM families WHERE id = ?", (DEFAULT_FAMILY_ID,)
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO families (id, name) VALUES (?, ?)", (DEFAULT_FAMILY_ID, "Семья")
    )

    # 1. Config members first — authoritative on conflict.
    import config
    child_id = getattr(config, "CHILD_ID", 0)
    parent_ids = getattr(config, "PARENT_IDS", []) or []
    child_name = getattr(config, "CHILD_NAME", "Ребёнок")
    if child_id:
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'child', ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
            "role = excluded.role, name = excluded.name",
            (child_id, DEFAULT_FAMILY_ID, child_name)
        )
    for pid in parent_ids:
        if pid:
            conn.execute(
                "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'parent', 'Родитель') "
                "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
                "role = excluded.role, name = excluded.name",
                (pid, DEFAULT_FAMILY_ID)
            )

    # 2. Legacy users fill gaps only (config already inserted above).
    cols = _table_columns(conn, "users")
    if "user_id" in cols:
        # Materialise first: `conn` may be a cursor, and reusing it for INSERT
        # while iterating its SELECT truncates the loop after the first row.
        rows = conn.execute(
            "SELECT user_id, role, first_name, child_name FROM users"
        ).fetchall()
        for r in rows:
            role = (r["role"] or "").strip()
            if role not in ("parent", "child"):
                continue
            name = r["child_name"] or r["first_name"] or ""
            conn.execute(
                "INSERT OR IGNORE INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?)",
                (r["user_id"], DEFAULT_FAMILY_ID, role, name)
            )


def _migrate_to_v2(conn):
    """Idempotent v1 -> v2 migration; owns table creation for fresh DBs."""
    _rebuild_measurements_v2(conn)
    _rebuild_settings_v2(conn)
    _rebuild_reminders_v2(conn)


def _create_invites_v3(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            token TEXT PRIMARY KEY,
            family_id INTEGER NOT NULL REFERENCES families(id),
            role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invites_family ON invites(family_id)")


def _add_active_child_v4(conn):
    try:
        conn.execute("ALTER TABLE members ADD COLUMN active_child_id INTEGER")
    except sqlite3.OperationalError:
        pass


def _create_achievements_v5(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            child_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY (child_id, code)
        )
    """)


def _backfill_achievements(conn):
    """Persist already-earned achievements silently (no notifications).

    Runs only on the first upgrade to v5 so a deploy does not spam families.
    """
    today = _today_str()
    children = conn.execute(
        "SELECT telegram_id FROM members WHERE role = 'child'"
    ).fetchall()
    for row in children:
        child_id = row["telegram_id"]
        dates = [r["d"] for r in conn.execute(
            "SELECT DISTINCT substr(measured_at, 1, 10) AS d FROM measurements "
            "WHERE child_id = ? "
            "AND measured_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'",
            (child_id,)
        ).fetchall()]
        total = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE child_id = ?", (child_id,)
        ).fetchone()[0]
        earned = gamification.evaluate(gamification.longest_streak(dates), total)
        for code in earned:
            conn.execute(
                "INSERT OR IGNORE INTO achievements (child_id, code, unlocked_at) "
                "VALUES (?, ?, ?)", (child_id, code, today)
            )


def _needs_migration(probe) -> bool:
    """True when a non-empty DB does not yet have the v2 schema.

    Detected by schema state, not only ``PRAGMA user_version``: Task 1 bumped
    the version constant before this migration existed, so user_version alone
    is unreliable (Ruling A).
    """
    try:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.OperationalError:
        # Locked/busy DB: be conservative and back up rather than risk a
        # migration without one.
        return True
    except sqlite3.DatabaseError:
        return False  # not a valid SQLite file — nothing safe to back up
    if version < SCHEMA_VERSION:
        return True
    mcols = {r[1] for r in probe.execute("PRAGMA table_info(measurements)")}
    if mcols and not {"child_id", "family_id"} <= mcols:
        return True
    members = probe.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='members'"
    ).fetchone()
    return members is None


def init_db(db_path: str):
    # Back up a non-empty pre-v2 database before migrating it.
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        probe = sqlite3.connect(db_path)
        try:
            needs_backup = _needs_migration(probe)
        finally:
            probe.close()
        if needs_backup:
            backup_db(db_path, db_path + ".v1.bak")

    conn = get_connection(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            telegram_id INTEGER PRIMARY KEY,
            family_id INTEGER NOT NULL REFERENCES families(id),
            role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_members_family ON members(family_id)")

    try:
        # Seed + v1->v2 rebuild run atomically so a mid-rebuild failure cannot
        # strand *_v1 tables (Ruling B).
        c.execute("BEGIN IMMEDIATE")
        old_version = c.execute("PRAGMA user_version").fetchone()[0]
        _seed_default_family(c)
        _migrate_to_v2(c)
        _create_invites_v3(c)
        _add_active_child_v4(c)
        _create_achievements_v5(c)
        if old_version < 5:
            _backfill_achievements(c)

        # Default target PEF if not set
        c.execute(
            "INSERT OR IGNORE INTO settings (family_id, key, value) VALUES (?, 'target_pef', '260')",
            (DEFAULT_FAMILY_ID,)
        )

        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_meas_family_child_time
            ON measurements(family_id, child_id, measured_at)
        """)

        # Record schema version.
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# Measurements CRUD
# ============================================================================
def add_measurement(db_path: str, pef_value: int, time_of_day: str,
                    child_id: int, added_by: int, source: str = "manual",
                    family_id: int = DEFAULT_FAMILY_ID) -> int:
    """Add measurement. Returns new measurement ID."""
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO measurements (family_id, pef_value, time_of_day, child_id, added_by, measured_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (family_id, pef_value, time_of_day, child_id, added_by, now_str, source)
    )
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return mid


def set_note(db_path: str, measurement_id: int, note: str, child_id: int,
             family_id: int = DEFAULT_FAMILY_ID) -> bool:
    """Attach a note to a measurement (record must belong to child)."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "UPDATE measurements SET note = ? WHERE id = ? AND child_id = ? AND family_id = ?",
        (note, measurement_id, child_id, family_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


_AUTO_FILTER = "(source IS NULL OR source != 'auto')"


def get_last_of_tod(db_path: str, child_id: int, time_of_day: str,
                    family_id: int = DEFAULT_FAMILY_ID) -> Optional[dict]:
    """Last real (non-auto) measurement of the given time of day."""
    conn = get_connection(db_path)
    row = conn.execute(
        f"SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND time_of_day = ? "
        f"AND {_AUTO_FILTER} ORDER BY id DESC LIMIT 1",
        (child_id, family_id, time_of_day)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def replace_auto_measurement(db_path: str, child_id: int, time_of_day: str,
                            pef_value: int, added_by: int,
                            family_id: int = DEFAULT_FAMILY_ID) -> "int | bool":
    """Overwrite today's auto-carry record with a real measurement.

    Returns the replaced row's id, or False when there was no auto record.
    """
    today = _today_str()
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "UPDATE measurements SET pef_value = ?, added_by = ?, source = 'manual', "
        "measured_at = ? WHERE child_id = ? AND family_id = ? AND time_of_day = ? "
        "AND source = 'auto' AND measured_at LIKE ? RETURNING id",
        (pef_value, added_by, now_str, child_id, family_id, time_of_day, f"{today}%")
    ).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else False


def add_or_replace_measurement(db_path: str, pef_value: int, time_of_day: str,
                               child_id: int, added_by: int, force: bool = False,
                               source: str = "manual",
                               family_id: int = DEFAULT_FAMILY_ID) -> tuple:
    """Atomically add a measurement for a slot, replacing today's auto record.

    Returns ``(id, status)`` where status is:

    - ``"ok"`` — row inserted or an existing auto record replaced;
    - ``"exists"`` — a real (non-auto) measurement already exists in this slot
      today and ``force`` is False; the returned id is the existing row.

    The check-then-insert runs under ``BEGIN IMMEDIATE`` so two concurrent
    callers (threads/processes) cannot both create a row for the same slot.
    """
    today = _today_str()
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not force:
            existing = conn.execute(
                f"SELECT id FROM measurements WHERE child_id = ? AND family_id = ? "
                f"AND time_of_day = ? AND measured_at LIKE ? AND {_AUTO_FILTER} "
                f"ORDER BY id DESC LIMIT 1",
                (child_id, family_id, time_of_day, f"{today}%")
            ).fetchone()
            if existing:
                conn.rollback()
                return existing["id"], "exists"

        auto = conn.execute(
            "SELECT id FROM measurements WHERE child_id = ? AND family_id = ? AND time_of_day = ? "
            "AND source = 'auto' AND measured_at LIKE ? ORDER BY id DESC LIMIT 1",
            (child_id, family_id, time_of_day, f"{today}%")
        ).fetchone()
        if auto:
            conn.execute(
                "UPDATE measurements SET pef_value = ?, added_by = ?, source = ?, "
                "measured_at = ? WHERE id = ?",
                (pef_value, added_by, source, now_str, auto["id"])
            )
            conn.commit()
            return auto["id"], "ok"

        cursor = conn.execute(
            "INSERT INTO measurements (family_id, pef_value, time_of_day, child_id, added_by, measured_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (family_id, pef_value, time_of_day, child_id, added_by, now_str, source)
        )
        mid = cursor.lastrowid
        conn.commit()
        return mid, "ok"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def edit_measurement(db_path: str, measurement_id: int, new_value: int, child_id: int,
                     family_id: int = DEFAULT_FAMILY_ID) -> bool:
    """Edit last measurement (only if it belongs to child)."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "UPDATE measurements SET pef_value = ? WHERE id = ? AND child_id = ? AND family_id = ?",
        (new_value, measurement_id, child_id, family_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def delete_measurement(db_path: str, measurement_id: int, child_id: int,
                       family_id: int = DEFAULT_FAMILY_ID) -> bool:
    conn = get_connection(db_path)
    cur = conn.execute(
        "DELETE FROM measurements WHERE id = ? AND child_id = ? AND family_id = ?",
        (measurement_id, child_id, family_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_measurement_by_id(db_path: str, measurement_id: int,
                          family_id: int = DEFAULT_FAMILY_ID,
                          child_id: Optional[int] = None) -> Optional[dict]:
    """Fetch a single measurement by primary key within a family.

    When ``child_id`` is given the lookup is additionally narrowed to that
    child (defense in depth); ``None`` keeps the original family-wide behavior.
    """
    conn = get_connection(db_path)
    if child_id is None:
        row = conn.execute(
            "SELECT * FROM measurements WHERE id = ? AND family_id = ?",
            (measurement_id, family_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM measurements WHERE id = ? AND family_id = ? AND child_id = ?",
            (measurement_id, family_id, child_id)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_previous_of_tod(db_path: str, child_id: int, time_of_day: str,
                        before_id: int, family_id: int = DEFAULT_FAMILY_ID) -> Optional[dict]:
    """Latest real measurement of this time of day with id < before_id.

    The "change" for a measurement compares morning↔morning (evening↔evening)
    rather than against an unrelated slot.
    """
    conn = get_connection(db_path)
    row = conn.execute(
        f"SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND time_of_day = ? "
        f"AND id < ? AND {_AUTO_FILTER} ORDER BY id DESC LIMIT 1",
        (child_id, family_id, time_of_day, before_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_recent_measurements(db_path: str, child_id: int, limit: int = 2,
                            family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Latest ``limit`` real (non-auto) measurements, newest first.

    Used for status/diff so handlers don't scan the whole history.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND {_AUTO_FILTER} "
        "ORDER BY id DESC LIMIT ?",
        (child_id, family_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_measurement(db_path: str, child_id: int,
                         family_id: int = DEFAULT_FAMILY_ID) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? ORDER BY id DESC LIMIT 1",
        (child_id, family_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_measurements(db_path: str, child_id: int, include_auto: bool = False,
                         family_id: int = DEFAULT_FAMILY_ID) -> list:
    conn = get_connection(db_path)
    if include_auto:
        rows = conn.execute(
            "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? ORDER BY id DESC",
            (child_id, family_id)
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND {_AUTO_FILTER} "
            "ORDER BY id DESC",
            (child_id, family_id)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Today / This week
# ============================================================================
def get_today_measurements(db_path: str, child_id: int,
                           family_id: int = DEFAULT_FAMILY_ID) -> list:
    today = _today_str()
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND measured_at LIKE ? "
        "ORDER BY measured_at ASC",
        (child_id, family_id, f"{today}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_today_measurement(db_path: str, child_id: int, time_of_day: str,
                          skip_auto: bool = False, family_id: int = DEFAULT_FAMILY_ID) -> bool:
    today = _today_str()
    conn = get_connection(db_path)
    if skip_auto:
        row = conn.execute(
            f"SELECT COUNT(*) FROM measurements WHERE child_id = ? AND family_id = ? "
            f"AND measured_at LIKE ? AND time_of_day = ? AND {_AUTO_FILTER}",
            (child_id, family_id, f"{today}%", time_of_day)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE child_id = ? AND family_id = ? "
            "AND measured_at LIKE ? AND time_of_day = ?",
            (child_id, family_id, f"{today}%", time_of_day)
        ).fetchone()
    conn.close()
    return row[0] > 0


def get_last_two_weeks(db_path: str, child_id: int,
                       family_id: int = DEFAULT_FAMILY_ID) -> tuple:
    """Returns (this_week_measurements, prev_week_measurements)."""
    now = _now()
    # This week: Monday to Sunday
    this_monday = now - timedelta(days=now.weekday())
    this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_monday = this_monday - timedelta(days=7)

    conn = get_connection(db_path)

    this_week = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND measured_at >= ?",
        (child_id, family_id, this_monday.strftime("%Y-%m-%d %H:%M:%S"))
    ).fetchall()

    prev_week = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND measured_at >= ? AND measured_at < ?",
        (child_id, family_id, prev_monday.strftime("%Y-%m-%d %H:%M:%S"),
         this_monday.strftime("%Y-%m-%d %H:%M:%S"))
    ).fetchall()

    conn.close()
    return [dict(r) for r in this_week], [dict(r) for r in prev_week]


# ============================================================================
# Pagination
# ============================================================================
def get_measurements_paginated(db_path: str, child_id: int, page: int = 1, per_page: int = 10,
                               family_id: int = DEFAULT_FAMILY_ID) -> tuple:
    conn = get_connection(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE child_id = ? AND family_id = ?",
        (child_id, family_id)
    ).fetchone()[0]

    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? "
        "ORDER BY measured_at DESC LIMIT ? OFFSET ?",
        (child_id, family_id, per_page, offset)
    ).fetchall()
    conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)
    return [dict(r) for r in rows], total, total_pages


# ============================================================================
# Chart data
# ============================================================================
def get_measurements_for_chart(db_path: str, child_id: int, days: int = 30,
                               family_id: int = DEFAULT_FAMILY_ID) -> list:
    since = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT pef_value, time_of_day, measured_at FROM measurements "
        f"WHERE child_id = ? AND family_id = ? AND measured_at >= ? AND {_AUTO_FILTER} "
        f"ORDER BY measured_at ASC",
        (child_id, family_id, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Statistics
# ============================================================================
def get_stats(db_path: str, child_id: int, family_id: int = DEFAULT_FAMILY_ID) -> dict:
    """Full statistics for a child (auto-carry records excluded)."""
    all_m = get_all_measurements(db_path, child_id, family_id=family_id)
    if not all_m:
        return {"total": 0}

    values = [m["pef_value"] for m in all_m]
    morning = [m["pef_value"] for m in all_m if m["time_of_day"] == "morning"]
    evening = [m["pef_value"] for m in all_m if m["time_of_day"] == "evening"]

    stats = {
        "total": len(all_m),
        "avg": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "latest": values[0],
        "morning_avg": sum(morning) / len(morning) if morning else None,
        "morning_count": len(morning),
        "evening_avg": sum(evening) / len(evening) if evening else None,
        "evening_count": len(evening),
        "today_count": len(get_today_measurements(db_path, child_id, family_id=family_id)),
    }

    # Trend: last 3 vs prev 3
    if len(values) >= 6:
        recent = sum(values[:3]) / 3
        older = sum(values[3:6]) / 3
        stats["trend"] = recent - older
    else:
        stats["trend"] = None

    return stats


# ============================================================================
# Reminder tracking
# ============================================================================
def mark_reminder_sent(db_path: str, date_str: str, reminder_type: str, child_id: int) -> None:
    """Mark that a reminder was sent today. Other flags stay intact."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO reminders_sent (child_id, date) VALUES (?, ?) "
        "ON CONFLICT(child_id, date) DO NOTHING",
        (child_id, date_str)
    )
    column = _reminder_column(reminder_type)
    if column:
        conn.execute(
            f"UPDATE reminders_sent SET {column} = 1 WHERE child_id = ? AND date = ?",
            (child_id, date_str)
        )
    conn.commit()
    conn.close()


def was_reminder_sent(db_path: str, date_str: str, reminder_type: str, child_id: int) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM reminders_sent WHERE child_id = ? AND date = ?", (child_id, date_str)
    ).fetchone()
    conn.close()
    if not row:
        return False
    column = _reminder_column(reminder_type)
    if column:
        return bool(row[column]) if column in row.keys() else False
    return False


def _reminder_column(reminder_type: str) -> Optional[str]:
    """Map reminder type to a reminders_sent column (None = unknown type)."""
    mapping = {
        "morning_missing": "morning_reminder",
        "evening_missing": "evening_reminder",
        "weekly": "weekly_report",
        "child_morning": "child_morning_reminder",
        "child_evening": "child_evening_reminder",
        "auto_morning": "auto_morning",
        "auto_evening": "auto_evening",
    }
    return mapping.get(reminder_type)


# ============================================================================
# Settings
# ============================================================================
def get_setting(db_path: str, key: str, default: str = "",
                family_id: int = DEFAULT_FAMILY_ID) -> str:
    """Get a setting value by key within a family."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT value FROM settings WHERE family_id = ? AND key = ?", (family_id, key)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(db_path: str, key: str, value: str,
                family_id: int = DEFAULT_FAMILY_ID):
    """Set a setting value within a family."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO settings (family_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(family_id, key) DO UPDATE SET value = excluded.value",
        (family_id, key, value)
    )
    conn.commit()
    conn.close()


def get_effective_target(db_path: str, fallback: int,
                         family_id: int = DEFAULT_FAMILY_ID) -> int:
    """Target PEF from DB settings, falling back to the env-configured value.

    Single source of truth shared by bot and web layer.
    """
    try:
        val = int(get_setting(db_path, "target_pef", str(fallback), family_id=family_id))
        return val if val > 0 else (fallback or 300)
    except (TypeError, ValueError):
        return fallback or 300


# ============================================================================
# Families / members (multi-tenant)
# ============================================================================
def create_family(db_path: str, name: str) -> int:
    conn = get_connection(db_path)
    cur = conn.execute("INSERT INTO families (name) VALUES (?)", (name,))
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid


def get_family(db_path: str, family_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM families WHERE id = ?", (family_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_member(db_path: str, telegram_id: int, family_id: int, role: str, name: str) -> None:
    if role not in ("parent", "child"):
        raise ValueError(f"invalid role: {role!r}")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
            "role = excluded.role, name = excluded.name",
            (telegram_id, family_id, role, name)
        )
        conn.commit()
    finally:
        conn.close()


def get_member(db_path: str, telegram_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM members WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_family_children(db_path: str, family_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM members WHERE family_id = ? AND role = 'child' ORDER BY telegram_id",
        (family_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_family_parents(db_path: str, family_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM members WHERE family_id = ? AND role = 'parent' ORDER BY telegram_id",
        (family_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_families(db_path: str) -> list:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM families ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_family_children(db_path: str, family_id: int) -> int:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM members WHERE family_id = ? AND role = 'child'", (family_id,)
    ).fetchone()
    conn.close()
    return row[0]


def set_active_child(db_path: str, telegram_id: int, child_id: int) -> bool:
    member = get_member(db_path, telegram_id)
    if not member or member["role"] != "parent":
        return False
    child = get_member(db_path, child_id)
    if not child or child["role"] != "child" or child["family_id"] != member["family_id"]:
        return False
    conn = get_connection(db_path)
    conn.execute("UPDATE members SET active_child_id = ? WHERE telegram_id = ?", (child_id, telegram_id))
    conn.commit()
    conn.close()
    return True


def resolve_active_child(db_path: str, member, children=None) -> Optional[int]:
    if not member:
        return None
    if member["role"] == "child":
        return member["telegram_id"]
    if children is None:
        children = list_family_children(db_path, member["family_id"])
    ids = [c["telegram_id"] for c in children]
    selected = member.get("active_child_id") if hasattr(member, "get") else None
    if selected in ids:
        return selected
    return ids[0] if ids else None


# ============================================================================
# Invites (registration tokens)
# ============================================================================
def create_invite(db_path: str, family_id: int, role: str, name: str = "") -> str:
    conn = get_connection(db_path)
    try:
        for _ in range(5):
            token = secrets.token_urlsafe(8)
            try:
                conn.execute(
                    "INSERT INTO invites (token, family_id, role, name) VALUES (?, ?, ?, ?)",
                    (token, family_id, role, name)
                )
                conn.commit()
                return token
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Не удалось создать уникальный invite-токен")
    finally:
        conn.close()


def get_invite(db_path: str, token: str) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_family_invite(db_path: str, family_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM invites WHERE family_id = ? AND role = 'parent' "
        "ORDER BY created_at DESC LIMIT 1",
        (family_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_child_cards(db_path: str, family_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM invites WHERE family_id = ? AND role = 'child' ORDER BY created_at",
        (family_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_invite(db_path: str, token: str, family_id: int = DEFAULT_FAMILY_ID) -> bool:
    conn = get_connection(db_path)
    cur = conn.execute(
        "DELETE FROM invites WHERE token = ? AND family_id = ?", (token, family_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def regenerate_family_invite(db_path: str, family_id: int) -> str:
    conn = get_connection(db_path)
    conn.execute("DELETE FROM invites WHERE family_id = ? AND role = 'parent'", (family_id,))
    conn.commit()
    conn.close()
    return create_invite(db_path, family_id, "parent")


# ============================================================================
# Registration (atomic family creation / invite join)
# ============================================================================
def create_family_with_owner(db_path: str, telegram_id: int, name: str) -> int:
    """Create a family with the caller as parent. Idempotent per telegram_id."""
    existing = get_member(db_path, telegram_id)
    if existing:
        return existing["family_id"]
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("INSERT INTO families (name) VALUES (?)", (name,))
        fid = cur.lastrowid
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'parent', ?)",
            (telegram_id, fid, name)
        )
        token = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO invites (token, family_id, role, name) VALUES (?, ?, 'parent', '')",
            (token, fid)
        )
        conn.commit()
        return fid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def join_by_invite(db_path: str, token: str, telegram_id: int,
                   name: Optional[str] = None) -> Optional[dict]:
    """Join a family by invite token. Returns {family_id, role, name} or None."""
    invite = get_invite(db_path, token)
    if not invite:
        return None
    role = invite["role"]
    member_name = name if name is not None else (
        invite["name"] or ("Родитель" if role == "parent" else "Ребёнок")
    )
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
            "role = excluded.role, name = excluded.name",
            (telegram_id, invite["family_id"], role, member_name)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"family_id": invite["family_id"], "role": role, "name": member_name}


# ============================================================================
# Reminder hours (configurable via settings)
# ============================================================================
REMINDER_HOURS_DEFAULT = {
    "child_morning": 8,
    "child_evening": 20,
    "parent_morning": 10,
    "parent_evening": 22,
}


def get_reminder_hours(db_path: str, family_id: int = DEFAULT_FAMILY_ID) -> dict:
    """Reminder hours from settings; defaults when missing/invalid."""
    result = dict(REMINDER_HOURS_DEFAULT)
    for key in result:
        try:
            val = int(get_setting(db_path, f"reminder_{key}", "", family_id=family_id))
            if 0 <= val <= 23:
                result[key] = val
        except ValueError:
            pass
    return result


def validate_reminder_hours(hours: dict) -> Optional[str]:
    """Return an error message if the escalation order is wrong, else None.

    Parents must be pinged *after* the child's reminder, otherwise the same
    minute both pings the child and escalates/auto-fills — the child never
    gets a chance to measure.
    """
    if hours.get("parent_morning", 0) <= hours.get("child_morning", 0):
        return "Час родителям (утро) должен быть позже часа ребёнку (утро)."
    if hours.get("parent_evening", 0) <= hours.get("child_evening", 0):
        return "Час родителям (вечер) должен быть позже часа ребёнку (вечер)."
    return None


# ============================================================================
# Backup
# ============================================================================
def backup_db(db_path: str, dest_path: str):
    """Consistent SQLite backup (safe under WAL)."""
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()


def backup_family_db(db_path: str, dest_path: str, family_id: int) -> None:
    """Write a self-contained SQLite backup containing only one family's data.

    The whole-file ``backup_db`` is multi-tenant: handing that file to one
    family would leak every other family's measurements. Here the destination
    gets the full schema but only the rows belonging to ``family_id``. Schema
    is created directly (not via ``init_db``) so no family #1 seed rows are
    written.
    """
    if os.path.exists(dest_path):
        os.remove(dest_path)
    dst = sqlite3.connect(dest_path)
    try:
        dst.row_factory = sqlite3.Row
        dst.executescript(_FAMILY_BACKUP_DDL)
        dst.execute("ATTACH DATABASE ? AS src", (db_path,))
        try:
            dst.execute(
                "INSERT INTO families SELECT * FROM src.families WHERE id = ?",
                (family_id,)
            )
            dst.execute(
                "INSERT INTO members SELECT * FROM src.members WHERE family_id = ?",
                (family_id,)
            )
            dst.execute(
                "INSERT INTO measurements SELECT * FROM src.measurements "
                "WHERE family_id = ?",
                (family_id,)
            )
            dst.execute(
                "INSERT INTO settings SELECT * FROM src.settings "
                "WHERE family_id = ?",
                (family_id,)
            )
            dst.execute(
                "INSERT INTO invites SELECT * FROM src.invites WHERE family_id = ?",
                (family_id,)
            )
            dst.execute(
                "INSERT INTO reminders_sent SELECT * FROM src.reminders_sent "
                "WHERE child_id IN (SELECT telegram_id FROM src.members "
                "WHERE family_id = ? AND role = 'child')",
                (family_id,)
            )
            dst.commit()
        finally:
            dst.execute("DETACH DATABASE src")
    finally:
        dst.close()


# Exact schema for a family-scoped backup (mirrors init_db's v4 tables).
_FAMILY_BACKUP_DDL = """
    CREATE TABLE IF NOT EXISTS families (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS members (
        telegram_id INTEGER PRIMARY KEY,
        family_id INTEGER NOT NULL REFERENCES families(id),
        role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
        name TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        active_child_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_members_family ON members(family_id);
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        family_id INTEGER NOT NULL DEFAULT 1,
        child_id INTEGER NOT NULL,
        pef_value INTEGER NOT NULL,
        time_of_day TEXT NOT NULL CHECK(time_of_day IN ('morning', 'evening', 'unknown')),
        measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        added_by INTEGER,
        note TEXT,
        source TEXT DEFAULT 'manual'
    );
    CREATE INDEX IF NOT EXISTS idx_meas_family_child_time
        ON measurements(family_id, child_id, measured_at);
    CREATE TABLE IF NOT EXISTS settings (
        family_id INTEGER NOT NULL DEFAULT 1,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (family_id, key)
    );
    CREATE TABLE IF NOT EXISTS invites (
        token TEXT PRIMARY KEY,
        family_id INTEGER NOT NULL REFERENCES families(id),
        role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
        name TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_invites_family ON invites(family_id);
    CREATE TABLE IF NOT EXISTS reminders_sent (
        child_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
        morning_reminder INTEGER DEFAULT 0,
        evening_reminder INTEGER DEFAULT 0,
        weekly_report INTEGER DEFAULT 0,
        child_morning_reminder INTEGER DEFAULT 0,
        child_evening_reminder INTEGER DEFAULT 0,
        auto_morning INTEGER DEFAULT 0,
        auto_evening INTEGER DEFAULT 0,
        PRIMARY KEY (child_id, date)
    );
"""


# ============================================================================
# Month selections (chart navigation, CSV by period)
# ============================================================================
def get_measurements_for_month(db_path: str, child_id: int, year: int, month: int,
                               include_auto: bool = False,
                               family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Measurements within a calendar month (auto excluded unless include_auto)."""
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    conn = get_connection(db_path)
    if include_auto:
        rows = conn.execute(
            "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? "
            "AND measured_at >= ? AND measured_at < ? ORDER BY measured_at ASC",
            (child_id, family_id, start, end)
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM measurements WHERE child_id = ? AND family_id = ? "
            f"AND measured_at >= ? AND measured_at < ? AND {_AUTO_FILTER} ORDER BY measured_at ASC",
            (child_id, family_id, start, end)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_months(db_path: str, child_id: int,
                         family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Sorted list of (year, month) having measurements, oldest first."""
    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT DISTINCT substr(measured_at, 1, 7) AS ym FROM measurements "
        f"WHERE child_id = ? AND family_id = ? AND {_AUTO_FILTER} ORDER BY ym ASC",
        (child_id, family_id)
    ).fetchall()
    conn.close()
    months = []
    for r in rows:
        y, m = r["ym"].split("-")
        months.append((int(y), int(m)))
    return months


def get_measurements_between(db_path: str, child_id: int,
                             date_from: str, date_to: str,
                             family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Measurements in [date_from 00:00, date_to 23:59:59] (auto included — export shows them)."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? AND measured_at >= ? "
        "AND measured_at < ? ORDER BY measured_at ASC",
        (child_id, family_id, f"{date_from} 00:00:00", f"{date_to} 23:59:59")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Achievements (gamification, schema v5)
# ============================================================================
def get_measurement_dates(db_path: str, child_id: int,
                          family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Distinct measurement dates (ISO, ascending), all sources."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT substr(measured_at, 1, 10) AS d FROM measurements "
        "WHERE child_id = ? AND family_id = ? ORDER BY d ASC",
        (child_id, family_id)
    ).fetchall()
    conn.close()
    return [r["d"] for r in rows]


def count_measurements(db_path: str, child_id: int,
                       family_id: int = DEFAULT_FAMILY_ID) -> int:
    """Total measurement count, all sources (incl. auto)."""
    conn = get_connection(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE child_id = ? AND family_id = ?",
        (child_id, family_id)
    ).fetchone()[0]
    conn.close()
    return n


def get_achievements(db_path: str, child_id: int) -> dict:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT code, unlocked_at FROM achievements WHERE child_id = ?", (child_id,)
    ).fetchall()
    conn.close()
    return {r["code"]: r["unlocked_at"] for r in rows}


def unlock_achievements(db_path: str, child_id: int, codes, when: str) -> set:
    """Insert new achievement codes; return only the ones actually inserted."""
    conn = get_connection(db_path)
    new = set()
    for code in codes:
        cur = conn.execute(
            "INSERT OR IGNORE INTO achievements (child_id, code, unlocked_at) "
            "VALUES (?, ?, ?)", (child_id, code, when)
        )
        if cur.rowcount:
            new.add(code)
    conn.commit()
    conn.close()
    return new

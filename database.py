import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import TZ_OFFSET

# Часовой пояс — единый источник (config.py читает TZ_OFFSET из .env)
_TZ = timezone(timedelta(hours=TZ_OFFSET))

DEFAULT_FAMILY_ID = 1

# Версия схемы БД (PRAGMA user_version). 2 = мульти-тенант (families/members).
SCHEMA_VERSION = 2


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
    has_old = "child_id" if "child_id" in cols else "user_id"
    tod = "time_of_day" if "time_of_day" in cols else "'unknown'"
    added_by = "added_by" if "added_by" in cols else "NULL"
    note = "note" if "note" in cols else "NULL"
    source = "COALESCE(source, 'manual')" if "source" in cols else "'manual'"
    conn.execute(
        f"INSERT INTO measurements (id, family_id, child_id, pef_value, time_of_day, "
        f"measured_at, added_by, note, source) "
        f"SELECT id, ?, {has_old}, pef_value, {tod}, measured_at, {added_by}, "
        f"{note}, {source} FROM measurements_v1",
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
        conn.execute(
            f"INSERT INTO reminders_sent (child_id, date{', ' + sel if sel else ''}) "
            f"SELECT 0, date{', ' + sel if sel else ''} FROM reminders_v1")
        conn.execute("DROP TABLE reminders_v1")


def _seed_default_family(conn):
    pass


def _migrate_to_v2(conn):
    """Idempotent v1 -> v2 migration; owns table creation for fresh DBs."""
    _rebuild_measurements_v2(conn)
    _rebuild_settings_v2(conn)
    _rebuild_reminders_v2(conn)


def init_db(db_path: str):
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

    _seed_default_family(c)                      # Task 5; заглушка pass до Task 5
    _migrate_to_v2(c)

    # Default target PEF if not set
    c.execute(
        "INSERT OR IGNORE INTO settings (family_id, key, value) VALUES (?, 'target_pef', '260')",
        (DEFAULT_FAMILY_ID,)
    )

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_meas_family_child_time
        ON measurements(family_id, child_id, measured_at)
    """)

    # Record schema version (idempotent migrations above are v1).
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    conn.commit()
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
                            family_id: int = DEFAULT_FAMILY_ID):
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
                          family_id: int = DEFAULT_FAMILY_ID) -> Optional[dict]:
    """Fetch a single measurement by primary key within a family."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM measurements WHERE id = ? AND family_id = ?", (measurement_id, family_id)
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
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
        "role = excluded.role, name = excluded.name",
        (telegram_id, family_id, role, name)
    )
    conn.commit()
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

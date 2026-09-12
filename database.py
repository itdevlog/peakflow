import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

# Часовой пояс из .env (по умолчанию UTC+5)
_TZ_OFFSET = int(os.getenv("TZ_OFFSET", "5"))
_TZ = timezone(timedelta(hours=_TZ_OFFSET))


def _now():
    """Текущее время в настроенном часовом поясе."""
    return datetime.now(_TZ)


def _today_str():
    return _now().strftime("%Y-%m-%d")


def _db_path(db_path: str) -> str:
    return db_path


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str):
    conn = get_connection(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pef_value INTEGER NOT NULL,
            time_of_day TEXT NOT NULL CHECK(time_of_day IN ('morning', 'evening')),
            measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            added_by INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders_sent (
            date TEXT PRIMARY KEY,
            morning_reminder INTEGER DEFAULT 0,
            evening_reminder INTEGER DEFAULT 0,
            weekly_report INTEGER DEFAULT 0
        )
    """)

    # Migration: add time_of_day column if it doesn't exist (from very old schema)
    try:
        c.execute("ALTER TABLE measurements ADD COLUMN time_of_day TEXT NOT NULL DEFAULT 'unknown'")
        # Update existing rows
        c.execute("UPDATE measurements SET time_of_day = 'morning' WHERE time_of_day = 'unknown'")
    except sqlite3.OperationalError:
        pass

    # Migration: add added_by column
    try:
        c.execute("ALTER TABLE measurements ADD COLUMN added_by INTEGER")
    except sqlite3.OperationalError:
        pass

    # Migration: note attached to a measurement ('болел', 'после спорта'...)
    try:
        c.execute("ALTER TABLE measurements ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass

    # Migration: measurement source ('manual' or 'auto' — auto-carry for missed slots)
    try:
        c.execute("ALTER TABLE measurements ADD COLUMN source TEXT DEFAULT 'manual'")
    except sqlite3.OperationalError:
        pass

    # Migration: child reminder + auto-fill dedup flags
    for col in ("child_morning_reminder", "child_evening_reminder",
                "auto_morning", "auto_evening"):
        try:
            c.execute(f"ALTER TABLE reminders_sent ADD COLUMN {col} INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # Settings table
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Default target PEF if not set
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_pef', '260')")

    # Index for user_id + measured_at queries (history, today, charts)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_meas_user_time
        ON measurements(user_id, measured_at)
    """)

    conn.commit()
    conn.close()


# ============================================================================
# Measurements CRUD
# ============================================================================
def add_measurement(db_path: str, pef_value: int, time_of_day: str,
                    user_id: int, added_by: int, source: str = "manual") -> int:
    """Add measurement. Returns new measurement ID."""
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO measurements (pef_value, time_of_day, user_id, added_by, measured_at, source) VALUES (?, ?, ?, ?, ?, ?)",
        (pef_value, time_of_day, user_id, added_by, now_str, source)
    )
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return mid


def set_note(db_path: str, measurement_id: int, note: str, user_id: int) -> bool:
    """Attach a note to a measurement (record must belong to user)."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "UPDATE measurements SET note = ? WHERE id = ? AND user_id = ?",
        (note, measurement_id, user_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


_AUTO_FILTER = "(source IS NULL OR source != 'auto')"


def get_last_of_tod(db_path: str, user_id: int, time_of_day: str) -> Optional[dict]:
    """Last real (non-auto) measurement of the given time of day."""
    conn = get_connection(db_path)
    row = conn.execute(
        f"SELECT * FROM measurements WHERE user_id = ? AND time_of_day = ? AND {_AUTO_FILTER} "
        "ORDER BY id DESC LIMIT 1",
        (user_id, time_of_day)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def replace_auto_measurement(db_path: str, user_id: int, time_of_day: str,
                            pef_value: int, added_by: int) -> bool:
    """Overwrite today's auto-carry record with a real measurement."""
    today = _today_str()
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        f"UPDATE measurements SET pef_value = ?, added_by = ?, source = 'manual', "
        "measured_at = ? WHERE user_id = ? AND time_of_day = ? AND source = 'auto' AND measured_at LIKE ?",
        (pef_value, added_by, now_str, user_id, time_of_day, f"{today}%")
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def edit_measurement(db_path: str, measurement_id: int, new_value: int, user_id: int) -> bool:
    """Edit last measurement (only if it belongs to user)."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "UPDATE measurements SET pef_value = ? WHERE id = ? AND user_id = ?",
        (new_value, measurement_id, user_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def delete_measurement(db_path: str, measurement_id: int, user_id: int) -> bool:
    conn = get_connection(db_path)
    cur = conn.execute(
        "DELETE FROM measurements WHERE id = ? AND user_id = ?",
        (measurement_id, user_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_last_measurement(db_path: str, user_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_measurements(db_path: str, user_id: int, include_auto: bool = False) -> list:
    conn = get_connection(db_path)
    if include_auto:
        rows = conn.execute(
            "SELECT * FROM measurements WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM measurements WHERE user_id = ? AND {_AUTO_FILTER} ORDER BY id DESC",
            (user_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Today / This week
# ============================================================================
def get_today_measurements(db_path: str, user_id: int) -> list:
    today = _today_str()
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? AND measured_at LIKE ? ORDER BY measured_at ASC",
        (user_id, f"{today}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_today_measurement(db_path: str, user_id: int, time_of_day: str,
                          skip_auto: bool = False) -> bool:
    today = _today_str()
    conn = get_connection(db_path)
    if skip_auto:
        row = conn.execute(
            f"SELECT COUNT(*) FROM measurements WHERE user_id = ? AND measured_at LIKE ? "
            f"AND time_of_day = ? AND {_AUTO_FILTER}",
            (user_id, f"{today}%", time_of_day)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE user_id = ? AND measured_at LIKE ? AND time_of_day = ?",
            (user_id, f"{today}%", time_of_day)
        ).fetchone()
    conn.close()
    return row[0] > 0


def get_week_measurements(db_path: str, user_id: int, week_start: datetime) -> list:
    week_end = week_start + timedelta(days=7)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ? AND measured_at < ? ORDER BY measured_at ASC",
        (user_id, week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_two_weeks(db_path: str, user_id: int) -> tuple:
    """Returns (this_week_measurements, prev_week_measurements)."""
    now = _now()
    # This week: Monday to Sunday
    this_monday = now - timedelta(days=now.weekday())
    this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_monday = this_monday - timedelta(days=7)

    conn = get_connection(db_path)

    this_week = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ?",
        (user_id, this_monday.strftime("%Y-%m-%d %H:%M:%S"))
    ).fetchall()

    prev_week = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ? AND measured_at < ?",
        (user_id, prev_monday.strftime("%Y-%m-%d %H:%M:%S"),
         this_monday.strftime("%Y-%m-%d %H:%M:%S"))
    ).fetchall()

    conn.close()
    return [dict(r) for r in this_week], [dict(r) for r in prev_week]


# ============================================================================
# Pagination
# ============================================================================
def get_measurements_paginated(db_path: str, user_id: int, page: int = 1, per_page: int = 10) -> tuple:
    conn = get_connection(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE user_id = ?", (user_id,)
    ).fetchone()[0]

    offset = (page - 1) * per_page
    rows = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? ORDER BY measured_at DESC LIMIT ? OFFSET ?",
        (user_id, per_page, offset)
    ).fetchall()
    conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)
    return [dict(r) for r in rows], total, total_pages


# ============================================================================
# Chart data
# ============================================================================
def get_measurements_for_chart(db_path: str, user_id: int, days: int = 30) -> list:
    since = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT pef_value, time_of_day, measured_at FROM measurements "
        f"WHERE user_id = ? AND measured_at >= ? AND {_AUTO_FILTER} ORDER BY measured_at ASC",
        (user_id, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Statistics
# ============================================================================
def get_stats(db_path: str, user_id: int) -> dict:
    """Full statistics for a user (auto-carry records excluded)."""
    all_m = get_all_measurements(db_path, user_id)
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
        "today_count": len(get_today_measurements(db_path, user_id)),
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
def mark_reminder_sent(db_path: str, date_str: str, reminder_type: str):
    """Mark that a reminder was sent today. Other flags stay intact."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO reminders_sent (date) VALUES (?) ON CONFLICT(date) DO NOTHING",
        (date_str,)
    )
    column = _reminder_column(reminder_type)
    if column:
        conn.execute(
            f"UPDATE reminders_sent SET {column} = 1 WHERE date = ?", (date_str,)
        )
    conn.commit()
    conn.close()


def was_reminder_sent(db_path: str, date_str: str, reminder_type: str) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM reminders_sent WHERE date = ?", (date_str,)
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
def get_setting(db_path: str, key: str, default: str = "") -> str:
    """Get a setting value by key."""
    conn = get_connection(db_path)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(db_path: str, key: str, value: str):
    """Set a setting value."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


# ============================================================================
# Reminder hours (configurable via settings)
# ============================================================================
REMINDER_HOURS_DEFAULT = {
    "child_morning": 8,
    "child_evening": 20,
    "parent_morning": 10,
    "parent_evening": 22,
}


def get_reminder_hours(db_path: str) -> dict:
    """Reminder hours from settings; defaults when missing/invalid."""
    result = dict(REMINDER_HOURS_DEFAULT)
    for key in result:
        try:
            val = int(get_setting(db_path, f"reminder_{key}", ""))
            if 0 <= val <= 23:
                result[key] = val
        except ValueError:
            pass
    return result


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
def get_measurements_for_month(db_path: str, user_id: int, year: int, month: int,
                               include_auto: bool = False) -> list:
    """Measurements within a calendar month (auto excluded unless include_auto)."""
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    conn = get_connection(db_path)
    if include_auto:
        rows = conn.execute(
            "SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ? AND measured_at < ? "
            "ORDER BY measured_at ASC",
            (user_id, start, end)
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ? AND measured_at < ? "
            f"AND {_AUTO_FILTER} ORDER BY measured_at ASC",
            (user_id, start, end)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_months(db_path: str, user_id: int) -> list:
    """Sorted list of (year, month) having measurements, oldest first."""
    conn = get_connection(db_path)
    rows = conn.execute(
        f"SELECT DISTINCT substr(measured_at, 1, 7) AS ym FROM measurements "
        f"WHERE user_id = ? AND {_AUTO_FILTER} ORDER BY ym ASC",
        (user_id,)
    ).fetchall()
    conn.close()
    months = []
    for r in rows:
        y, m = r["ym"].split("-")
        months.append((int(y), int(m)))
    return months


def get_measurements_between(db_path: str, user_id: int,
                             date_from: str, date_to: str) -> list:
    """Measurements in [date_from 00:00, date_to 23:59:59] (auto included — export shows them)."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? AND measured_at >= ? "
        "AND measured_at < ? ORDER BY measured_at ASC",
        (user_id, f"{date_from} 00:00:00", f"{date_to} 23:59:59")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

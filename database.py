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

    # Settings table
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Default target PEF if not set
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_pef', '260')")

    conn.commit()
    conn.close()


# ============================================================================
# Measurements CRUD
# ============================================================================
def add_measurement(db_path: str, pef_value: int, time_of_day: str,
                    user_id: int, added_by: int) -> int:
    """Add measurement. Returns new measurement ID."""
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO measurements (pef_value, time_of_day, user_id, added_by, measured_at) VALUES (?, ?, ?, ?, ?)",
        (pef_value, time_of_day, user_id, added_by, now_str)
    )
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return mid


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


def get_all_measurements(db_path: str, user_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM measurements WHERE user_id = ? ORDER BY id DESC",
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


def has_today_measurement(db_path: str, user_id: int, time_of_day: str) -> bool:
    today = _today_str()
    conn = get_connection(db_path)
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
        "SELECT pef_value, time_of_day, measured_at FROM measurements WHERE user_id = ? AND measured_at >= ? ORDER BY measured_at ASC",
        (user_id, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================================
# Statistics
# ============================================================================
def get_stats(db_path: str, user_id: int) -> dict:
    """Full statistics for a user."""
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
    """Mark that a reminder was sent today."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO reminders_sent (date) VALUES (?)",
        (date_str,)
    )
    if reminder_type == "morning_missing":
        conn.execute("UPDATE reminders_sent SET morning_reminder = 1 WHERE date = ?", (date_str,))
    elif reminder_type == "evening_missing":
        conn.execute("UPDATE reminders_sent SET evening_reminder = 1 WHERE date = ?", (date_str,))
    elif reminder_type == "weekly":
        conn.execute("UPDATE reminders_sent SET weekly_report = 1 WHERE date = ?", (date_str,))
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
    if reminder_type == "morning_missing":
        return bool(row["morning_reminder"])
    elif reminder_type == "evening_missing":
        return bool(row["evening_reminder"])
    elif reminder_type == "weekly":
        return bool(row["weekly_report"])
    return False


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

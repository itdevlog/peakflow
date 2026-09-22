"""Чистые хелперы и CSV-генерация (общие для бота и Mini App).

Не импортирует bot.py, database.py, aiogram или matplotlib.
"""
import csv
import io

MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def month_title(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month - 1]} {year}"


def tod_emoji(tod: str) -> str:
    return "☀️" if tod == "morning" else "🌙"


def tod_label(tod: str) -> str:
    return "Утро" if tod == "morning" else "Вечер"


def pef_zone(value: int, target: int, zone_green: int = 80,
             zone_yellow: int = 60) -> tuple[str, str]:
    pct = (value / target) * 100 if target else 100
    if pct >= zone_green:
        return "🟢", "Зелёная"
    elif pct >= zone_yellow:
        return "🟡", "Жёлтая"
    else:
        return "🔴", "Красная"


def pct_of(value: int, target: int) -> int:
    return int((value / target) * 100) if target else 100


def escape_md(text) -> str:
    """Escape Telegram legacy-Markdown special chars in user-supplied text.

    CHILD_NAME comes from .env and may contain '_', '*', '[', '`' — without
    escaping these, Telegram rejects the message with a parse error.
    """
    if text is None:
        return ""
    out = str(text)
    for ch in ("_", "*", "[", "]", "`"):
        out = out.replace(ch, "\\" + ch)
    return out


def display_name(user_id: int, child_id: int, child_name: str, parent_ids: list) -> str:
    if user_id == child_id:
        return child_name
    if user_id in parent_ids:
        return "Родитель"
    return "Кто-то"


def parse_month(payload: str):
    """'2026-08' / 'csv_2026-08' → (2026, 8); invalid → None."""
    try:
        text = str(payload).replace("csv_", "")
        y, m = text.split("-")
        y, m = int(y), int(m)
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except (ValueError, AttributeError):
        pass
    return None


def build_csv_content(rows, target, child_name, stats=None, include_summary=True,
                      display_name=None, zone_green=80, zone_yellow=60):
    """CSV text for measurements (rows must be oldest-first).

    Uses the csv module so commas/quotes in names and notes are escaped
    correctly instead of corrupting the file.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Дата", "Время", "Период", "ПСВ (л/мин)", "% от нормы",
                     "Зона", "Добавил", "Заметка", "Источник"])
    for m in rows:
        ts = m["measured_at"].replace("T", " ")
        date_part = ts[:10]
        time_part = ts[11:16]
        pct = pct_of(m["pef_value"], target)
        _, zone_name = pef_zone(m["pef_value"], target, zone_green, zone_yellow)
        who = display_name(m.get("added_by", 0)) if display_name else "Кто-то"
        note = (m.get("note") or "") or "—"
        src = "авто" if m.get("source") == "auto" else "ручной"
        writer.writerow([
            date_part, time_part, tod_label(m["time_of_day"]),
            m["pef_value"], f"{pct}%", zone_name, who, note, src,
        ])

    csv_content = buf.getvalue()

    if include_summary:
        stats = stats or {}
        summary = (
            f"# Статистика\n"
            f"# Всего замеров: {stats.get('total', 0)}\n"
            f"# Среднее: {stats.get('avg', 0):.0f} л/мин\n"
            f"# Мин: {stats.get('min', 0)} | Макс: {stats.get('max', 0)}\n"
            f"# Цель: {target} л/мин\n"
            f"# Ребёнок: {child_name}\n\n"
        )
        csv_content = "\n" + summary + csv_content
    return csv_content


def daily_average_series(rows: list, dates: list) -> list:
    """Average PEF per ISO date in ``dates`` (None when a day has no rows)."""
    buckets = {}
    for r in rows:
        key = str(r["measured_at"])[:10]
        buckets.setdefault(key, []).append(r["pef_value"])
    out = []
    for d in dates:
        vals = buckets.get(d)
        out.append(sum(vals) / len(vals) if vals else None)
    return out

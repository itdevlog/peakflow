"""PDF-отчёты врачу (SP4A).

Чистый модуль: stdlib + matplotlib. Не импортирует bot.py/database.py/aiogram.
Разрешено использовать чистые хелперы из report.py.
"""
from datetime import date, timedelta

from report import month_title, pef_zone

PERIODS = ("week", "month", "quarter")

_ROMAN = ("I", "II", "III", "IV")


def period_bounds(period: str, today: date) -> tuple[str, str]:
    """ISO-границы [date_from, date_to] для периода, включительно."""
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    elif period == "quarter":
        first_month = ((today.month - 1) // 3) * 3 + 1
        start = date(today.year, first_month, 1)
        last_month = first_month + 2
        if last_month == 12:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, last_month + 1, 1) - timedelta(days=1)
    else:
        raise ValueError(f"Неизвестный период: {period}")
    return start.isoformat(), end.isoformat()


def period_label(period: str, today: date) -> str:
    """Человекочитаемая подпись периода для шапки PDF."""
    if period == "week":
        start_s, end_s = period_bounds(period, today)
        start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
        if start.year != end.year:
            return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"
        if start.month != end.month:
            return f"{start:%d.%m}–{end:%d.%m.%Y}"
        return f"{start:%d}–{end:%d.%m.%Y}"
    if period == "month":
        return month_title(today.year, today.month)
    if period == "quarter":
        return f"{_ROMAN[(today.month - 1) // 3]} квартал {today.year}"
    raise ValueError(f"Неизвестный период: {period}")


_ZONE_KEYS = {"Зелёная": "green", "Жёлтая": "yellow", "Красная": "red"}


def compute_stats(rows: list[dict], target: int, zone_green: int = 80,
                  zone_yellow: int = 60) -> dict:
    """Статистика за период из уже выбранных rows (не all-time get_stats)."""
    values = [r["pef_value"] for r in rows]
    morning = [r["pef_value"] for r in rows if r["time_of_day"] == "morning"]
    evening = [r["pef_value"] for r in rows if r["time_of_day"] == "evening"]
    zones = {"green": 0, "yellow": 0, "red": 0}
    for r in rows:
        key = _ZONE_KEYS.get(pef_zone(r["pef_value"], target, zone_green, zone_yellow)[1])
        if key:
            zones[key] += 1
    return {
        "total": len(values),
        "avg": sum(values) / len(values) if values else 0,
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "morning_avg": sum(morning) / len(morning) if morning else None,
        "evening_avg": sum(evening) / len(evening) if evening else None,
        "zones": zones,
    }

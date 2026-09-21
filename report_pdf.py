"""PDF-отчёты врачу (SP4A).

Чистый модуль: stdlib + matplotlib. Не импортирует bot.py/database.py/aiogram.
Разрешено использовать чистые хелперы из report.py.
"""
from datetime import date, timedelta

from report import month_title

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

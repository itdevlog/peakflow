"""PDF-отчёты врачу (SP4A).

Чистый модуль: stdlib + matplotlib. Не импортирует bot.py/database.py/aiogram.
Разрешено использовать чистые хелперы из report.py.
"""
import threading
from datetime import date, timedelta

from report import month_title, pef_zone

RENDER_LOCK = threading.Lock()

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


def draw_chart(ax, rows, target, zone_green, zone_yellow,
               title: str | None = None, text_labels: bool = False) -> None:
    """Нарисовать график ПСВ на переданном ax (без создания/закрытия фигуры)."""
    from datetime import datetime

    import matplotlib.dates as mdates

    dates = [datetime.strptime(r["measured_at"], "%Y-%m-%d %H:%M:%S") for r in rows]
    values = [r["pef_value"] for r in rows]
    morning_d = [dates[i] for i, r in enumerate(rows) if r["time_of_day"] == "morning"]
    morning_v = [values[i] for i, r in enumerate(rows) if r["time_of_day"] == "morning"]
    evening_d = [dates[i] for i, r in enumerate(rows) if r["time_of_day"] == "evening"]
    evening_v = [values[i] for i, r in enumerate(rows) if r["time_of_day"] == "evening"]

    ax.plot(dates, values, marker="o", linewidth=2, label="ПСВ",
            color="#2196F3", markersize=4, zorder=3)
    if morning_d:
        ax.scatter(morning_d, morning_v, color="#FF9800", label="Утро",
                   zorder=5, s=80, edgecolors="white", linewidth=1.5)
    if evening_d:
        ax.scatter(evening_d, evening_v, color="#9C27B0", label="Вечер",
                   zorder=5, s=80, edgecolors="white", linewidth=1.5)

    best_idx = values.index(max(values))
    worst_idx = values.index(min(values))
    if text_labels:
        best_text, worst_text = f"Лучший {max(values)}", f"Худший {min(values)}"
    else:
        best_text, worst_text = f"🏆 {max(values)}", f"⚠️ {min(values)}"
    ax.annotate(best_text, (dates[best_idx], values[best_idx]),
                textcoords="offset points", xytext=(0, 12), ha="center",
                fontsize=9, fontweight="bold", color="green")
    ax.annotate(worst_text, (dates[worst_idx], values[worst_idx]),
                textcoords="offset points", xytext=(0, -14), ha="center",
                fontsize=9, fontweight="bold", color="red")

    if target:
        ax.axhline(y=target, color="green", linestyle="--",
                   label=f"Норма ({target})", linewidth=1.5, zorder=2)
        ax.axhline(y=int(target * zone_green / 100), color="orange",
                   linestyle=":", alpha=0.5, linewidth=1)
        ax.axhline(y=int(target * zone_yellow / 100), color="yellow",
                   linestyle=":", alpha=0.5, linewidth=1)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.set_ylabel("ПСВ (л/мин)")
    if title:
        ax.set_title(f"Пикфлоуметрия — {title}")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)

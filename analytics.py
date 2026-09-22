"""Аналитика ПСВ (SP5D).

Чистый модуль: только stdlib + report.pef_zone. Не импортирует
bot.py/database.py/aiogram/matplotlib.
"""
from datetime import date

from report import pef_zone

_ZONE_KEYS = {"Зелёная": "green", "Жёлтая": "yellow", "Красная": "red"}


def zone_distribution(rows, target, zone_green=80, zone_yellow=60) -> dict:
    """Counts of measurements per zone."""
    out = {"green": 0, "yellow": 0, "red": 0}
    for r in rows:
        key = _ZONE_KEYS.get(pef_zone(r["pef_value"], target, zone_green, zone_yellow)[1])
        if key:
            out[key] += 1
    return out


def weekday_averages(rows, target, zone_green=80, zone_yellow=60) -> list:
    """Per weekday (Mon=0..Sun=6): {"avg","count","zone"} or None."""
    buckets = [[] for _ in range(7)]
    for r in rows:
        try:
            wd = date.fromisoformat(str(r["measured_at"])[:10]).weekday()
        except (ValueError, TypeError):
            continue
        buckets[wd].append(r["pef_value"])
    out = []
    for vals in buckets:
        if not vals:
            out.append(None)
        else:
            avg = sum(vals) / len(vals)
            zone = _ZONE_KEYS.get(pef_zone(avg, target, zone_green, zone_yellow)[1])
            out.append({"avg": avg, "count": len(vals), "zone": zone})
    return out


def linear_fit(values) -> tuple:
    """Least-squares (slope, intercept) over x=0..n-1."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, vals[0]
    x_mean = (n - 1) / 2
    y_mean = sum(vals) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den
    return slope, y_mean - slope * x_mean

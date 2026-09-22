"""Аналитика ПСВ (SP5D).

Чистый модуль: только stdlib + report.pef_zone. Не импортирует
bot.py/database.py/aiogram/matplotlib.
"""
import re
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


NOTE_GROUPS = [
    {"key": "sick",    "title": "Болел",
     "roots": ["болел", "болит", "болезн", "болеет", "болею", "заболе", "температ", "просту"]},
    {"key": "sport",   "title": "Спорт",     "roots": ["спорт", "трениров"]},
    {"key": "meds",    "title": "Лекарства", "roots": ["лекарств", "ингаляц", "беродуал", "вентолин"]},
    {"key": "allergy", "title": "Аллергия",  "roots": ["аллерг"]},
    {"key": "night",   "title": "Ночь",      "roots": ["ночь", "ночн", "не спал"]},
]


def _matches(text, root) -> bool:
    return re.search(r"(?<![а-яё])" + re.escape(root), text) is not None


def match_note_groups(note) -> list[str]:
    """Group keys whose roots occur at a word start in the note (case-insensitive)."""
    text = (note or "").lower()
    if not text:
        return []
    return [g["key"] for g in NOTE_GROUPS if any(_matches(text, root) for root in g["roots"])]


def note_correlation(rows) -> dict:
    """Baseline vs per-group PEF averages across all rows with notes."""
    baseline_vals = []
    per_group = {g["key"]: [] for g in NOTE_GROUPS}
    for r in rows:
        groups = match_note_groups(r.get("note"))
        if not groups:
            baseline_vals.append(r["pef_value"])
            continue
        for key in groups:
            per_group[key].append(r["pef_value"])
    baseline_avg = sum(baseline_vals) / len(baseline_vals) if baseline_vals else None
    groups = []
    for g in NOTE_GROUPS:
        vals = per_group[g["key"]]
        avg = sum(vals) / len(vals) if vals else None
        delta = (avg - baseline_avg) if (avg is not None and baseline_avg is not None) else None
        groups.append({"key": g["key"], "title": g["title"], "avg": avg,
                       "count": len(vals), "delta": delta})
    return {"baseline": {"avg": baseline_avg, "count": len(baseline_vals)}, "groups": groups}

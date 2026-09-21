"""Геймификация: серия дней и достижения (SP4B).

Чистый модуль: только stdlib. Не импортирует bot.py/database.py/aiogram.
"""
from datetime import date, timedelta

ACHIEVEMENTS = [
    {"code": "streak_7",   "emoji": "🔥", "title": "7 дней подряд",   "kind": "streak", "threshold": 7},
    {"code": "streak_30",  "emoji": "🔥", "title": "30 дней подряд",  "kind": "streak", "threshold": 30},
    {"code": "streak_100", "emoji": "🏆", "title": "100 дней подряд", "kind": "streak", "threshold": 100},
    {"code": "total_100",  "emoji": "💯", "title": "100 замеров",     "kind": "total",  "threshold": 100},
    {"code": "total_500",  "emoji": "⭐", "title": "500 замеров",     "kind": "total",  "threshold": 500},
    {"code": "total_1000", "emoji": "👑", "title": "1000 замеров",    "kind": "total",  "threshold": 1000},
]

_BY_CODE = {a["code"]: a for a in ACHIEVEMENTS}


def _to_dates(dates) -> set:
    out = set()
    for d in dates or []:
        out.add(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))
    return out


def current_streak(dates, today: date) -> int:
    """Длина серии, заканчивающейся сегодня или вчера (grace). Иначе 0."""
    days = {d for d in _to_dates(dates) if d <= today}
    if not days:
        return 0
    latest = max(days)
    if latest == today:
        start = today
    elif latest == today - timedelta(days=1):
        start = latest
    else:
        return 0
    n = 0
    day = start
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def longest_streak(dates) -> int:
    """Максимальная серия подряд идущих дней за всю историю."""
    days = sorted(_to_dates(dates))
    if not days:
        return 0
    best = cur = 1
    for prev, day in zip(days, days[1:]):
        if day - prev == timedelta(days=1):
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def evaluate(streak_longest: int, total: int) -> set:
    """Заслуженные коды достижений (streak — по рекордной серии)."""
    earned = set()
    for a in ACHIEVEMENTS:
        value = streak_longest if a["kind"] == "streak" else total
        if value >= a["threshold"]:
            earned.add(a["code"])
    return earned


def achievement_status(code: str, streak_longest: int, total: int) -> tuple:
    """(unlocked, current, threshold) для отображения прогресса."""
    a = _BY_CODE[code]
    current = streak_longest if a["kind"] == "streak" else total
    return current >= a["threshold"], current, a["threshold"]

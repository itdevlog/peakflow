# SP4B — Геймификация (streak, достижения): план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Показать ребёнку серию дней подряд и достижения («7/30/100 дней», «100/500/1000 замеров») в боте и Mini App, один раз уведомляя семью о разблокировке.

**Architecture:** Новый чистый модуль `gamification.py` (stdlib) считает `current_streak`/`longest_streak`/`evaluate`. Схема v5 добавляет таблицу `achievements(child_id, code, unlocked_at)`; `database.py` даёт доступоры и одноразовый бэкфилл без уведомлений. Бот показывает серию в статусе и экран «🏅 Достижения», а при сохранении замера разблокирует новые бейджи и уведомляет родителей один раз. Веб отдаёт `GET /api/gamification`; Mini App рисует серию и сетку бейджей.

**Tech Stack:** Python 3.11+, sqlite3, pytest, aiogram 3, FastAPI, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-21-sp4b-gamification-design.md`

## Global Constraints

- `gamification.py` — только stdlib; **не** импортирует `bot.py`/`database.py`/`aiogram`/`matplotlib`.
- Новых зависимостей нет.
- Активный день = любой замер (включая `auto`); границы дня по `TZ_OFFSET`.
- streak-достижения — по `longest_streak`; total — по числу всех замеров.
- Изоляция данных: `(family_id, child_id)`.
- `member=None` → прежнее поведение семьи №1 из `.env`.
- Схема v5, миграция идемпотентна; бэкфилл — без уведомлений.
- Уведомление о достижении — ровно один раз (`PRIMARY KEY(child_id, code)` + `INSERT OR IGNORE`).
- Ветка: `sp4b-gamification`; коммиты `feat(...): ... (SP4B)`.
- Проверка: `venv/bin/python -m pytest test/ -q`; `venv/bin/python -m pytest test/test_webapp_api.py -q`; без `.env`.
- Полная проверка: `venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py web && echo ALL_GREEN`.

---

## File Structure

- `gamification.py` — новый: каталог достижений и чистые расчёты.
- `database.py` — схема v5, доступоры `achievements`, бэкфилл.
- `bot.py` — серия в статусе, экран достижений, разблокировка + уведомление.
- `web/api.py` — `GET /api/gamification`.
- `web/static/app.js` — серия и бейджи на экране статистики.
- `test/test_gamification.py` — новый; `test/test_bot.py`, `test/test_webapp_api.py` — дополнения.
- `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, todo-plan — CI-список и счётчики.

---

### Task 1: `gamification.py` — чистые расчёты

**Files:** Create `gamification.py`; Create `test/test_gamification.py`.

**Interfaces:**
- Produces: `ACHIEVEMENTS`, `current_streak(dates, today) -> int`, `longest_streak(dates) -> int`, `evaluate(streak_longest, total) -> set[str]`, `achievement_status(code, streak_longest, total) -> tuple[bool, int, int]`.

- [ ] **Step 1: Write the failing test**

```python
"""Тесты геймификации (SP4B)."""
from datetime import date, timedelta

import pytest

TODAY = date(2026, 9, 21)


def _d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


class TestCurrentStreak:
    def test_empty(self):
        from gamification import current_streak
        assert current_streak([], TODAY) == 0

    def test_today_only(self):
        from gamification import current_streak
        assert current_streak([_d(0)], TODAY) == 1

    def test_consecutive_ending_today(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(-1), _d(-2)], TODAY) == 3

    def test_grace_ending_yesterday(self):
        from gamification import current_streak
        assert current_streak([_d(-1), _d(-2)], TODAY) == 2

    def test_gap_resets(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(-2)], TODAY) == 1

    def test_stale_is_zero(self):
        from gamification import current_streak
        assert current_streak([_d(-2), _d(-3)], TODAY) == 0

    def test_unsorted_and_duplicates(self):
        from gamification import current_streak
        assert current_streak([_d(-1), _d(0), _d(0), _d(-2)], TODAY) == 3

    def test_future_dates_ignored(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(1)], TODAY) == 1
        assert current_streak([_d(1)], TODAY) == 0


class TestLongestStreak:
    def test_empty(self):
        from gamification import longest_streak
        assert longest_streak([]) == 0

    def test_single(self):
        from gamification import longest_streak
        assert longest_streak([_d(0)]) == 1

    def test_max_of_runs(self):
        from gamification import longest_streak
        assert longest_streak([_d(-6), _d(-5), _d(-3), _d(-2), _d(-1)]) == 3

    def test_all_consecutive(self):
        from gamification import longest_streak
        assert longest_streak([_d(-2), _d(-1), _d(0)]) == 3


class TestEvaluate:
    def test_below_threshold(self):
        from gamification import evaluate
        assert evaluate(6, 99) == set()

    def test_on_threshold_streak(self):
        from gamification import evaluate
        assert "streak_7" in evaluate(7, 0)

    def test_total_milestones(self):
        from gamification import evaluate
        assert {"total_100", "total_500"} <= evaluate(0, 500)
        assert "total_1000" not in evaluate(0, 500)

    def test_streak_uses_longest(self):
        from gamification import evaluate
        assert {"streak_7", "streak_30"} <= evaluate(30, 0)
        assert "streak_100" not in evaluate(30, 0)


class TestAchievementStatus:
    def test_streak_progress(self):
        from gamification import achievement_status
        assert achievement_status("streak_7", 5, 0) == (False, 5, 7)
        assert achievement_status("streak_7", 7, 0) == (True, 7, 7)

    def test_total_progress(self):
        from gamification import achievement_status
        assert achievement_status("total_100", 0, 40) == (False, 40, 100)

    def test_unknown_code_raises(self):
        from gamification import achievement_status
        with pytest.raises(KeyError):
            achievement_status("nope", 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_gamification.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gamification'`.

- [ ] **Step 3: Write minimal implementation**

Create `gamification.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_gamification.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gamification.py test/test_gamification.py
git commit -m "feat(gamification): streak and achievement math (SP4B)"
```

---

### Task 2: Схема v5, доступоры и бэкфилл

**Files:** Modify `database.py`; Test `test/test_bot.py` (new `TestAchievements`).

**Interfaces:**
- Consumes (Task 1): `gamification.longest_streak`, `gamification.evaluate`.
- Produces: `get_measurement_dates(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> list[str]`, `count_measurements(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> int`, `get_achievements(db_path, child_id) -> dict[str,str]`, `unlock_achievements(db_path, child_id, codes, when) -> set[str]`, `_create_achievements_v5(conn)`, `_backfill_achievements(conn)`.

- [ ] **Step 1: Write the failing test** (append to `test/test_bot.py`)

```python
class TestAchievements:
    def test_schema_v5(self):
        import database
        assert database.SCHEMA_VERSION == 5

    def test_achievements_table(self):
        from database import init_db
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        assert "achievements" in names

    def test_unlock_is_idempotent(self):
        from database import init_db, unlock_achievements
        init_db(TEST_DB)
        first = unlock_achievements(TEST_DB, 111, {"streak_7", "total_100"}, "2026-09-21")
        second = unlock_achievements(TEST_DB, 111, {"streak_7"}, "2026-09-22")
        assert first == {"streak_7", "total_100"}
        assert second == set()

    def test_get_achievements(self):
        from database import init_db, unlock_achievements, get_achievements
        init_db(TEST_DB)
        unlock_achievements(TEST_DB, 111, {"streak_7"}, "2026-09-21")
        assert get_achievements(TEST_DB, 111) == {"streak_7": "2026-09-21"}

    def test_get_measurement_dates_distinct_sorted(self):
        from database import init_db, get_measurement_dates
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        for ts in ("2026-09-02 08:00:00", "2026-09-01 08:00:00",
                   "2026-09-01 20:00:00"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', ?, 222, 'manual')",
                (ts,))
        conn.commit()
        conn.close()
        assert get_measurement_dates(TEST_DB, 111) == ["2026-09-01", "2026-09-02"]

    def test_count_measurements_includes_auto(self):
        from database import init_db, count_measurements
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        for src in ("manual", "auto"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', "
                "'2026-09-01 08:00:00', 222, ?)", (src,))
        conn.commit()
        conn.close()
        assert count_measurements(TEST_DB, 111) == 2

    def test_backfill_inserts_earned_without_bot(self):
        from database import (init_db, add_member, _backfill_achievements,
                              get_connection, get_achievements)
        init_db(TEST_DB)
        add_member(TEST_DB, 111, 1, "child", "Motya")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 111, 250, 'morning', "
                "'2026-09-01 08:00:00', 222, 'manual')")
        conn.commit()
        conn.close()
        c = get_connection(TEST_DB)
        _backfill_achievements(c)
        c.commit()
        c.close()
        ach = get_achievements(TEST_DB, 111)
        assert "total_100" in ach
        assert "streak_7" not in ach  # все замеры в один день
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements -q`
Expected: FAIL — `SCHEMA_VERSION == 4`, missing accessors.

- [ ] **Step 3: Write minimal implementation**

In `database.py`: bump the version and comment:

```python
# Версия схемы БД (PRAGMA user_version). 5 = геймификация (achievements).
SCHEMA_VERSION = 5
```

Add `import gamification` at the top (next to `from config import TZ_OFFSET`).

Add builders after `_add_active_child_v4`:

```python
def _create_achievements_v5(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            child_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY (child_id, code)
        )
    """)


def _backfill_achievements(conn):
    """Persist already-earned achievements silently (no notifications).

    Runs only on the first upgrade to v5 so a deploy does not spam families.
    """
    today = _today_str()
    children = conn.execute(
        "SELECT telegram_id FROM members WHERE role = 'child'"
    ).fetchall()
    for row in children:
        child_id = row["telegram_id"]
        dates = [r["d"] for r in conn.execute(
            "SELECT DISTINCT substr(measured_at, 1, 10) AS d FROM measurements "
            "WHERE child_id = ?", (child_id,)
        ).fetchall()]
        total = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE child_id = ?", (child_id,)
        ).fetchone()[0]
        earned = gamification.evaluate(gamification.longest_streak(dates), total)
        for code in earned:
            conn.execute(
                "INSERT OR IGNORE INTO achievements (child_id, code, unlocked_at) "
                "VALUES (?, ?, ?)", (child_id, code, today)
            )
```

In `init_db`, inside the `try` right after `c.execute("BEGIN IMMEDIATE")`, read the old version; and after `_add_active_child_v4(c)` add v5 + conditional backfill:

```python
        c.execute("BEGIN IMMEDIATE")
        old_version = c.execute("PRAGMA user_version").fetchone()[0]
        _seed_default_family(c)
        _migrate_to_v2(c)
        _create_invites_v3(c)
        _add_active_child_v4(c)
        _create_achievements_v5(c)
        if old_version < 5:
            _backfill_achievements(c)
```

Add accessors (after `get_measurements_between`):

```python
def get_measurement_dates(db_path: str, child_id: int,
                          family_id: int = DEFAULT_FAMILY_ID) -> list:
    """Distinct measurement dates (ISO, ascending), all sources."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT substr(measured_at, 1, 10) AS d FROM measurements "
        "WHERE child_id = ? AND family_id = ? ORDER BY d ASC",
        (child_id, family_id)
    ).fetchall()
    conn.close()
    return [r["d"] for r in rows]


def count_measurements(db_path: str, child_id: int,
                       family_id: int = DEFAULT_FAMILY_ID) -> int:
    """Total measurement count, all sources (incl. auto)."""
    conn = get_connection(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE child_id = ? AND family_id = ?",
        (child_id, family_id)
    ).fetchone()[0]
    conn.close()
    return n


def get_achievements(db_path: str, child_id: int) -> dict:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT code, unlocked_at FROM achievements WHERE child_id = ?", (child_id,)
    ).fetchall()
    conn.close()
    return {r["code"]: r["unlocked_at"] for r in rows}


def unlock_achievements(db_path: str, child_id: int, codes, when: str) -> set:
    """Insert new achievement codes; return only the ones actually inserted."""
    conn = get_connection(db_path)
    new = set()
    for code in codes:
        cur = conn.execute(
            "INSERT OR IGNORE INTO achievements (child_id, code, unlocked_at) "
            "VALUES (?, ?, ?)", (child_id, code, when)
        )
        if cur.rowcount:
            new.add(code)
    conn.commit()
    conn.close()
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): achievements schema v5 and accessors (SP4B)"
```

---

### Task 3: Бот — серия в статусе и экран достижений

**Files:** Modify `bot.py`; Test `test/test_bot.py` (extend `TestAchievements`).

**Interfaces:**
- Consumes (Tasks 1–2): `gamification.ACHIEVEMENTS`, `gamification.current_streak`, `gamification.longest_streak`; `get_measurement_dates`, `count_measurements`; `_ctx`, `_no_child_reply`, `respond`, `kb_back`.
- Produces: `build_achievements_text(streak_longest, total) -> str`, handler `cb_achievements`.

- [ ] **Step 1: Write the failing test** (append to `test/test_bot.py::TestAchievements`)

```python
    def test_build_achievements_text_marks_unlocked(self):
        from bot import build_achievements_text
        text = build_achievements_text(7, 100)
        assert "7 дней подряд" in text and "100 замеров" in text
        assert "30 дней подряд" in text
        # streak_7 unlocked, streak_30 locked with progress 7/30
        assert "7/30" in text

    def test_kb_main_has_achievements(self):
        import bot
        for is_parent in (True, False):
            cbs = [b.callback_data for row in bot.kb_main(is_parent).inline_keyboard for b in row]
            assert "achievements" in cbs

    def test_cb_achievements_scoped(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        seen = {}
        def fake_dates(db, child_id, family_id=1):
            seen["child_id"] = child_id; seen["family_id"] = family_id
            return ["2026-09-20", "2026-09-19"]
        with patch.object(bot, "get_measurement_dates", side_effect=fake_dates), \
             patch.object(bot, "count_measurements", return_value=2), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()) as resp:
            asyncio.run(bot.cb_achievements(
                cb, member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert seen == {"child_id": 700, "family_id": 2}
        resp.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements -q`
Expected: FAIL — `build_achievements_text` / `cb_achievements` missing.

- [ ] **Step 3: Write minimal implementation**

In `bot.py` add `import gamification` next to `import report_pdf`.

Extend the database import list with `get_measurement_dates`, `count_measurements` (and, for Task 4, `unlock_achievements`).

In `build_status_block`, before the return, compute the streak and append:

```python
    dates = await _db(get_measurement_dates, DB_PATH, child_id, family_id=family_id)
    streak = gamification.current_streak(dates, now_tz().date())
    streak_line = f"\n🔥 Серия: {streak} дн." if streak >= 1 else ""

    name = await _child_name(member, child_id)
    return (
        f"👋 *{escape_md(name)}* | Целевая: {target} л/мин\n\n"
        f"Сегодня: {morning_display} | {evening_display}"
        f"{diff}{streak_line}"
    )
```

In `kb_main`, add a «🏅 Достижения» button to both branches. Parent branch — a new row after the stats/weekly row:

```python
        rows.append([
            InlineKeyboardButton(text="📊 Сводка", callback_data="summary"),
            InlineKeyboardButton(text="📈 Неделя", callback_data="weekly"),
        ])
        rows.append([InlineKeyboardButton(text="🏅 Достижения", callback_data="achievements")])
```

Child branch — after the chart/stats row:

```python
        rows.append([
            InlineKeyboardButton(text="📊 Мой график", callback_data="chart"),
            InlineKeyboardButton(text="📈 Моя статистика", callback_data="stats"),
        ])
        rows.append([InlineKeyboardButton(text="🏅 Достижения", callback_data="achievements")])
```

Add text builder and handler (near `cb_stats`):

```python
def build_achievements_text(streak_longest: int, total: int) -> str:
    lines = ["🏅 *Достижения*", "", f"🔥 Рекордная серия: {streak_longest} дн.", ""]
    for a in gamification.ACHIEVEMENTS:
        unlocked, current, threshold = gamification.achievement_status(
            a["code"], streak_longest, total)
        if unlocked:
            lines.append(f"✅ {a['emoji']} {a['title']}")
        else:
            lines.append(f"⬜ {a['emoji']} {a['title']} — {current}/{threshold}")
    return "\n".join(lines)


@router.callback_query(F.data == "achievements")
async def cb_achievements(callback: types.CallbackQuery, member=None):
    family_id, child_id = await _ctx(member)
    if child_id is None:
        await _no_child_reply(callback, member)
        return
    dates = await _db(get_measurement_dates, DB_PATH, child_id, family_id=family_id)
    total = await _db(count_measurements, DB_PATH, child_id, family_id=family_id)
    await respond(callback, build_achievements_text(
        gamification.longest_streak(dates), total), kb=kb_back())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): streak in status and achievements screen (SP4B)"
```

---

### Task 4: Бот — разблокировка и одноразовое уведомление

**Files:** Modify `bot.py`; Test `test/test_bot.py` (extend `TestAchievements`).

**Interfaces:**
- Consumes (Task 2): `unlock_achievements`, `get_measurement_dates`, `count_measurements`; `gamification.evaluate`, `gamification.longest_streak`; `_family_parents`, `now_tz`.
- Produces: `_evaluate_and_notify(child_id, family_id, who, member=None) -> None`.

- [ ] **Step 1: Write the failing test** (append to `test/test_bot.py::TestAchievements`)

```python
    def test_evaluate_and_notify_sends_once(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch
        sent = []
        async def fake_send(pid, text, **kw):
            sent.append((pid, text))
        with patch.object(bot, "get_measurement_dates",
                          return_value=["2026-09-21", "2026-09-20", "2026-09-19",
                                        "2026-09-18", "2026-09-17", "2026-09-16",
                                        "2026-09-15"]), \
             patch.object(bot, "count_measurements", return_value=7), \
             patch.object(bot, "unlock_achievements", return_value={"streak_7"}), \
             patch.object(bot, "_family_parents", new=AsyncMock(return_value=[222])), \
             patch.object(bot.bot, "send_message", side_effect=fake_send):
            asyncio.run(bot._evaluate_and_notify(111, 1, 999))
        assert sent, "achievement notification must be sent"
        assert any("достижение" in t.lower() for _, t in sent)

    def test_evaluate_and_notify_no_new_silent(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, patch
        sent = []
        async def fake_send(pid, text, **kw):
            sent.append(pid)
        with patch.object(bot, "get_measurement_dates", return_value=["2026-09-21"]), \
             patch.object(bot, "count_measurements", return_value=1), \
             patch.object(bot, "unlock_achievements", return_value=set()), \
             patch.object(bot, "_family_parents", new=AsyncMock(return_value=[222])), \
             patch.object(bot.bot, "send_message", side_effect=fake_send):
            asyncio.run(bot._evaluate_and_notify(111, 1, 999))
        assert sent == []

    def test_persist_measurement_triggers_evaluation(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        state = MagicMock(); state.get_data = AsyncMock(return_value={})
        state.update_data = AsyncMock(); state.set_state = AsyncMock()
        with patch.object(bot, "respond", new=AsyncMock()), \
             patch.object(bot, "replace_auto_measurement", return_value=1), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_previous_of_tod", return_value=None), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "_family_parents", new=AsyncMock(return_value=[])), \
             patch.object(bot, "_evaluate_and_notify", new=AsyncMock()) as ev:
            asyncio.run(bot._persist_measurement(
                cb, state, 250, "morning",
                member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        ev.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements -q`
Expected: FAIL — `_evaluate_and_notify` missing.

- [ ] **Step 3: Write minimal implementation**

Add to `bot.py` (near `_family_parents`):

```python
async def _evaluate_and_notify(child_id, family_id, who, member=None):
    """Unlock newly earned achievements and notify the family once each."""
    dates = await _db(get_measurement_dates, DB_PATH, child_id, family_id=family_id)
    total = await _db(count_measurements, DB_PATH, child_id, family_id=family_id)
    earned = gamification.evaluate(gamification.longest_streak(dates), total)
    if not earned:
        return
    new = await _db(unlock_achievements, DB_PATH, child_id, earned,
                    now_tz().strftime("%Y-%m-%d"))
    if not new:
        return
    recipients = set(await _family_parents(member, family_id)) | {child_id}
    recipients.discard(who)
    titles = [f"{a['emoji']} {a['title']}"
              for a in gamification.ACHIEVEMENTS if a["code"] in new]
    text = "🎉 Новое достижение!\n" + "\n".join(titles)
    for pid in recipients:
        try:
            await bot.send_message(pid, text)
        except Exception:
            pass
```

Call it at the end of `_persist_measurement`, right before `await state.update_data(note_for_id=mid)`:

```python
    await _evaluate_and_notify(child_id, family_id, who, member)

    await state.update_data(note_for_id=mid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_bot.py::TestAchievements test/test_bot.py::TestRedZoneAuthorExclusion -q`
Expected: PASS. (The red-zone test now also runs `_evaluate_and_notify`; its assertions still hold.)

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): unlock and notify achievements once (SP4B)"
```

---

### Task 5: Web API — `GET /api/gamification`

**Files:** Modify `web/api.py`; Test `test/test_webapp_api.py` (new `TestGamificationApi`).

**Interfaces:**
- Consumes (Tasks 1–2): `gamification.ACHIEVEMENTS/longest_streak/current_streak/evaluate`; `get_measurement_dates`, `count_measurements`, `get_achievements`; `require_user`, `auth`.
- Produces: `GET /api/gamification`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`)

```python
class TestGamificationApi:
    def test_gamification_ok(self):
        _setup_db()
        conn = sqlite3.connect(TEST_DB)
        for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, ?, 250, 'morning', ?, ?, 'manual')",
                (CHILD_ID, f"{day} 08:00:00", CHILD_ID))
        conn.commit()
        conn.close()
        body = _client().get("/api/gamification", headers=_auth(222)).json()
        assert body["total"] == 3
        assert body["streak_longest"] == 3
        assert len(body["achievements"]) == 6
        codes = {a["code"] for a in body["achievements"]}
        assert {"streak_7", "total_100"} <= codes
        assert all(a["unlocked"] is False for a in body["achievements"])

    def test_gamification_no_child(self):
        _setup_db()
        from database import create_family_with_owner
        create_family_with_owner(TEST_DB, 999, "B")  # no children
        r = _client().get("/api/gamification", headers=_auth(999))
        assert r.status_code == 404

    def test_gamification_isolation(self):
        _setup_db()
        # Family #2 child reuses family #1's child id; family #1 has 100 rows.
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        conn = sqlite3.connect(TEST_DB)
        for _ in range(100):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
                "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/gamification", headers=_auth(999)).json()
        assert body["total"] == 0
        assert all(a["unlocked"] is False for a in body["achievements"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestGamificationApi -q`
Expected: FAIL — route missing (404/405).

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`: add `import gamification` next to `import report_pdf`; extend the `database` import list with `get_measurement_dates`, `count_measurements`, `get_achievements`.

Add the endpoint after `/api/stats`:

```python
    @app.get("/api/gamification")
    async def gamification_endpoint(auth: dict = Depends(require_user)):
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        dates = await _db(get_measurement_dates, config.DB_PATH, child_id,
                          auth["family_id"])
        total = await _db(count_measurements, config.DB_PATH, child_id,
                          auth["family_id"])
        longest = gamification.longest_streak(dates)
        today = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).date()
        current = gamification.current_streak(dates, today)
        earned = gamification.evaluate(longest, total)
        unlocked = await _db(get_achievements, config.DB_PATH, child_id)
        achievements = [
            {"code": a["code"], "emoji": a["emoji"], "title": a["title"],
             "unlocked": a["code"] in earned,
             "unlocked_at": unlocked.get(a["code"])}
            for a in gamification.ACHIEVEMENTS
        ]
        return {"streak_current": current, "streak_longest": longest,
                "total": total, "achievements": achievements}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestGamificationApi -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test/test_webapp_api.py
git commit -m "feat(web): gamification endpoint (SP4B)"
```

---

### Task 6: Mini App — серия и бейджи на экране статистики

**Files:** Modify `web/static/app.js`; Test `test/test_webapp_api.py` (add static test to `TestGamificationApi`).

**Interfaces:** Consumes `GET /api/gamification`.

- [ ] **Step 1: Write the failing test**

```python
    def test_app_js_has_gamification(self):
        import pathlib
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "/api/gamification" in js and "Серия" in js
```

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestGamificationApi::test_app_js_has_gamification -q`
Expected: FAIL.

- [ ] **Step 2: Edit `web/static/app.js`**

In `loadStats`, fetch gamification and build the badges markup. Replace the start of `loadStats` and the early-return, and append the streak/badges to the full render:

```javascript
async function loadStats() {
  const s = await api("/api/stats");
  const g = await api("/api/gamification");
  state.target = s.target_pef || state.target;
  const el = $("screen-stats");
  const streakCard = `<div class="card"><div class="label">🔥 Серия</div>` +
    `<div class="big">${g.streak_current} <span class="label">дн. (рекорд ${g.streak_longest})</span></div></div>`;
  const badges = `<div class="card"><div class="label">🏅 Достижения</div>` +
    g.achievements.map((a) =>
      `<div class="row"><span>${a.unlocked ? "✅" : "⬜"} ${a.emoji} ${esc(a.title)}</span></div>`
    ).join("") + `</div>`;
  if (!s.total) {
    el.innerHTML = `<div class="hint">Недостаточно данных</div>${streakCard}${badges}`;
    return;
  }
  const trend = (s.trend == null) ? "—"
    : s.trend > 0 ? `↑ +${s.trend.toFixed(1)}`
    : s.trend < 0 ? `↓ ${Math.abs(s.trend).toFixed(1)}`
    : "→ 0";
  const avg = (v) => (v == null ? "—" : Math.round(v));
  el.innerHTML = `
    <div class="card"><div class="label">Всего замеров</div><div class="big">${s.total}</div></div>
    <div class="card"><div class="label">Среднее</div><div class="big">${avg(s.avg)}</div></div>
    <div class="card"><div class="label">Мин / Макс</div><div class="big">${s.min} / ${s.max}</div></div>
    <div class="card"><div class="label">Утро (сред.)</div><div class="big">${avg(s.morning_avg)} <span class="label">×${s.morning_count}</span></div></div>
    <div class="card"><div class="label">Вечер (сред.)</div><div class="big">${avg(s.evening_avg)} <span class="label">×${s.evening_count}</span></div></div>
    <div class="card"><div class="label">Тренд (3 vs 3)</div><div class="big">${trend}</div></div>
    ${streakCard}${badges}`;
}
```

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestGamificationApi::test_app_js_has_gamification -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js test/test_webapp_api.py
git commit -m "feat(web): streak and badges in Mini App stats (SP4B)"
```

---

### Task 7: CI-список, документация, полная регрессия

**Files:** `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1: Add `gamification.py` to CI** — update both commands:
  - pyflakes: `python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py web/*.py`
  - compileall: `python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py web`

- [ ] **Step 2: Update `PROJECT.md`** — схема v5, `gamification.py`, `/api/gamification`, экран «🏅 Достижения», streak в статусе; обновить число тестов.

- [ ] **Step 3: Update `wiki.md`** — `gamification.py` (расчёты), таблица `achievements`, streak/бейджи, бэкфилл; обновить число тестов.

- [ ] **Step 4: Update `/start`/todo-plan** — отметить `4.2` как выполненный (✅ SP4B); обновить тестовые счётчики в шапке и «Метрики успеха».

- [ ] **Step 5: Run full verification**

Run:
```bash
venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`; зафиксировать фактическое число тестов. Дополнительно прогнать в CI-подобном окружении: `CHILD_ID=0 PARENT_IDS=0,0 CHILD_NAME=Ребёнок venv/bin/python -m pytest test/ -q`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: SP4B gamification (SP4B)"
```

---

## Self-Review

**1. Spec coverage:**
- Активный день/streak (grace, длящиеся/разрыв/будущее) → Task 1 ✅
- Достижения, evaluate по longest/total, progress → Task 1 ✅
- Схема v5 + таблица + доступоры + бэкфилл без уведомлений → Task 2 ✅
- Серия в статусе, экран «🏅 Достижения», кнопки → Task 3 ✅
- Разблокировка + одноразовое уведомление → Task 4 ✅
- `GET /api/gamification`, 404 без ребёнка, изоляция → Task 5 ✅
- Mini App: серия + сетка бейджей → Task 6 ✅
- CI-список/документация/счётчики → Task 7 ✅
- Вне scope (картинки, лидерборды, награды родителям, пуши из планировщика) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; все шаги содержат код/точные команды.

**3. Type consistency:**
- `current_streak(dates, today: date) -> int`, `longest_streak(dates) -> int`, `evaluate(streak_longest, total) -> set`, `achievement_status(code, streak_longest, total) -> (bool,int,int)` — Task 1; используются в Tasks 3/4/5.
- `get_measurement_dates(db, child_id, family_id=1)`, `count_measurements(...)`, `get_achievements(db, child_id)`, `unlock_achievements(db, child_id, codes, when) -> set` — Task 2; используются в Tasks 3/4/5.
- `build_achievements_text(streak_longest, total)` — Task 3; `_evaluate_and_notify(child_id, family_id, who, member=None)` — Task 4.
- `GET /api/gamification` поля `streak_current/streak_longest/total/achievements[{code,emoji,title,unlocked,unlocked_at}]` — Task 5; потребляет Task 6.

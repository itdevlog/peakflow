# SP2c: Mini App — настройки, CSV, бэкап — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Mini App настройки (цель ПСВ, часы напоминаний), CSV-экспорт по периодам и согласованный бэкап БД — только для родителей, с общей CSV-логикой в `report.py`.

**Architecture:** Чистые хелперы/CSV выносятся из `bot.py` в `report.py` (bot ре-экспортирует их для совместимости тестов). `web/api.py` получает settings/export/backup роуты под `require_parent`; фронтенд — пятый таб «Настройки» со скачиванием через fetch+blob.

**Tech Stack:** Python 3.11+, FastAPI 0.141.1 + `FileResponse`/`BackgroundTask`, aiogram 3.31.0 (bot), SQLite, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-14-sp2c-miniapp-settings-design.md`

## Global Constraints

- Python 3.11+; пины не менять (`fastapi==0.141.1`, `uvicorn==0.52.4`, `httpx==0.28.1`).
- Тесты — в корне репозитория; запуск `venv/bin/python -m pytest <файл> -v`. Корневой `conftest.py` ставит `DB_PATH=test_peakflow.db` до сбора.
- `report.py` НЕ импортирует `bot.py`, `database.py`, aiogram, matplotlib.
- Все SP2c-роуты под `require_parent` (child → 403).
- target 100–800; часы 0–23; CSV — UTF-8 с BOM (`utf-8-sig`).
- Существующие тесты `test_bot.py` (`month_title`, `pef_zone`, `pct_of`, `parse_csv_month`, `build_csv_content`) должны остаться зелёными без правок.
- Секреты не логировать. Новых зависимостей нет.

---

### Task 1: `report.py` — общие чистые хелперы и CSV

**Files:**
- Create: `report.py`
- Test: `test_report.py`

**Interfaces:**
- Produces: `month_title(year, month)`, `tod_emoji(tod)`, `tod_label(tod)`, `pef_zone(value, target, zone_green=80, zone_yellow=60) -> tuple[str, str]`, `pct_of(value, target) -> int`, `display_name(user_id, child_id, child_name, parent_ids) -> str`, `parse_month(payload) -> tuple[int,int]|None`, `build_csv_content(rows, target, child_name, stats=None, include_summary=True, display_name=None, zone_green=80, zone_yellow=60) -> str`, `MONTH_NAMES`.

- [ ] **Step 1: Write the failing test**

Create `test_report.py`:

```python
"""Тесты общих хелперов и CSV (report.py)."""
from report import (
    build_csv_content,
    display_name,
    month_title,
    parse_month,
    pct_of,
    pef_zone,
    tod_label,
)


def test_month_title():
    assert month_title(2026, 8) == "Август 2026"
    assert month_title(2026, 9) == "Сентябрь 2026"


def test_pef_zone_thresholds():
    emoji, name = pef_zone(220, 260)
    assert name == "Зелёная"
    assert pef_zone(180, 260)[1] == "Жёлтая"
    assert pef_zone(100, 260)[1] == "Красная"


def test_pef_zone_custom_thresholds():
    # 200/260 = 76.9% → with green=70 it is green, default (80) it is yellow
    assert pef_zone(200, 260, 80, 60)[1] == "Жёлтая"
    assert pef_zone(200, 260, 70, 60)[1] == "Зелёная"


def test_pct_of():
    assert pct_of(208, 260) == 80
    assert pct_of(130, 260) == 50


def test_tod_label():
    assert tod_label("morning") == "Утро"
    assert tod_label("evening") == "Вечер"


def test_display_name():
    assert display_name(111, 111, "Motya", [222, 333]) == "Motya"
    assert display_name(222, 111, "Motya", [222, 333]) == "Родитель"
    assert display_name(999, 111, "Motya", [222, 333]) == "Кто-то"


def test_parse_month():
    assert parse_month("2026-08") == (2026, 8)
    assert parse_month("csv_2026-08") == (2026, 8)
    assert parse_month("csv_x") is None
    assert parse_month("2026-13") is None


def test_build_csv_content_columns_and_marks():
    rows = [
        {"measured_at": "2026-08-05 08:00:00", "time_of_day": "morning",
         "pef_value": 240, "added_by": 222, "source": "manual", "note": "болел, сильно"},
        {"measured_at": "2026-08-06 20:00:00", "time_of_day": "evening",
         "pef_value": 250, "added_by": 222, "source": "auto", "note": None},
    ]
    content = build_csv_content(
        rows, target=260, child_name="Motya",
        display_name=lambda uid: "Родитель", include_summary=False,
    )
    assert content.startswith("Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил,Заметка,Источник")
    assert "болел; сильно" in content   # comma replaced by ';'
    assert "авто" in content
    assert "ручной" in content
    assert "# Статистика" not in content


def test_build_csv_content_summary():
    rows = [{"measured_at": "2026-08-05 08:00:00", "time_of_day": "morning",
             "pef_value": 240, "added_by": 222, "source": "manual", "note": None}]
    stats = {"total": 1, "avg": 240.0, "min": 240, "max": 240}
    content = build_csv_content(rows, target=260, child_name="Motya",
                                stats=stats, include_summary=True,
                                display_name=lambda uid: "Родитель")
    assert content.startswith("\n# Статистика")
    assert "Всего замеров: 1" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 3: Write minimal implementation**

Create `report.py`:

```python
"""Чистые хелперы и CSV-генерация (общие для бота и Mini App).

Не импортирует bot.py, database.py, aiogram или matplotlib.
"""

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
    """CSV text for measurements (rows must be oldest-first)."""
    lines = ["Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил,Заметка,Источник"]
    for m in rows:
        ts = m["measured_at"].replace("T", " ")
        date_part = ts[:10]
        time_part = ts[11:16]
        pct = pct_of(m["pef_value"], target)
        _, zone_name = pef_zone(m["pef_value"], target, zone_green, zone_yellow)
        who = display_name(m.get("added_by", 0)) if display_name else "Кто-то"
        note = (m.get("note") or "").replace(",", ";") or "—"
        src = "авто" if m.get("source") == "auto" else "ручной"
        lines.append(
            f"{date_part},{time_part},{tod_label(m['time_of_day'])},"
            f"{m['pef_value']},{pct}%,{zone_name},{who},{note},{src}"
        )

    csv_content = "\n".join(lines) + "\n"

    if include_summary:
        stats = stats or {}
        summary = (
            f"\n# Статистика\n"
            f"# Всего замеров: {stats.get('total', 0)}\n"
            f"# Среднее: {stats.get('avg', 0):.0f} л/мин\n"
            f"# Мин: {stats.get('min', 0)} | Макс: {stats.get('max', 0)}\n"
            f"# Цель: {target} л/мин\n"
            f"# Ребёнок: {child_name}\n"
        )
        csv_content = summary + csv_content
    return csv_content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_report.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add report.py test_report.py
git commit -m "feat(report): shared helpers and CSV generation"
```

---

### Task 2: `bot.py` — рефакторинг на `report.py` (ре-экспорт)

**Files:**
- Modify: `bot.py` (удалить локальные дубли; импорт + обёртки)
- Test: `test_bot.py` (без изменений — должен пройти)

**Interfaces:**
- Consumes: `report.month_title`, `report.tod_emoji`, `report.tod_label`, `report.pef_zone`, `report.pct_of`, `report.parse_month`, `report.build_csv_content`, `report.MONTH_NAMES`.
- Produces: прежние имена в `bot`: `month_title`, `tod_emoji`, `tod_label`, `pef_zone` (обёртка с порогами config), `pct_of`, `parse_csv_month`, `build_csv_content` (обёртка), `MONTH_NAMES`.

- [ ] **Step 1: Add imports and wrappers**

In `bot.py`, after the `from config import (...)` block, add:

```python
from report import (
    MONTH_NAMES, month_title, tod_emoji, tod_label, pct_of,
    parse_month as parse_csv_month,
    build_csv_content as _report_build_csv,
    pef_zone as _report_pef_zone,
)
```

Delete the local definitions of `tod_emoji`, `tod_label`, `pef_zone`, `pct_of` (lines near 141–160) and replace with:

```python
def pef_zone(value, target):
    """Зоны с порогами из config (обёртка над report.pef_zone)."""
    return _report_pef_zone(value, target, ZONE_GREEN, ZONE_YELLOW)
```

Delete the local `MONTH_NAMES` list and `month_title` function (now imported). Keep `parse_chart_month` unchanged (it is separate from `parse_csv_month`).

Delete the local `build_csv_content` function near line 932 and replace with:

```python
def build_csv_content(rows, target, include_summary=True):
    stats = get_stats(DB_PATH, CHILD_ID) if include_summary else None
    return _report_build_csv(rows, target, CHILD_NAME, stats=stats,
                             include_summary=include_summary,
                             display_name=_user_display_name,
                             zone_green=ZONE_GREEN, zone_yellow=ZONE_YELLOW)
```

Delete the local `parse_csv_month` function (now imported as an alias).

Keep `_user_display_name` as-is (used by the wrapper and elsewhere).

- [ ] **Step 2: Verify syntax + tests**

Run: `venv/bin/python -m py_compile bot.py && venv/bin/python -m pytest test_bot.py -q && venv/bin/python -m pytest test_report.py -q`
Expected: compile OK; `test_bot.py` all pass (unchanged); `test_report.py` pass.

- [ ] **Step 3: Confirm no duplicate definitions remain**

Run: `grep -n "^def month_title\|^MONTH_NAMES\|^def pef_zone\|^def pct_of\|^def tod_emoji\|^def tod_label\|^def parse_csv_month" bot.py`
Expected: only the single `def pef_zone` wrapper (no `month_title`, `MONTH_NAMES`, `pct_of`, `tod_emoji`, `tod_label`, `parse_csv_month` definitions).

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "refactor(bot): use shared report helpers"
```

---

### Task 3: `GET/PUT /api/settings` — настройки

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_settings.py`

**Interfaces:**
- Consumes: `database.get_setting`, `set_setting`, `get_reminder_hours`, `get_all_measurements`; `_effective_target(config)`.
- Produces: `GET /api/settings`, `PUT /api/settings/target`, `PUT /api/settings/reminders`; модели `TargetIn`, `RemindersIn`.

- [ ] **Step 1: Write the failing test**

Create `test_webapp_settings.py`:

```python
"""Тесты настроек Mini App (SP2c)."""
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import get_reminder_hours, get_setting, init_db
from web.api import create_app

TEST_DB = "test_peakflow.db"
BOT_TOKEN = "123456:ABC-DEF_token"
CHILD_ID = 111
PARENT_IDS = [222, 333]


def make_init_data(user_id: int) -> str:
    auth_date = int(time.time())
    params = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id, "first_name": "T"})}
    pairs = sorted(params.items())
    dcs = "\n".join(f"{k}={v}" for k, v in pairs)
    sig = hmac.new(
        hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest(),
        dcs.encode(), hashlib.sha256,
    ).hexdigest()
    pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def _config():
    return SimpleNamespace(
        DB_PATH=TEST_DB, BOT_TOKEN=BOT_TOKEN, CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS,
        CHILD_NAME="Motya", TARGET_PEF=260, TZ_OFFSET=5,
        ZONE_GREEN=80, ZONE_YELLOW=60, ZONE_RED=50,
    )


def _client():
    return TestClient(create_app({"config": _config(), "bot": None}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


@pytest.fixture(autouse=True)
def _db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    yield


def test_settings_child_forbidden():
    assert _client().get("/api/settings", headers=_auth(CHILD_ID)).status_code == 403


def test_settings_parent_ok():
    body = _client().get("/api/settings", headers=_auth(PARENT_IDS[0])).json()
    assert body["target_pef"] == 260
    assert body["child_name"] == "Motya"
    assert body["total"] == 0
    assert body["reminder_hours"]["child_morning"] == 8


def test_put_target_parent_ok():
    r = _client().put("/api/settings/target", json={"target_pef": 300}, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json() == {"target_pef": 300}
    assert get_setting(TEST_DB, "target_pef") == "300"


@pytest.mark.parametrize("bad", [99, 801])
def test_put_target_out_of_range(bad):
    assert _client().put("/api/settings/target", json={"target_pef": bad},
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_put_target_child_forbidden():
    assert _client().put("/api/settings/target", json={"target_pef": 300},
                         headers=_auth(CHILD_ID)).status_code == 403


def test_put_reminders_parent_ok():
    body = {"child_morning": 7, "child_evening": 19, "parent_morning": 9, "parent_evening": 21}
    r = _client().put("/api/settings/reminders", json=body, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json()["reminder_hours"]["child_morning"] == 7
    assert get_reminder_hours(TEST_DB)["parent_evening"] == 21


@pytest.mark.parametrize("bad", [24, -1])
def test_put_reminders_out_of_range(bad):
    body = {"child_morning": bad, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
    assert _client().put("/api/settings/reminders", json=body,
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_put_reminders_child_forbidden():
    body = {"child_morning": 8, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
    assert _client().put("/api/settings/reminders", json=body,
                         headers=_auth(CHILD_ID)).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_settings.py -v`
Expected: FAIL — `/api/settings` → 404.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add to the `database` import block: `get_reminder_hours`, `set_setting` (keep all existing). Add models next to `NoteIn`:

```python
class TargetIn(BaseModel):
    target_pef: int = Field(ge=100, le=800)


class RemindersIn(BaseModel):
    child_morning: int = Field(ge=0, le=23)
    child_evening: int = Field(ge=0, le=23)
    parent_morning: int = Field(ge=0, le=23)
    parent_evening: int = Field(ge=0, le=23)


REMINDER_KEYS = ("child_morning", "child_evening", "parent_morning", "parent_evening")
```

Add routes after `/api/measurements/{mid}/note`:

```python
    @app.get("/api/settings")
    async def settings(auth: dict = Depends(require_parent)):
        return {
            "target_pef": _effective_target(config),
            "child_name": getattr(config, "CHILD_NAME", "Ребёнок"),
            "total": len(get_all_measurements(config.DB_PATH, config.CHILD_ID, include_auto=True)),
            "reminder_hours": get_reminder_hours(config.DB_PATH),
        }

    @app.put("/api/settings/target")
    async def put_target(body: TargetIn, auth: dict = Depends(require_parent)):
        set_setting(config.DB_PATH, "target_pef", str(body.target_pef))
        return {"target_pef": body.target_pef}

    @app.put("/api/settings/reminders")
    async def put_reminders(body: RemindersIn, auth: dict = Depends(require_parent)):
        for key in REMINDER_KEYS:
            set_setting(config.DB_PATH, f"reminder_{key}", str(getattr(body, key)))
        return {"reminder_hours": get_reminder_hours(config.DB_PATH)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_settings.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_settings.py
git commit -m "feat(web): settings endpoints"
```

---

### Task 4: `/api/export/*` — CSV и бэкап

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_export.py`

**Interfaces:**
- Consumes: `database.get_measurements_between`, `get_available_months`, `get_stats`, `backup_db`; `report.build_csv_content`; `_effective_target`, `_user_display_name`-эквивалент.
- Produces: `GET /api/export/periods`, `GET /api/export/csv?period=`, `GET /api/backup`.

- [ ] **Step 1: Write the failing test**

Create `test_webapp_export.py`:

```python
"""Тесты CSV-экспорта и бэкапа Mini App (SP2c)."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import init_db
from web.api import create_app

TEST_DB = "test_peakflow.db"
BOT_TOKEN = "123456:ABC-DEF_token"
CHILD_ID = 111
PARENT_IDS = [222, 333]


def make_init_data(user_id: int) -> str:
    auth_date = int(time.time())
    params = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id, "first_name": "T"})}
    pairs = sorted(params.items())
    dcs = "\n".join(f"{k}={v}" for k, v in pairs)
    sig = hmac.new(
        hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest(),
        dcs.encode(), hashlib.sha256,
    ).hexdigest()
    pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def _config():
    return SimpleNamespace(
        DB_PATH=TEST_DB, BOT_TOKEN=BOT_TOKEN, CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS,
        CHILD_NAME="Motya", TARGET_PEF=260, TZ_OFFSET=5,
        ZONE_GREEN=80, ZONE_YELLOW=60, ZONE_RED=50,
    )


def _client():
    return TestClient(create_app({"config": _config(), "bot": None}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


@pytest.fixture(autouse=True)
def _db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    conn = sqlite3.connect(TEST_DB)
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source, note) "
        "VALUES (111, 240, 'morning', '2026-08-05 08:00:00', 222, 'manual', 'болел')")
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
        "VALUES (111, 250, 'evening', '2026-08-06 20:00:00', 222, 'auto')")
    conn.execute(
        "INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by, source) "
        "VALUES (111, 260, 'morning', '2026-09-01 08:00:00', 222, 'manual')")
    conn.commit()
    conn.close()
    yield


def test_csv_child_forbidden():
    assert _client().get("/api/export/csv?period=all", headers=_auth(CHILD_ID)).status_code == 403


def test_csv_all_bom_and_columns():
    r = _client().get("/api/export/csv?period=all", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:3] == b"\xef\xbb\xbf"   # UTF-8 BOM
    text = r.content.decode("utf-8-sig")
    assert "Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил,Заметка,Источник" in text
    assert "болел" in text
    assert "авто" in text
    assert "# Статистика" in text


def test_csv_month_only_that_month():
    r = _client().get("/api/export/csv?period=2026-08", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert "2026-08-05" in text
    # September row excluded (do not assert on target "260" — the summary includes it)
    assert "2026-09-01" not in text


def test_csv_unknown_period_422():
    assert _client().get("/api/export/csv?period=2026-13",
                         headers=_auth(PARENT_IDS[0])).status_code == 422


def test_csv_no_data_404():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)
    assert _client().get("/api/export/csv?period=all",
                         headers=_auth(PARENT_IDS[0])).status_code == 404


def test_export_periods():
    body = _client().get("/api/export/periods", headers=_auth(PARENT_IDS[0])).json()
    assert body["months"] == ["2026-08", "2026-09"]


def test_backup_returns_sqlite():
    r = _client().get("/api/backup", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in r.headers["content-disposition"]


def test_backup_child_forbidden():
    assert _client().get("/api/backup", headers=_auth(CHILD_ID)).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_export.py -v`
Expected: FAIL — `/api/export/csv` → 404.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add imports:

```python
import tempfile

from fastapi import BackgroundTasks
from fastapi.responses import FileResponse, Response

from database import (
    ...,
    backup_db,
    get_measurements_between,
)
from report import build_csv_content as _build_csv, month_title, parse_month
```

Add helpers above `create_app`:

```python
def _today(config) -> str:
    offset = getattr(config, "TZ_OFFSET", 0)
    return datetime.now(timezone(timedelta(hours=offset))).strftime("%Y-%m-%d")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    first_next = (datetime(year, month, 28) + timedelta(days=4)).replace(day=1)
    last_day = first_next - timedelta(days=1)
    return start, last_day.strftime("%Y-%m-%d")


def _who(config, added_by: int) -> str:
    if added_by == getattr(config, "CHILD_ID", 0):
        return getattr(config, "CHILD_NAME", "Ребёнок")
    if added_by in (getattr(config, "PARENT_IDS", []) or []):
        return "Родитель"
    return "Кто-то"
```

Add routes after `/api/settings/reminders`:

```python
    @app.get("/api/export/periods")
    async def export_periods(auth: dict = Depends(require_parent)):
        months = [f"{y:04d}-{m:02d}" for y, m in get_available_months(config.DB_PATH, config.CHILD_ID)]
        return {"months": months, "latest": months[-1] if months else None}

    @app.get("/api/export/csv")
    async def export_csv(period: str = "all", auth: dict = Depends(require_parent)):
        target = _effective_target(config)
        child = getattr(config, "CHILD_NAME", "Ребёнок")
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        if period == "all":
            rows = get_measurements_between(config.DB_PATH, config.CHILD_ID, "2000-01-01", _today(config))
            filename = f"peakflow_{child}_{stamp}.csv"
        else:
            parsed = parse_month(period)
            if not parsed:
                raise HTTPException(422, "Неверный период")
            y, m = parsed
            start, end = _month_bounds(y, m)
            rows = get_measurements_between(config.DB_PATH, config.CHILD_ID, start, end)
            filename = f"peakflow_{child}_{y:04d}-{m:02d}.csv"
        if not rows:
            raise HTTPException(404, "Нет записей за период")
        stats = get_stats(config.DB_PATH, config.CHILD_ID)
        content = _build_csv(rows, target, child, stats=stats,
                             display_name=lambda uid: _who(config, uid),
                             zone_green=getattr(config, "ZONE_GREEN", 80),
                             zone_yellow=getattr(config, "ZONE_YELLOW", 60))
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/backup")
    async def backup(background: BackgroundTasks, auth: dict = Depends(require_parent)):
        stamp = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).strftime("%Y%m%d_%H%M")
        fd, dest = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            backup_db(config.DB_PATH, dest)
        except Exception:
            if os.path.exists(dest):
                os.remove(dest)
            raise HTTPException(500, "Не удалось создать бэкап")
        background.add_task(os.remove, dest)
        return FileResponse(dest, media_type="application/octet-stream",
                            filename=f"peakflow_backup_{stamp}.db")
```

Note: import `BackgroundTasks` from `fastapi` (add to the existing `from fastapi import ...` line). `tempfile` and `FileResponse`/`Response` imports as shown.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_export.py -v && venv/bin/python -m pytest -q`
Expected: export tests pass; whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_export.py
git commit -m "feat(web): CSV export and DB backup endpoints"
```

---

### Task 5: Фронтенд — таб «Настройки»

**Files:**
- Modify: `web/static/index.html`
- Modify: `web/static/app.js`
- Modify: `web/static/style.css`
- Test: `test_webapp_api.py` (static served)

**Interfaces:**
- Consumes: `/api/settings`, `/api/settings/target`, `/api/settings/reminders`, `/api/export/periods`, `/api/export/csv`, `/api/backup`, `state.role`.

- [ ] **Step 1: Add the tab and screen container**

In `web/static/index.html`, inside `<nav id="tabs">`, after the «Статистика» button add:
```html
    <button class="tab" data-screen="settings" id="tab-settings">⚙️</button>
```
Inside `<main>`, after `<section id="screen-stats" ...></section>` add:
```html
    <section id="screen-settings" class="screen" hidden></section>
```

Add a static test to `test_webapp_api.py`:
```python
def test_static_index_has_settings_screen():
    r = _client().get("/")
    assert 'id="screen-settings"' in r.text
    assert 'data-screen="settings"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py::test_static_index_has_settings_screen -v`
Expected: FAIL.

- [ ] **Step 3: Add JS + CSS**

In `web/static/app.js`:

1. Add a raw-download helper (after `api`):
```javascript
async function download(path, fallbackName) {
  const r = await fetch(path, { headers: { "X-Telegram-Init-Data": tg.initData || "" } });
  if (!r.ok) {
    const b = await r.json().catch(() => ({ detail: "Ошибка" }));
    throw new Error(b.detail || `HTTP ${r.status}`);
  }
  const cd = r.headers.get("Content-Disposition") || "";
  const mm = /filename="?([^"]+)"?/.exec(cd);
  const name = mm ? mm[1] : fallbackName;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}
```

2. In `switchTo`, add the settings branch and hide the tab for children:
```javascript
async function switchTo(name) {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((t) =>
    t.classList.toggle("active", t.dataset.screen === name));
  document.querySelectorAll(".screen").forEach((s) =>
    s.hidden = (s.id !== `screen-${name}`));
  const setTab = document.getElementById("tab-settings");
  if (setTab) setTab.hidden = (state.role !== "parent");
  clearError();
  try {
    if (name === "today") await loadToday();
    else if (name === "history") await loadHistory(1);
    else if (name === "stats") await loadStats();
    else if (name === "chart") await loadChart();
    else if (name === "settings") await loadSettings();
  } catch (e) { showError(e.message); }
}
```

3. Add settings functions (before `boot()`):
```javascript
/* ---- settings tab (parents only) ---- */
async function loadSettings() {
  const s = await api("/api/settings");
  const el = $("screen-settings");
  const h = s.reminder_hours;
  const periods = (await api("/api/export/periods")).months;
  const periodBtns = [`<button class="mini" data-csv="all">Всё время</button>`]
    .concat(periods.slice().reverse().map((p) => `<button class="mini" data-csv="${p}">${p}</button>`))
    .join(" ");
  el.innerHTML = `
    <div class="card"><div class="label">Ребёнок</div><div class="big">${esc(s.child_name)}</div></div>
    <div class="card">
      <div class="label">🎯 Целевая ПСВ</div>
      <div class="big">${s.target_pef} <span class="label">л/мин</span></div>
      <button class="mini" id="set-target">Изменить</button>
    </div>
    <div class="card">
      <div class="label">⏰ Напоминания</div>
      <div class="row"><span>🌅 Ребёнку утром</span><button class="mini" data-hour="child_morning">${h.child_morning}:00</button></div>
      <div class="row"><span>🌙 Ребёнку вечером</span><button class="mini" data-hour="child_evening">${h.child_evening}:00</button></div>
      <div class="row"><span>🌅 Родителям (утро)</span><button class="mini" data-hour="parent_morning">${h.parent_morning}:00</button></div>
      <div class="row"><span>🌙 Родителям (вечер)</span><button class="mini" data-hour="parent_evening">${h.parent_evening}:00</button></div>
    </div>
    <div class="card">
      <div class="label">📥 Экспорт CSV</div>
      <div class="row" style="flex-wrap:wrap;gap:6px">${periodBtns}</div>
    </div>
    <div class="card">
      <div class="label">💾 Бэкап БД</div>
      <button class="mini" id="set-backup">Скачать бэкап</button>
    </div>`;
  $("set-target").onclick = changeTarget;
  $("set-backup").onclick = () => download("/api/backup", "peakflow_backup.db").catch((e) => showError(e.message));
  el.querySelectorAll("[data-csv]").forEach((b) =>
    b.onclick = () => download(`/api/export/csv?period=${b.dataset.csv}`, "peakflow.csv").catch((e) => showError(e.message)));
  el.querySelectorAll("[data-hour]").forEach((b) =>
    b.onclick = () => changeHour(b.dataset.hour, h));
}

async function changeTarget() {
  const cur = await api("/api/settings");
  const input = window.prompt("Целевая ПСВ (100–800)", String(cur.target_pef));
  if (input == null) return;
  const val = Number(input);
  if (!(val >= 100 && val <= 800)) { showError("Диапазон: 100–800"); return; }
  try {
    await api("/api/settings/target", { method: "PUT", body: { target_pef: val } });
    await loadSettings();
  } catch (e) { showError(e.message); }
}

async function changeHour(key, hours) {
  const input = window.prompt("Час (0–23)", String(hours[key]));
  if (input == null) return;
  const val = Number(input);
  if (!(val >= 0 && val <= 23)) { showError("Диапазон: 0–23"); return; }
  const body = { ...hours, [key]: val };
  try {
    await api("/api/settings/reminders", { method: "PUT", body });
    await loadSettings();
  } catch (e) { showError(e.message); }
}
```

4. In `boot()`, after setting `state.role`, ensure settings tab visibility is applied on first render (switchTo already handles it).

Add to `web/static/style.css`:
```css
#tab-settings[hidden] { display: none !important; }
#screen-settings .row { justify-content: space-between; }
```

- [ ] **Step 4: Verify**

Run:
```
venv/bin/python -m pytest test_webapp_api.py -v
venv/bin/python - <<'PY'
import pathlib
s = pathlib.Path("web/static/app.js").read_text()
assert s.count("(") == s.count(")") and s.count("{") == s.count("}"), "unbalanced"
print("app.js balance OK")
PY
```
Expected: static tests pass incl. the new one; balance OK.

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/app.js web/static/style.css test_webapp_api.py
git commit -m "feat(web): settings tab with CSV and backup download"
```

---

### Task 6: Документация SP2c

**Files:**
- Modify: `README.md`
- Modify: `wiki.md`
- Modify: `roadmap.md`

- [ ] **Step 1: Update `README.md`**

In "### Веб-версия (Telegram Mini App)", replace the sentence "Настройки, CSV и бэкап — подпроект SP2c." with:
```markdown
Родителям доступен таб «Настройки»: смена целевой ПСВ и часов напоминаний,
экспорт CSV по периодам и скачивание бэкапа БД. Общая CSV-логика вынесена в
`report.py` и используется и ботом, и Mini App.
```

- [ ] **Step 2: Update `wiki.md`**

After the "#### Mini App (SP2b): запись" block, add:
```markdown
#### Mini App (SP2c): настройки, CSV, бэкап (только родители)

- `GET/PUT /api/settings`, `PUT /api/settings/target` (100–800),
  `PUT /api/settings/reminders` (часы 0–23).
- `GET /api/export/periods`, `GET /api/export/csv?period=all|YYYY-MM`
  (UTF-8 BOM, те же колонки, что в боте), `GET /api/backup` (согласованный
  `sqlite3.backup`, файл удаляется после отдачи).
- `report.py` — общие чистые хелперы (`pef_zone`, `pct_of`, `month_title`,
  `parse_month`, `build_csv_content`, `display_name`); `bot.py` ре-экспортирует
  их для совместимости.
- Фронтенд: таб «⚙️ Настройки» (скрыт у ребёнка), скачивание через fetch+blob.
```

- [ ] **Step 3: Update `roadmap.md`**

In "### 23. Развёртывание и бэкапы", replace the tail "настройки/CSV/бэкап (SP2c) — далее." with:
```markdown
настройки/CSV/бэкап (SP2c) — сделано 2026-09-14 (Mini App полностью паритетен боту).
```

- [ ] **Step 4: Verify docs only**

Run: `git status --porcelain`
Expected: only `README.md`, `wiki.md`, `roadmap.md`.

- [ ] **Step 5: Commit**

```bash
git add README.md wiki.md roadmap.md
git commit -m "docs: Mini App settings/CSV/backup (SP2c)"
```

---

## Self-Review

**1. Spec coverage:**
- §2 `report.py` + ре-экспорт в bot → Tasks 1, 2.
- §3 права (require_parent) → Tasks 3, 4.
- §4.1 settings GET → Task 3; §4.2 target → Task 3; §4.3 reminders → Task 3;
  §4.4 CSV → Task 4; §4.5 periods → Task 4; §4.6 backup → Task 4.
- §5 фронтенд (таб, скачивание fetch+blob) → Task 5.
- §6 ошибки (403/422/404/500) → Tasks 3–5.
- §7 тесты → Tasks 1, 3, 4 (+ static Task 5); регрессия bot — Task 2.
- §8 файлы — все покрыты; §9 безопасность — require_parent, temp cleanup, валидация.

**2. Placeholder scan:** нет TBD/TODO; все блоки дословные.

**3. Type consistency:** `build_csv_content(rows, target, child_name, stats, include_summary, display_name, zone_green, zone_yellow)` одинаково в `report.py` (Task 1) и его вызовах в `bot.py` (Task 2) и `web/api.py` (Task 4); `pef_zone(value,target,zone_green,zone_yellow)`; `require_parent` из SP2b переиспользуется; ключи ответов settings/periods совпадают с фронтендом (Task 5); `parse_month` принимает и `csv_YYYY-MM`, и `YYYY-MM`.

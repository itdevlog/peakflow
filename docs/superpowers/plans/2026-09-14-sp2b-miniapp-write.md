# SP2b: Mini App — запись (замеры, заметки) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Mini App добавление/редактирование/удаление замеров и заметок, с теми же правами и Telegram-уведомлениями, что в боте.

**Architecture:** Расширяем `web/api.py` write-эндпоинтами под `require_user`/`require_parent`, используя существующие функции `database.py`; Telegram-уведомления — новый `web/notify.py` (no-op при `bot is None`); бот передаёт свой `Bot` в web-слой через `services`; фронтенд — пошаговый ввод (сотни→десятки) в существующем `app.js`.

**Tech Stack:** Python 3.11+, FastAPI 0.141.1 (есть), aiogram 3.31.0, SQLite, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-14-sp2b-miniapp-write-design.md`

## Global Constraints

- Python 3.11+; пины не менять (`fastapi==0.141.1`, `uvicorn==0.52.4`, `httpx==0.28.1`).
- Тесты — в корне репозитория, запуск `venv/bin/python -m pytest <файл> -v`. Корневой `conftest.py` ставит `DB_PATH=test_peakflow.db` до сбора.
- Не импортировать `bot.py` в `web/api.py`/`web/notify.py`. `bot` приходит из `services["bot"]`; `bot is None` → no-op.
- Все write-роуты: add/note → `require_user`; edit/delete → `require_parent`.
- Все записи принадлежат `config.CHILD_ID`; `pef` 100–690; `note` ≤ 200 символов.
- Уведомления — плоский текст, каждый send в try/except, ошибки не роняют запрос.
- Секреты не логировать/не отдавать. Новых зависимостей нет.

---

### Task 1: `web/notify.py` — Telegram-уведомления

**Files:**
- Create: `web/notify.py`
- Test: `test_webapp_notify.py`

**Interfaces:**
- Produces: `web.notify.notify_added(bot, config, who: int, pef: int, tod: str, target: int) -> None` (async), `web.notify.notify_red_zone(bot, config, pef: int, tod: str, target: int) -> None` (async), `web.notify._display_name(config, user_id: int) -> str`.
- Consumes: `config.CHILD_ID`, `config.PARENT_IDS`, `config.CHILD_NAME`, `config.ZONE_GREEN`, `config.ZONE_YELLOW`.

- [ ] **Step 1: Write the failing test**

Create `test_webapp_notify.py`:

```python
"""Тесты Telegram-уведомлений из Mini App (SP2b)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from web.notify import _display_name, notify_added, notify_red_zone

CHILD_ID = 111
PARENT_IDS = [222, 333]


def _config():
    return SimpleNamespace(
        CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS, CHILD_NAME="Motya",
        ZONE_GREEN=80, ZONE_YELLOW=60, ZONE_RED=50,
    )


def test_display_name():
    cfg = _config()
    assert _display_name(cfg, CHILD_ID) == "Motya"
    assert _display_name(cfg, PARENT_IDS[0]) == "Родитель"
    assert _display_name(cfg, 999) == "Кто-то"


def test_notify_added_sends_to_other_parents_only():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_added(bot, cfg, PARENT_IDS[0], 250, "morning", 260))
    assert bot.send_message.await_count == 1
    sent_to = bot.send_message.await_args.args[0]
    assert sent_to == PARENT_IDS[1]
    assert "250" in bot.send_message.await_args.args[1]


def test_notify_added_from_child_sends_to_all_parents():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "evening", 260))
    assert bot.send_message.await_count == 2


def test_notify_red_zone_sends_to_all_parents():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260))
    assert bot.send_message.await_count == 2
    assert "120" in bot.send_message.await_args.args[1]


def test_notify_errors_are_swallowed():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "morning", 260))  # must not raise
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260))         # must not raise


def test_notify_noop_when_bot_none():
    cfg = _config()
    asyncio.run(notify_added(None, cfg, CHILD_ID, 250, "morning", 260))
    asyncio.run(notify_red_zone(None, cfg, 120, "morning", 260))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.notify'`.

- [ ] **Step 3: Write minimal implementation**

Create `web/notify.py` (exactly this content — no scaffolding):

```python
"""Telegram-уведомления, отправляемые из Mini App (паритет с ботом)."""
import logging

logger = logging.getLogger(__name__)


def _zone(value: int, target: int, config) -> tuple[str, str]:
    pct = (value / target) * 100 if target else 100
    if pct >= getattr(config, "ZONE_GREEN", 80):
        return "🟢", "Зелёная"
    if pct >= getattr(config, "ZONE_YELLOW", 60):
        return "🟡", "Жёлтая"
    return "🔴", "Красная"


def _display_name(config, user_id: int) -> str:
    if user_id == getattr(config, "CHILD_ID", 0):
        return getattr(config, "CHILD_NAME", "Ребёнок")
    if user_id in (getattr(config, "PARENT_IDS", []) or []):
        return "Родитель"
    return "Кто-то"


async def _send(bot, recipients, text: str) -> None:
    if bot is None:
        return
    for pid in recipients:
        try:
            await bot.send_message(pid, text)
        except Exception as e:
            logger.error("Не удалось отправить уведомление %s: %s", pid, e)


async def notify_added(bot, config, who: int, pef: int, tod: str, target: int) -> None:
    """Сообщить родителям (кроме автора), что добавлен замер."""
    name = _display_name(config, who)
    zone_emoji, _ = _zone(pef, target, config)
    tod_name = "Утро" if tod == "morning" else "Вечер"
    child = getattr(config, "CHILD_NAME", "Ребёнок")
    text = f"📝 {name} добавил для {child}: {pef} л/мин {zone_emoji} ({tod_name})"
    recipients = [
        p for p in (getattr(config, "PARENT_IDS", []) or [])
        if p != who and p != getattr(config, "CHILD_ID", 0)
    ]
    await _send(bot, recipients, text)


async def notify_red_zone(bot, config, pef: int, tod: str, target: int) -> None:
    """Тревога родителям при ПСВ ниже жёлтой зоны."""
    _, zone_name = _zone(pef, target, config)
    pct = int((pef / target) * 100) if target else 100
    child = getattr(config, "CHILD_NAME", "Ребёнок")
    text = f"🚨 {child}: ПСВ {pef} л/мин — {zone_name}!\nНорма: {target} л/мин ({pct}%). Свяжитесь с врачом."
    await _send(bot, list(getattr(config, "PARENT_IDS", []) or []), text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_notify.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add web/notify.py test_webapp_notify.py
git commit -m "feat(web): Telegram notifications for Mini App writes"
```

---

### Task 2: `bot.py` — передать `bot` в web-слой

**Files:**
- Modify: `bot.py` (`_web_services`)
- Test: `test_webapp_api.py` (sanity only)

**Interfaces:**
- Produces: `bot._web_services(state, shutdown_event) -> dict` теперь включает `"bot": bot`.
- Consumes: `bot.bot` (aiogram Bot).

- [ ] **Step 1: Update `_web_services`**

Replace in `bot.py`:

```python
def _web_services(state: dict, shutdown_event: asyncio.Event) -> dict:
    return {"config": app_config, "state": state, "shutdown_event": shutdown_event}
```

with:

```python
def _web_services(state: dict, shutdown_event: asyncio.Event) -> dict:
    return {"config": app_config, "bot": bot, "state": state, "shutdown_event": shutdown_event}
```

- [ ] **Step 2: Verify syntax + existing suite**

Run: `venv/bin/python -m py_compile bot.py && venv/bin/python -m pytest -q`
Expected: compile OK; suite passes (118 passed) — no behavior change yet.

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat(bot): pass bot instance to web layer"
```

---

### Task 3: `POST /api/measurements` — добавление

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_write.py`

**Interfaces:**
- Consumes: `database.replace_auto_measurement`, `add_measurement`, `has_today_measurement`, `get_last_measurement`, `get_all_measurements`; `web.notify.notify_added`, `notify_red_zone`.
- Produces: модульные `_auto_time_of_day(config) -> str`, `_pef_zone(pef, target, config) -> str` (ключ), `_pct_of(pef, target) -> int`; роут `POST /api/measurements` → `{id, pef, tod, zone, pct, diff}`.

- [ ] **Step 1: Write the failing test**

Create `test_webapp_write.py`. Add a `_config()` with `bot=None` and a `monkeypatch` fixture forcing `_auto_time_of_day` to `"morning"`:

```python
"""Тесты write-API Mini App (SP2b)."""
import asyncio
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from database import add_measurement, get_all_measurements, init_db
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


def _client(bot=None):
    return TestClient(create_app({"config": _config(), "bot": bot}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


def _setup_db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)


@pytest.fixture(autouse=True)
def _fix_tod(monkeypatch):
    import web.api as api
    monkeypatch.setattr(api, "_auto_time_of_day", lambda config: "morning")


def test_add_child_creates_manual_measurement():
    _setup_db()
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pef"] == 250
    assert body["tod"] == "morning"
    assert body["zone"] == "green"
    assert body["pct"] == 96
    rows = get_all_measurements(TEST_DB, CHILD_ID)
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_add_parent_allowed():
    _setup_db()
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200


def test_add_replaces_auto_record():
    _setup_db()
    add_measurement(TEST_DB, 180, "morning", CHILD_ID, 0, source="auto")
    r = _client().post("/api/measurements", json={"pef": 250}, headers=_auth(CHILD_ID))
    assert r.status_code == 200
    rows = get_all_measurements(TEST_DB, CHILD_ID, include_auto=True)
    assert len(rows) == 1
    assert rows[0]["pef_value"] == 250
    assert rows[0]["source"] == "manual"


def test_add_second_slot_goes_to_other_tod():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    r = _client().post("/api/measurements", json={"pef": 230}, headers=_auth(CHILD_ID))
    assert r.status_code == 200
    assert r.json()["tod"] == "evening"


def test_add_pef_out_of_range():
    _setup_db()
    assert _client().post("/api/measurements", json={"pef": 99}, headers=_auth(CHILD_ID)).status_code == 422
    assert _client().post("/api/measurements", json={"pef": 691}, headers=_auth(CHILD_ID)).status_code == 422


def test_add_requires_auth():
    _setup_db()
    assert _client().post("/api/measurements", json={"pef": 250}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_write.py -v`
Expected: FAIL — `POST /api/measurements` → 404/405.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add imports (cumulative — keep existing):

```python
from pydantic import BaseModel, Field

from database import (
    add_measurement,
    get_available_months,
    get_all_measurements,
    get_last_measurement,
    get_last_two_weeks,
    get_measurements_for_month,
    get_measurements_paginated,
    get_setting,
    get_stats,
    get_today_measurements,
    has_today_measurement,
    replace_auto_measurement,
)
from web.notify import notify_added, notify_red_zone
```

Add module-level helpers above `create_app`:

```python
def _auto_time_of_day(config) -> str:
    offset = getattr(config, "TZ_OFFSET", 0)
    hour = datetime.now(timezone(timedelta(hours=offset))).hour
    return "morning" if hour < 12 else "evening"


def _pct_of(pef: int, target: int) -> int:
    return int((pef / target) * 100) if target else 100


def _pef_zone(pef: int, target: int, config) -> str:
    pct = (pef / target) * 100 if target else 100
    if pct >= getattr(config, "ZONE_GREEN", 80):
        return "green"
    if pct >= getattr(config, "ZONE_YELLOW", 60):
        return "yellow"
    return "red"


class MeasurementIn(BaseModel):
    pef: int = Field(ge=100, le=690)
```

Add inside `create_app`, after `require_user`:

```python
    def require_parent(auth: dict = Depends(require_user)) -> dict:
        if auth["role"] != "parent":
            raise HTTPException(403, "Только родители")
        return auth
```

Add the route after `/api/weekly`:

```python
    @app.post("/api/measurements")
    async def add(body: MeasurementIn, auth: dict = Depends(require_user)):
        who = auth["user"]["id"]
        tod = _auto_time_of_day(config)
        if has_today_measurement(config.DB_PATH, config.CHILD_ID, tod, skip_auto=True):
            tod = "evening" if tod == "morning" else "morning"
        target = _effective_target(config)
        replaced = replace_auto_measurement(config.DB_PATH, config.CHILD_ID, tod, body.pef, who)
        if replaced:
            mid = get_last_measurement(config.DB_PATH, config.CHILD_ID)["id"]
        else:
            mid = add_measurement(config.DB_PATH, body.pef, tod, config.CHILD_ID, who)
        all_m = get_all_measurements(config.DB_PATH, config.CHILD_ID)
        diff = None
        if len(all_m) >= 2:
            diff = body.pef - all_m[1]["pef_value"]
        pct = _pct_of(body.pef, target)
        await notify_added(services.get("bot"), config, who, body.pef, tod, target)
        if pct < getattr(config, "ZONE_YELLOW", 60):
            await notify_red_zone(services.get("bot"), config, body.pef, tod, target)
        return {"id": mid, "pef": body.pef, "tod": tod,
                "zone": _pef_zone(body.pef, target, config), "pct": pct, "diff": diff}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_write.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_write.py
git commit -m "feat(web): add measurement endpoint"
```

---

### Task 4: `PATCH`/`DELETE /api/measurements/{id}` — edit/delete (только родители)

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_write.py`

**Interfaces:**
- Consumes: `database.edit_measurement`, `delete_measurement`.
- Produces: `PATCH /api/measurements/{id}` → `{id, pef}`; `DELETE /api/measurements/{id}` → `{deleted, id}`.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_write.py`:

```python
def _add(pef=250, tod="morning"):
    return add_measurement(TEST_DB, pef, tod, CHILD_ID, CHILD_ID)


def test_edit_parent_ok_child_forbidden():
    _setup_db()
    mid = _add(250)
    assert _client().patch(f"/api/measurements/{mid}", json={"pef": 300},
                           headers=_auth(CHILD_ID)).status_code == 403
    r = _client().patch(f"/api/measurements/{mid}", json={"pef": 300}, headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json()["pef"] == 300
    assert get_all_measurements(TEST_DB, CHILD_ID)[0]["pef_value"] == 300


def test_delete_parent_ok_child_forbidden():
    _setup_db()
    mid = _add(250)
    assert _client().delete(f"/api/measurements/{mid}", headers=_auth(CHILD_ID)).status_code == 403
    r = _client().delete(f"/api/measurements/{mid}", headers=_auth(PARENT_IDS[0]))
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "id": mid}
    assert get_all_measurements(TEST_DB, CHILD_ID) == []


def test_edit_missing_returns_404():
    _setup_db()
    assert _client().patch("/api/measurements/99999", json={"pef": 300},
                           headers=_auth(PARENT_IDS[0])).status_code == 404


def test_delete_missing_returns_404():
    _setup_db()
    assert _client().delete("/api/measurements/99999",
                            headers=_auth(PARENT_IDS[0])).status_code == 404


def test_edit_pef_out_of_range():
    _setup_db()
    mid = _add()
    assert _client().patch(f"/api/measurements/{mid}", json={"pef": 50},
                           headers=_auth(PARENT_IDS[0])).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_write.py -v`
Expected: FAIL — PATCH/DELETE → 405.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add `edit_measurement`, `delete_measurement` to the `database` import block. Add routes after the POST route:

```python
    @app.patch("/api/measurements/{mid}")
    async def edit(mid: int, body: MeasurementIn, auth: dict = Depends(require_parent)):
        if not edit_measurement(config.DB_PATH, mid, body.pef, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "pef": body.pef}

    @app.delete("/api/measurements/{mid}")
    async def remove(mid: int, auth: dict = Depends(require_parent)):
        if not delete_measurement(config.DB_PATH, mid, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"deleted": True, "id": mid}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_write.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_write.py
git commit -m "feat(web): edit and delete measurement endpoints"
```

---

### Task 5: `POST /api/measurements/{id}/note` — заметка

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_write.py`

**Interfaces:**
- Consumes: `database.set_note`.
- Produces: `POST /api/measurements/{id}/note` → `{id, note, truncated}`.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_write.py`:

```python
def test_note_saves_and_truncates():
    _setup_db()
    mid = _add(250)
    r = _client().post(f"/api/measurements/{mid}/note", json={"note": "болел"},
                       headers=_auth(CHILD_ID))
    assert r.status_code == 200
    assert r.json() == {"id": mid, "note": "болел", "truncated": False}
    long_note = "x" * 250
    r2 = _client().post(f"/api/measurements/{mid}/note", json={"note": long_note},
                        headers=_auth(PARENT_IDS[0]))
    assert r2.status_code == 200
    assert r2.json()["truncated"] is True
    assert len(r2.json()["note"]) == 200
    from database import get_last_measurement
    assert get_last_measurement(TEST_DB, CHILD_ID)["note"] == "x" * 200


def test_note_missing_returns_404():
    _setup_db()
    assert _client().post("/api/measurements/99999/note", json={"note": "x"},
                          headers=_auth(CHILD_ID)).status_code == 404


def test_note_requires_auth():
    _setup_db()
    mid = _add()
    assert _client().post(f"/api/measurements/{mid}/note", json={"note": "x"}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_write.py -v`
Expected: FAIL — note route → 404/405.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add `set_note` to the `database` import block, and add a body model next to `MeasurementIn`:

```python
class NoteIn(BaseModel):
    note: str = ""
```

Add the route after the DELETE route:

```python
    @app.post("/api/measurements/{mid}/note")
    async def note(mid: int, body: NoteIn, auth: dict = Depends(require_user)):
        raw = body.note or ""
        note_text = raw.strip()[:200]
        if not set_note(config.DB_PATH, mid, note_text, config.CHILD_ID):
            raise HTTPException(404, "Запись не найдена")
        return {"id": mid, "note": note_text, "truncated": len(raw.strip()) > 200}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_write.py -v && venv/bin/python -m pytest -q`
Expected: `test_webapp_write.py` 14 passed; whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_write.py
git commit -m "feat(web): note endpoint"
```

---

### Task 6: Фронтенд — пошаговый ввод + добавление

**Files:**
- Modify: `web/static/index.html` (добавить контейнеры формы в экран «Сегодня»)
- Modify: `web/static/app.js`
- Modify: `web/static/style.css`
- Test: `test_webapp_api.py` (static served only)

**Interfaces:**
- Consumes: `POST /api/measurements`, `POST /api/measurements/{id}/note`.
- Produces: JS `openAddForm()`, `selectHundreds(h)`, `selectTens(d)`, `submitMeasurement(pef, mode, editId)`, `promptNote(mid)`.

- [ ] **Step 1: Add static markup**

In `web/static/index.html`, inside `<section id="screen-today" ...>`, the section is currently empty (JS fills it). No HTML change is strictly required; instead the JS renders a **persistent** «+ Замер» button and a form container. Add before `</main>` a dedicated modal container:

```html
    <div id="form-overlay" hidden></div>
```

And add a test asserting the markup exists — append to `test_webapp_api.py`:

```python
def test_static_index_has_form_overlay():
    r = _client().get("/")
    assert 'id="form-overlay"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py::test_static_index_has_form_overlay -v`
Expected: FAIL — `id="form-overlay"` absent.

- [ ] **Step 3: Add the overlay + styles, then the JS form**

In `web/static/index.html`, insert `<div id="form-overlay" hidden></div>` just before `</div>` closing `#app` (after `</main>`).

In `web/static/style.css`, append:

```css
#form-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 50; }
#form-overlay[hidden] { display: none !important; }
.form-box { background: var(--bg); border-radius: 12px; padding: 16px; width: 100%;
  max-width: 420px; }
.form-box h3 { margin: 0 0 8px; font-size: 16px; }
.form-value { font-size: 34px; font-weight: 700; text-align: center; margin: 8px 0; }
.grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.grid button { padding: 12px 0; border: none; border-radius: 8px; background: var(--card);
  color: var(--fg); font-size: 15px; }
.form-actions { display: flex; gap: 8px; margin-top: 12px; }
.form-actions button { flex: 1; padding: 10px; border: none; border-radius: 8px;
  background: var(--accent); color: #fff; font-size: 15px; }
.form-actions button.secondary { background: var(--card); color: var(--fg); }
#add-btn { width: 100%; padding: 12px; border: none; border-radius: 10px;
  background: var(--accent); color: #fff; font-size: 16px; margin-bottom: 8px; }
```

In `web/static/app.js`, add to `state`: `role`, `form`. Replace the `state` line:

```javascript
const state = {
  target: 0, role: null,
  chart: { year: null, month: null }, history: { page: 1 },
  form: { open: false, step: "h", hundreds: null, mode: "add", editId: null },
};
```

In `boot()`, capture role:

```javascript
async function boot() {
  try {
    const me = await api("/api/me");
    state.role = me.role;
    $("child-name").textContent = me.child_name || "Дневник";
  } catch (e) {
    showError("Откройте приложение через Telegram");
    return;
  }
  await switchTo("today");
}
```

Add the form functions (place before `boot()`):

```javascript
/* ---- add/edit form (stepwise hundreds → tens) ---- */
function renderForm() {
  const ov = $("form-overlay");
  const f = state.form;
  if (!f.open) { ov.hidden = true; ov.innerHTML = ""; return; }
  const title = f.mode === "edit" ? "Изменить ПСВ (л/мин)" : "Выбери ПСВ (л/мин)";
  let body;
  if (f.step === "h") {
    body = `<div class="grid">` +
      [1, 2, 3, 4, 5, 6].map((h) => `<button data-h="${h}">${h}</button>`).join("") +
      `</div>`;
  } else {
    body = `<div class="form-value">${f.hundreds}__</div><div class="grid">` +
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => `<button data-d="${d}">${String(d * 10).padStart(2, "0")}</button>`).join("") +
      `</div>`;
  }
  ov.innerHTML = `<div class="form-box">
    <h3>${title}</h3>${body}
    <div class="form-actions">
      <button class="secondary" id="form-cancel">Отмена</button>
      ${f.step === "t" ? `<button class="secondary" id="form-back">‹ Назад</button>` : ""}
    </div></div>`;
  ov.hidden = false;
  ov.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => { f.hundreds = Number(b.dataset.h); f.step = "t"; renderForm(); });
  ov.querySelectorAll("[data-d]").forEach((b) => b.onclick = () => submitMeasurement(f.hundreds * 100 + Number(b.dataset.d), f.mode, f.editId));
  $("form-cancel").onclick = closeForm;
  const back = $("form-back");
  if (back) back.onclick = () => { f.step = "h"; f.hundreds = null; renderForm(); };
}

function closeForm() { state.form.open = false; renderForm(); }

function openForm(mode, editId = null) {
  state.form = { open: true, step: "h", hundreds: null, mode, editId };
  renderForm();
}

async function submitMeasurement(pef, mode, editId) {
  try {
    if (mode === "edit") {
      await api(`/api/measurements/${editId}`, { method: "PATCH", body: { pef } });
      closeForm();
      await loadHistory(state.history.page);
    } else {
      const res = await api("/api/measurements", { method: "POST", body: { pef } });
      renderResult(res);
    }
  } catch (e) { closeForm(); showError(e.message); }
}

function renderResult(res) {
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>${res.tod === "morning" ? "☀️ Утро" : "🌙 Вечер"}</h3>
    <div class="form-value ${zoneClass(res.pct)}">${res.pef} <span class="label">${res.pct}%</span></div>
    ${res.diff != null ? `<div class="center label">Изменение: ${res.diff > 0 ? "+" : ""}${res.diff}</div>` : ""}
    <div class="label center">Добавить заметку?</div>
    <div class="form-actions">
      <button id="res-note">📝 Да</button>
      <button class="secondary" id="res-skip">Пропустить</button>
    </div></div>`;
  ov.hidden = false;
  $("res-note").onclick = () => openNote(res.id);
  $("res-skip").onclick = async () => { closeForm(); await switchTo("today"); };
}

function openNote(mid) {
  const ov = $("form-overlay");
  ov.innerHTML = `<div class="form-box">
    <h3>Заметка (до 200 символов)</h3>
    <textarea id="note-input" maxlength="200" rows="3" style="width:100%;box-sizing:border-box"></textarea>
    <div class="form-actions">
      <button id="note-save">Сохранить</button>
      <button class="secondary" id="note-cancel">Отмена</button>
    </div></div>`;
  ov.hidden = false;
  $("note-save").onclick = async () => {
    const text = $("note-input").value;
    try {
      await api(`/api/measurements/${mid}/note`, { method: "POST", body: { note: text } });
      closeForm();
      await switchTo("today");
    } catch (e) { showError(e.message); }
  };
  $("note-cancel").onclick = () => { closeForm(); switchTo("today"); };
}
```

Update `api()` to support method/body:

```javascript
async function api(path, opts = {}) {
  const init = { headers: { "X-Telegram-Init-Data": tg.initData || "" } };
  if (opts.method) init.method = opts.method;
  if (opts.body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    const b = await r.json().catch(() => ({ detail: "Ошибка сети" }));
    throw new Error(b.detail || `HTTP ${r.status}`);
  }
  return r.json();
}
```

Update `loadToday` to render the «+ Замер» button above the cards:

```javascript
async function loadToday() {
  const s = await api("/api/status");
  state.target = s.target_pef;
  $("target-badge").textContent = `цель ${s.target_pef}`;
  const el = $("screen-today");
  const btn = `<button id="add-btn">+ Замер</button>`;
  const cards = s.today.length
    ? s.today.map(measureCard).join("")
    : (s.last ? `<div class="label">Последний замер</div>` + measureCard(s.last) : "") +
      `<div class="hint">Сегодня замеров ещё нет 💨</div>`;
  el.innerHTML = btn + cards;
  $("add-btn").onclick = () => openForm("add");
}
```

Add `.center { text-align: center; }` to `style.css`.

- [ ] **Step 4: Verify**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: static tests pass, including `test_static_index_has_form_overlay`.
JS balance:
```
venv/bin/python - <<'PY'
import pathlib
s = pathlib.Path("web/static/app.js").read_text()
assert s.count("(") == s.count(")") and s.count("{") == s.count("}"), "unbalanced"
print("app.js balance OK")
PY
```

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/app.js web/static/style.css test_webapp_api.py
git commit -m "feat(web): add-measurement form in Mini App"
```

---

### Task 7: Фронтенд — edit/delete в истории (только родители)

**Files:**
- Modify: `web/static/app.js`
- Test: manual (no JS unit test)

**Interfaces:**
- Consumes: `PATCH`/`DELETE /api/measurements/{id}`, `state.role`.

- [ ] **Step 1: Render edit/delete buttons for parents**

In `web/static/app.js`, modify `measureCard` to accept an index/actions, or add a history-specific renderer. Replace `loadHistory` body with:

```javascript
async function loadHistory(page = 1) {
  state.history.page = page;
  const h = await api(`/api/history?page=${page}&per_page=10`);
  const el = $("screen-history");
  if (!h.items.length) {
    el.innerHTML = `<div class="hint">История пуста</div>`;
    return;
  }
  const isParent = state.role === "parent";
  el.innerHTML = h.items.map((m) => `<div class="card">
    ${measureCardInner(m)}
    ${isParent ? `<div class="row" style="margin-top:6px">
      <button class="mini" data-edit="${m.id}">✏️</button>
      <button class="mini" data-del="${m.id}">🗑️</button>
    </div>` : ""}
  </div>`).join("") + `<div class="pager">
    <button id="hist-prev" ${page <= 1 ? "disabled" : ""}>‹</button>
    <span class="label">${page} / ${h.total_pages}</span>
    <button id="hist-next" ${page >= h.total_pages ? "disabled" : ""}>›</button>
  </div>`;
  const prev = $("hist-prev"), next = $("hist-next");
  if (prev) prev.onclick = () => loadHistory(page - 1);
  if (next) next.onclick = () => loadHistory(page + 1);
  if (isParent) {
    el.querySelectorAll("[data-edit]").forEach((b) =>
      b.onclick = () => openForm("edit", Number(b.dataset.edit)));
    el.querySelectorAll("[data-del]").forEach((b) =>
      b.onclick = () => deleteMeasurement(Number(b.dataset.del)));
  }
}

async function deleteMeasurement(mid) {
  if (!window.confirm("Удалить запись? Действие необратимо.")) return;
  try {
    await api(`/api/measurements/${mid}`, { method: "DELETE" });
    await loadHistory(state.history.page);
  } catch (e) { showError(e.message); }
}
```

Refactor `measureCard` to expose the inner markup without the outer `.card` (so history can add buttons):

```javascript
function measureCardInner(m) {
  const p = pct(m.pef_value);
  const auto = (m.source === "auto") ? ' <span class="auto">🤖</span>' : "";
  const note = m.note ? `<div class="note">ℹ️ ${esc(m.note)}</div>` : "";
  return `<div class="row">
      <span class="label">${todLabel(m.time_of_day)}</span>
      <span class="label">${esc(String(m.measured_at).slice(5, 16))}</span>
    </div>
    <div class="big ${zoneClass(p)}">${m.pef_value} <span class="label">${p}%</span>${auto}</div>
    ${note}`;
}

function measureCard(m) {
  return `<div class="card">${measureCardInner(m)}</div>`;
}
```

Add `.mini` style to `style.css`:

```css
.mini { padding: 8px 14px; border: none; border-radius: 8px; background: var(--card);
  color: var(--fg); font-size: 15px; }
```

- [ ] **Step 2: Verify JS balance + suite**

Run:
```
venv/bin/python - <<'PY'
import pathlib
s = pathlib.Path("web/static/app.js").read_text()
assert s.count("(") == s.count(")") and s.count("{") == s.count("}"), "unbalanced"
print("app.js balance OK")
PY
venv/bin/python -m pytest -q
```
Expected: `app.js balance OK`; suite passes.

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js web/static/style.css
git commit -m "feat(web): edit and delete measurements in Mini App history"
```

---

### Task 8: Документация SP2b

**Files:**
- Modify: `README.md`
- Modify: `wiki.md`
- Modify: `roadmap.md`

- [ ] **Step 1: Update `README.md`**

In the "### Веб-версия (Telegram Mini App)" section, replace the sentence "Добавление/редактирование замеров, настройки, CSV и бэкап в Mini App — следующие подпроекты (SP2b/SP2c)." with:

```markdown
В Mini App можно добавлять замеры (пошаговый ввод, авто-определение утра/вечера,
замена авто-записи, заметка) и — родителям — редактировать и удалять записи.
Действия из Mini App так же уведомляют родителей в Telegram, как и бот.
Настройки, CSV и бэкап — подпроект SP2c.
```

- [ ] **Step 2: Update `wiki.md`**

In the "#### Mini App (SP2a): чтение" block, after its list, add:

```markdown
#### Mini App (SP2b): запись

- `POST /api/measurements` — добавить замер (`{pef}`, 100–690): авто-время суток,
  замена авто-записи, ответ `{id, pef, tod, zone, pct, diff}`; уведомляет родителей.
- `PATCH /api/measurements/{id}` / `DELETE /api/measurements/{id}` — только родители.
- `POST /api/measurements/{id}/note` — заметка (≤200 символов), обе роли.
- `web/notify.py` — Telegram-уведомления (паритет с ботом, no-op без бота).
- `bot._web_services` передаёт `bot` в web-слой.
```

- [ ] **Step 3: Update `roadmap.md`**

In the "### 23. Развёртывание и бэкапы" "Сделано" line, replace the tail "запись (SP2b) и настройки/CSV/бэкап (SP2c) — далее." with:

```markdown
запись (SP2b: add/edit/delete замеров и заметки с уведомлениями) — сделано 2026-09-14; настройки/CSV/бэкап (SP2c) — далее.
```

- [ ] **Step 4: Verify docs only**

Run: `git status --porcelain`
Expected: only `README.md`, `wiki.md`, `roadmap.md`.

- [ ] **Step 5: Commit**

```bash
git add README.md wiki.md roadmap.md
git commit -m "docs: Mini App write ops (SP2b)"
```

---

## Self-Review

**1. Spec coverage:**
- §2 Архитектура (`web/notify.py`, `_web_services` +bot) → Tasks 1, 2.
- §3 Права (`require_parent`) → Tasks 3, 4.
- §4.1 add → Task 3; §4.2 edit → Task 4; §4.3 delete → Task 4; §4.4 note → Task 5.
- §5 Уведомления → Task 1 (+ wire-in Task 3).
- §6 Фронтенд (add, edit/delete, role) → Tasks 6, 7.
- §7 Обработка ошибок → 422/403/404 covered in Tasks 3–5; banner in 6–7.
- §8 Тесты → Tasks 1, 3, 4, 5 (+ static test in 6).
- §9 Файлы — все покрыты; §10 безопасность — auth/limits/уведомления плоским текстом.

**2. Placeholder scan:** в коде шагов нет TBD/TODO. (Task 1 Step 3 warns against a leftover `_send_all`/`_LAST_TEXT` scaffold; the final file is defined.)

**3. Type consistency:** `create_app(services)` сохраняет сигнатуру; `require_parent` зависит от `require_user`; ключи ответа (`id/pef/tod/zone/pct/diff`, `deleted`, `note/truncated`) совпадают между API (Tasks 3–5) и фронтендом (Tasks 6–7); `_auto_time_of_day` monkeypatch-ится в тестах; `notify_added(bot, config, who, pef, tod, target)` единообразно в Task 1 и Task 3.

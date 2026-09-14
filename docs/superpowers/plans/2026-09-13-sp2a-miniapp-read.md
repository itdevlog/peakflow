# SP2a: Mini App (только чтение) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в бота Telegram Mini App, доступный только семье, который показывает статус, историю, интерактивный график и статистику ПСВ.

**Architecture:** FastAPI-эндпоинты в уже работающем процессе бота (SP1) читают существующую SQLite-БД через функции `database.py`; авторизация — проверка HMAC-подписи Telegram `initData`; фронтенд — статический vanilla-JS в `web/static`, график рисуется вручную на `<canvas>`.

**Tech Stack:** Python 3.11+, FastAPI 0.141.1 (уже установлен), aiogram 3.31.0, SQLite, vanilla JS + Telegram WebApp SDK.

**Spec:** `docs/superpowers/specs/2026-09-13-sp2a-miniapp-read-design.md`

## Global Constraints

- Python 3.11+; существующие пины: `fastapi==0.141.1`, `uvicorn==0.52.4`, `httpx==0.28.1`.
- Тесты — в корне репозитория, запуск `venv/bin/python -m pytest <файл> -v`. Корневой `conftest.py` уже ставит `DB_PATH=test_peakflow.db` до сбора.
- БД — `DB_PATH` из переданного `config` (в тестах — тестовая БД через `setup_db`-подобную фикстуру).
- Все `/api/*` требуют валидный `X-Telegram-Init-Data`; не-семья → `403`; пустые данные → пустые списки, не `500`.
- Бэкенд не импортирует `bot.py` (тяжёлый: aiogram + matplotlib + Bot). Хелперы (`MONTH_NAMES`, зоны, target) локальны в `web/api.py`.
- Секреты не логировать и не отдавать.
- Не добавлять новых Python-зависимостей.
- Комментарии — только по образцу существующего кода.

---

### Task 1: `web/auth.py` — валидация Telegram initData

**Files:**
- Create: `web/auth.py`
- Test: `test_webapp_auth.py`

**Interfaces:**
- Produces: `web.auth.parse_init_data(init_data: str) -> dict`, `web.auth.validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict | None`, `web.auth.get_user_from_init_data(init_data: str, bot_token: str) -> dict | None`.

- [ ] **Step 1: Write the failing test**

Create `test_webapp_auth.py`:

```python
"""Валидация Telegram initData (HMAC-SHA256)."""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

from web.auth import get_user_from_init_data, parse_init_data, validate_init_data

BOT_TOKEN = "123456:ABC-DEF_token"
SECRET = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()


def _make_init_data(params: dict, sign: bool = True, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    params = {**params, "auth_date": str(auth_date)}
    pairs = sorted(params.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    if sign:
        sig = hmac.new(SECRET, data_check_string.encode(), hashlib.sha256).hexdigest()
        pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def test_parse_init_data_decodes_user():
    user = {"id": 42, "first_name": "Иван"}
    raw = _make_init_data({"user": json.dumps(user, ensure_ascii=False)})
    parsed = parse_init_data(raw)
    assert parsed["user"]["id"] == 42
    assert parsed["user"]["first_name"] == "Иван"
    assert "hash" in parsed


def test_validate_ok():
    raw = _make_init_data({"user": json.dumps({"id": 42})})
    result = validate_init_data(raw, BOT_TOKEN)
    assert result is not None
    assert result["user"]["id"] == 42


def test_validate_bad_signature():
    raw = _make_init_data({"user": '{"id": 42}'}, sign=False) + "&hash=deadbeef"
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_expired():
    raw = _make_init_data({"user": '{"id": 42}'}, auth_date=int(time.time()) - 90000)
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_future():
    raw = _make_init_data({"user": '{"id": 42}'}, auth_date=int(time.time()) + 90000)
    assert validate_init_data(raw, BOT_TOKEN) is None


def test_validate_empty():
    assert validate_init_data("", BOT_TOKEN) is None
    assert validate_init_data("auth_date=1&hash=zz", "") is None


def test_validate_wrong_token():
    raw = _make_init_data({"user": '{"id": 42}'})
    assert validate_init_data(raw, "999:other") is None


def test_get_user():
    raw = _make_init_data({"user": '{"id": 7}'})
    assert get_user_from_init_data(raw, BOT_TOKEN) == {"id": 7}


def test_get_user_invalid():
    assert get_user_from_init_data("auth_date=1&hash=zz", BOT_TOKEN) is None


def test_validate_non_ascii_hash_returns_none():
    raw = f"auth_date={int(time.time())}&hash={quote('пароль')}"
    assert validate_init_data(raw, BOT_TOKEN) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.auth'`.

- [ ] **Step 3: Write minimal implementation**

Create `web/auth.py`:

```python
"""Валидация Telegram WebApp initData (HMAC-SHA256)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def parse_init_data(init_data: str) -> dict:
    """Разбирает строку initData в dict; 'user'/'receiver' — из JSON."""
    result: dict = dict(parse_qsl(init_data, keep_blank_values=True))
    for key in ("user", "receiver"):
        if key in result:
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                result[key] = None
    return result


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict | None:
    """Проверяет подпись HMAC и свежесть auth_date. None = невалидно."""
    if not init_data or not bot_token:
        return None
    raw = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = raw.pop("hash", None)
    if not received_hash:
        return None
    try:
        auth_age = time.time() - int(raw.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_age < 0 or auth_age > max_age:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(raw.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(calculated.encode(), received_hash.encode()):
            return None
    except (ValueError, TypeError):
        return None
    return parse_init_data(init_data)


def get_user_from_init_data(init_data: str, bot_token: str) -> dict | None:
    """Валидирует initData и возвращает dict пользователя (или None)."""
    data = validate_init_data(init_data, bot_token)
    user = (data or {}).get("user")
    return user if isinstance(user, dict) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_auth.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add web/auth.py test_webapp_auth.py
git commit -m "feat(web): Telegram initData validation"
```

---

### Task 2: `web/api.py` — авторизация + `/api/me`

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_api.py`

**Interfaces:**
- Consumes: `web.auth.get_user_from_init_data`; `database.get_setting`.
- Produces: внутри `create_app`: `_resolve_user(init_data) -> dict` (чистая, поднимает `HTTPException(403)`); `require_user` FastAPI-зависимость; `GET /api/me -> {user, role, child_name, target_pef}`.
- Config, который читает API (через `services["config"]`): `BOT_TOKEN`, `CHILD_ID`, `PARENT_IDS`, `CHILD_NAME`, `TARGET_PEF`, `DB_PATH`.

- [ ] **Step 1: Write the failing test**

Replace `test_webapp_api.py` with (keeps SP1 healthz tests; adds auth + me). Note the local `_make_init_data` generator and a `_client` that builds a fake config namespace:

```python
"""API Mini App через FastAPI TestClient (SP2a: чтение)."""
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import quote

from fastapi.testclient import TestClient

from database import init_db
from web.api import create_app

TEST_DB = "test_peakflow.db"
BOT_TOKEN = "123456:ABC-DEF_token"
SECRET = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
CHILD_ID = 111
PARENT_IDS = [222, 333]


def make_init_data(user_id: int, token: str = BOT_TOKEN, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    params = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id, "first_name": "Test"})}
    pairs = sorted(params.items())
    dcs = "\n".join(f"{k}={v}" for k, v in pairs)
    sig = hmac.new(
        hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(),
        dcs.encode(), hashlib.sha256,
    ).hexdigest()
    pairs.append(("hash", sig))
    return "&".join(f"{k}={quote(str(v))}" for k, v in pairs)


def _config():
    return SimpleNamespace(
        DB_PATH=TEST_DB, BOT_TOKEN=BOT_TOKEN, CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS,
        CHILD_NAME="Motya", TARGET_PEF=260,
    )


def _client(config=None) -> TestClient:
    return TestClient(create_app({"config": config or _config()}))


def _auth(uid: int) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(uid)}


def _setup_db():
    for ext in ["", "-wal", "-shm", "-journal"]:
        if os.path.exists(TEST_DB + ext):
            os.remove(TEST_DB + ext)
    init_db(TEST_DB)


def test_healthz_ok():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_path_404():
    assert _client().get("/nope").status_code == 404


def test_healthz_bot_down_returns_503():
    cfg = _config()
    r = TestClient(create_app({"config": cfg, "state": {"bot_ok": False}})).get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "bot down"}


def test_api_requires_init_data():
    assert _client().get("/api/me").status_code == 403


def test_api_invalid_signature_forbidden():
    r = _client().get("/api/me", headers={"X-Telegram-Init-Data": make_init_data(CHILD_ID, token="bad")})
    assert r.status_code == 403


def test_api_outsider_forbidden():
    assert _client().get("/api/me", headers=_auth(999)).status_code == 403


def test_api_child_role():
    _setup_db()
    body = _client().get("/api/me", headers=_auth(CHILD_ID)).json()
    assert body["role"] == "child"
    assert body["child_name"] == "Motya"
    assert body["target_pef"] == 260


def test_api_parent_role():
    _setup_db()
    body = _client().get("/api/me", headers=_auth(PARENT_IDS[0])).json()
    assert body["role"] == "parent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: FAIL — `/api/me` returns 404 (not implemented), auth tests fail.

- [ ] **Step 3: Write minimal implementation**

Replace `web/api.py` with:

```python
"""REST API Mini App. SP2a: чтение данных дневника ПСВ."""
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from database import get_setting
from web.auth import get_user_from_init_data


def _effective_target(config) -> int:
    try:
        val = int(get_setting(config.DB_PATH, "target_pef", str(config.TARGET_PEF)))
        return val if val > 0 else 300
    except (ValueError, Exception):
        return getattr(config, "TARGET_PEF", 0) or 300


def create_app(services: dict) -> FastAPI:
    app = FastAPI(title="Peakflow Bot Mini App API", docs_url=None, redoc_url=None)
    config = services.get("config")

    def _resolve_user(init_data: str | None) -> dict:
        token = getattr(config, "BOT_TOKEN", "") or ""
        if not token or not init_data:
            raise HTTPException(403, "Нет доступа")
        user = get_user_from_init_data(init_data, token)
        if not user:
            raise HTTPException(403, "Нет доступа")
        uid = user.get("id")
        if uid == getattr(config, "CHILD_ID", 0):
            role = "child"
        elif uid in (getattr(config, "PARENT_IDS", []) or []):
            role = "parent"
        else:
            raise HTTPException(403, "Нет доступа")
        return {"user": user, "role": role}

    def require_user(x_telegram_init_data: str | None = Header(None)) -> dict:
        return _resolve_user(x_telegram_init_data)

    @app.get("/healthz")
    async def healthz():
        state = services.get("state")
        if state is not None and not state.get("bot_ok", True):
            return JSONResponse(status_code=503, content={"status": "bot down"})
        return {"status": "ok"}

    @app.get("/api/me")
    async def me(auth: dict = Depends(require_user)):
        return {
            "user": auth["user"],
            "role": auth["role"],
            "child_name": getattr(config, "CHILD_NAME", "Ребёнок"),
            "target_pef": _effective_target(config),
        }

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_api.py
git commit -m "feat(web): initData auth and /api/me"
```

---

### Task 3: `/api/status`, `/api/summary`, `/api/history`

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_api.py`

**Interfaces:**
- Consumes: `database.get_today_measurements`, `get_last_measurement`, `get_measurements_paginated`.
- Produces: `GET /api/status`, `GET /api/summary`, `GET /api/history?page=&per_page=`.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_api.py`:

```python
from database import add_measurement


def test_api_status_and_summary_empty():
    _setup_db()
    body = _client().get("/api/status", headers=_auth(CHILD_ID)).json()
    assert body["today"] == []
    assert body["last"] is None
    assert body["target_pef"] == 260
    summary = _client().get("/api/summary", headers=_auth(CHILD_ID)).json()
    assert summary["today"] == []


def test_api_history_pagination():
    _setup_db()
    for i in range(3):
        add_measurement(TEST_DB, 200 + i, "morning", CHILD_ID, PARENT_IDS[0])
    body = _client().get("/api/history?page=1&per_page=2", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2
    # measured_at has second precision: don't assert intra-page order
    assert {it["pef_value"] for it in body["items"]} <= {200, 201, 202}


def test_api_status_has_today():
    _setup_db()
    add_measurement(TEST_DB, 240, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/status", headers=_auth(CHILD_ID)).json()
    assert len(body["today"]) == 1
    assert body["last"]["pef_value"] == 240
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: FAIL — `/api/status` → 404.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add imports and routes inside `create_app` (before `return app`). Change the import block:

```python
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from database import (
    get_last_measurement,
    get_measurements_paginated,
    get_setting,
    get_today_measurements,
)
from web.auth import get_user_from_init_data
```

Add routes after `/api/me`:

```python
    @app.get("/api/status")
    async def status(auth: dict = Depends(require_user)):
        return {
            "today": get_today_measurements(config.DB_PATH, config.CHILD_ID),
            "last": get_last_measurement(config.DB_PATH, config.CHILD_ID),
            "target_pef": _effective_target(config),
        }

    @app.get("/api/summary")
    async def summary(auth: dict = Depends(require_user)):
        return {
            "today": get_today_measurements(config.DB_PATH, config.CHILD_ID),
            "target_pef": _effective_target(config),
        }

    @app.get("/api/history")
    async def history(
        page: int = Query(1, ge=1),
        per_page: int = Query(10, ge=1, le=50),
        auth: dict = Depends(require_user),
    ):
        items, total, total_pages = get_measurements_paginated(
            config.DB_PATH, config.CHILD_ID, page, per_page
        )
        return {"items": items, "page": page, "total": total, "total_pages": total_pages}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_api.py
git commit -m "feat(web): status, summary, history endpoints"
```

---

### Task 4: `/api/chart`

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_api.py`

**Interfaces:**
- Consumes: `database.get_measurements_for_month`, `get_available_months`; `config.TZ_OFFSET`, `ZONE_GREEN`, `ZONE_YELLOW`, `ZONE_RED`.
- Produces: `GET /api/chart?year=&month=` → `{points, target_pef, zones, month, title, can_prev, can_next, available_months}`.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_api.py`:

```python
def test_api_chart_current_month_excludes_auto_and_has_zones():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    add_measurement(TEST_DB, 180, "evening", CHILD_ID, CHILD_ID, source="auto")
    body = _client().get("/api/chart", headers=_auth(CHILD_ID)).json()
    assert body["target_pef"] == 260
    assert body["zones"]["green"] == 80
    assert body["zones"]["yellow"] == 60
    assert len(body["points"]) == 1
    assert body["points"][0]["pef"] == 250
    assert body["points"][0]["tod"] == "morning"


def test_api_chart_explicit_month_and_nav():
    _setup_db()
    add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/chart?year=2020&month=1", headers=_auth(CHILD_ID)).json()
    assert body["month"] == "2020-01"
    assert body["points"] == []
    assert body["can_next"] is True
    assert body["can_prev"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: FAIL — `/api/chart` → 404.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add imports:

```python
from datetime import datetime, timedelta, timezone

from database import (
    get_available_months,
    get_last_measurement,
    get_measurements_for_month,
    get_measurements_paginated,
    get_setting,
    get_today_measurements,
)
```

Add module-level constants and helper above `create_app`:

```python
MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
```

Add route after `/api/history`:

```python
    @app.get("/api/chart")
    async def chart(
        year: int | None = Query(None, ge=2000, le=2100),
        month: int | None = Query(None, ge=1, le=12),
        auth: dict = Depends(require_user),
    ):
        offset = getattr(config, "TZ_OFFSET", 0)
        now = datetime.now(timezone(timedelta(hours=offset)))
        if year is None or month is None:
            year, month = now.year, now.month
        rows = get_measurements_for_month(config.DB_PATH, config.CHILD_ID, year, month)
        points = [
            {
                "date": str(r["measured_at"])[:10],
                "tod": r["time_of_day"],
                "pef": r["pef_value"],
                "source": r.get("source") or "manual",
            }
            for r in rows
        ]
        available = get_available_months(config.DB_PATH, config.CHILD_ID)
        requested = (year, month)
        return {
            "points": points,
            "target_pef": _effective_target(config),
            "zones": {
                "green": getattr(config, "ZONE_GREEN", 80),
                "yellow": getattr(config, "ZONE_YELLOW", 60),
                "red": getattr(config, "ZONE_RED", 50),
            },
            "month": f"{year:04d}-{month:02d}",
            "title": f"{MONTH_NAMES[month - 1]} {year}",
            "can_prev": any(m < requested for m in available),
            "can_next": any(m > requested for m in available),
            "available_months": [f"{y:04d}-{m:02d}" for y, m in available],
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_api.py
git commit -m "feat(web): chart endpoint"
```

---

### Task 5: `/api/stats` и `/api/weekly`

**Files:**
- Modify: `web/api.py`
- Test: `test_webapp_api.py`

**Interfaces:**
- Consumes: `database.get_stats`, `get_last_two_weeks`.
- Produces: `GET /api/stats`, `GET /api/weekly?offset=`.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_api.py`:

```python
def test_api_stats():
    _setup_db()
    for i in range(3):
        add_measurement(TEST_DB, 200 + i * 10, "morning", CHILD_ID, CHILD_ID)
    body = _client().get("/api/stats", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 3
    assert body["avg"] == 210
    assert body["target_pef"] == 260


def test_api_stats_empty():
    _setup_db()
    body = _client().get("/api/stats", headers=_auth(CHILD_ID)).json()
    assert body["total"] == 0


def test_api_weekly_shape():
    _setup_db()
    body = _client().get("/api/weekly?offset=0", headers=_auth(CHILD_ID)).json()
    assert isinstance(body["this_week"], list)
    assert isinstance(body["prev_week"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: FAIL — `/api/stats` → 404.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`, add to imports:

```python
from database import (
    get_available_months,
    get_last_measurement,
    get_last_two_weeks,
    get_measurements_for_month,
    get_measurements_paginated,
    get_setting,
    get_stats,
    get_today_measurements,
)
```

Add routes after `/api/chart`:

```python
    @app.get("/api/stats")
    async def stats(auth: dict = Depends(require_user)):
        data = get_stats(config.DB_PATH, config.CHILD_ID)
        data["target_pef"] = _effective_target(config)
        return data

    @app.get("/api/weekly")
    async def weekly(offset: int = Query(0, ge=0, le=0), auth: dict = Depends(require_user)):
        this_week, prev_week = get_last_two_weeks(config.DB_PATH, config.CHILD_ID)
        return {"this_week": this_week, "prev_week": prev_week, "offset": offset}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: PASS (16 passed).

- [ ] **Step 5: Commit**

```bash
git add web/api.py test_webapp_api.py
git commit -m "feat(web): stats and weekly endpoints"
```

---

### Task 6: Статика — `index.html`, `style.css`, `app.js` (табы, статус, история, статистика)

**Files:**
- Create: `web/static/index.html`
- Create: `web/static/style.css`
- Create: `web/static/app.js`
- Modify: `web/api.py` (mount static at the end of `create_app`)
- Test: `test_webapp_api.py`

**Interfaces:**
- Consumes: REST from Tasks 2–5.
- Produces: `GET /` отдаёт `index.html`; `GET /api/*` остаётся приоритетнее статики.

- [ ] **Step 1: Write the failing test**

Append to `test_webapp_api.py`:

```python
def test_static_index_served():
    r = _client().get("/")
    assert r.status_code == 200
    assert "telegram-web-app.js" in r.text
    assert 'id="app"' in r.text


def test_api_not_shadowed_by_static():
    assert _client().get("/api/me").status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: FAIL — `/` returns 404 (static not mounted / files missing).

- [ ] **Step 3: Write the files**

Create `web/static/index.html`:

```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Дневник ПСВ</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div id="app">
  <header id="topbar">
    <span id="child-name">…</span>
    <span id="target-badge"></span>
  </header>
  <nav id="tabs">
    <button class="tab active" data-screen="today">Сегодня</button>
    <button class="tab" data-screen="history">История</button>
    <button class="tab" data-screen="chart">График</button>
    <button class="tab" data-screen="stats">Статистика</button>
  </nav>
  <div id="error" hidden></div>
  <main>
    <section id="screen-today" class="screen"></section>
    <section id="screen-history" class="screen" hidden></section>
    <section id="screen-chart" class="screen" hidden>
      <div id="chart-nav">
        <button id="chart-prev">‹</button>
        <span id="chart-title"></span>
        <button id="chart-next">›</button>
      </div>
      <canvas id="chart" height="320"></canvas>
      <div id="chart-tip" hidden></div>
    </section>
    <section id="screen-stats" class="screen" hidden></section>
  </main>
</div>
<script src="app.js"></script>
</body>
</html>
```

Create `web/static/style.css`:

```css
:root {
  --bg: var(--tg-theme-bg-color, #ffffff);
  --fg: var(--tg-theme-text-color, #111111);
  --muted: var(--tg-theme-hint-color, #777777);
  --accent: var(--tg-theme-button-color, #2ea6ff);
  --card: var(--tg-theme-secondary-bg-color, #f4f4f5);
  --green: #2fb344;
  --yellow: #e8a600;
  --red: #e5484d;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, system-ui, Roboto, sans-serif; }
#app { max-width: 720px; margin: 0 auto; padding: 12px; }
#topbar { display: flex; justify-content: space-between; align-items: center;
  font-weight: 600; margin-bottom: 8px; }
#target-badge { font-weight: 400; color: var(--muted); font-size: 14px; }
#tabs { display: flex; gap: 4px; margin-bottom: 12px; }
.tab { flex: 1; padding: 10px 4px; border: none; border-radius: 8px;
  background: var(--card); color: var(--fg); font-size: 14px; }
.tab.active { background: var(--accent); color: #fff; }
#error { background: var(--red); color: #fff; padding: 10px; border-radius: 8px;
  margin-bottom: 10px; }
.screen { display: flex; flex-direction: column; gap: 8px; }
.card { background: var(--card); border-radius: 10px; padding: 12px; }
.card .big { font-size: 28px; font-weight: 700; }
.card .label { color: var(--muted); font-size: 13px; }
.zone-green { color: var(--green); }
.zone-yellow { color: var(--yellow); }
.zone-red { color: var(--red); }
.row { display: flex; justify-content: space-between; align-items: center; }
.note { color: var(--muted); font-size: 13px; }
.auto { color: var(--muted); }
.pager { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
.pager button { padding: 8px 16px; border: none; border-radius: 8px;
  background: var(--card); color: var(--fg); }
#chart-nav { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px; }
#chart-nav button { padding: 6px 14px; border: none; border-radius: 8px;
  background: var(--card); color: var(--fg); font-size: 18px; }
#chart { width: 100%; background: var(--card); border-radius: 10px; }
#chart-tip { background: var(--fg); color: var(--bg); padding: 6px 10px;
  border-radius: 8px; font-size: 13px; display: inline-block; margin-top: 6px; }
.hint { color: var(--muted); text-align: center; padding: 24px 0; }
```

Create `web/static/app.js`:

```javascript
/* Mini App «Дневник ПСВ»: vanilla JS + Telegram WebApp SDK. */
const tg = window.Telegram.WebApp;
try { tg.ready(); tg.expand(); } catch (e) {}

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function zoneClass(pct) {
  if (pct >= 80) return "zone-green";
  if (pct >= 60) return "zone-yellow";
  return "zone-red";
}

const state = { target: 0, chart: { year: null, month: null }, history: { page: 1 } };

async function api(path) {
  const r = await fetch(path, { headers: { "X-Telegram-Init-Data": tg.initData || "" } });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: "Ошибка сети" }));
    throw new Error(body.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.hidden = false;
}

function clearError() { $("error").hidden = true; }

function todLabel(tod) { return tod === "morning" ? "☀️ Утро" : "🌙 Вечер"; }

function pct(value) {
  return state.target ? Math.round((value / state.target) * 100) : 100;
}

function measureCard(m) {
  const p = pct(m.pef_value);
  const auto = (m.source === "auto") ? ' <span class="auto">🤖</span>' : "";
  const note = m.note ? `<div class="note">ℹ️ ${esc(m.note)}</div>` : "";
  return `<div class="card">
    <div class="row">
      <span class="label">${todLabel(m.time_of_day)}</span>
      <span class="label">${esc(String(m.measured_at).slice(5, 16))}</span>
    </div>
    <div class="big ${zoneClass(p)}">${m.pef_value} <span class="label">${p}%</span>${auto}</div>
    ${note}
  </div>`;
}

async function loadToday() {
  const s = await api("/api/status");
  state.target = s.target_pef;
  $("target-badge").textContent = `цель ${s.target_pef}`;
  const el = $("screen-today");
  if (!s.today.length) {
    el.innerHTML = `<div class="hint">Сегодня замеров ещё нет 💨</div>`;
    return;
  }
  el.innerHTML = s.today.map(measureCard).join("");
}

async function loadHistory(page = 1) {
  state.history.page = page;
  const h = await api(`/api/history?page=${page}&per_page=10`);
  const el = $("screen-history");
  if (!h.items.length) {
    el.innerHTML = `<div class="hint">История пуста</div>`;
    return;
  }
  el.innerHTML = h.items.map(measureCard).join("") + `<div class="pager">
    <button id="hist-prev" ${page <= 1 ? "disabled" : ""}>‹</button>
    <span class="label">${page} / ${h.total_pages}</span>
    <button id="hist-next" ${page >= h.total_pages ? "disabled" : ""}>›</button>
  </div>`;
  const prev = $("hist-prev"), next = $("hist-next");
  if (prev) prev.onclick = () => loadHistory(page - 1);
  if (next) next.onclick = () => loadHistory(page + 1);
}

async function loadStats() {
  const s = await api("/api/stats");
  state.target = s.target_pef || state.target;
  const el = $("screen-stats");
  if (!s.total) { el.innerHTML = `<div class="hint">Недостаточно данных</div>`; return; }
  const trend = (s.trend == null) ? "—" : (s.trend > 0 ? `↑ +${s.trend.toFixed(1)}` : `↓ ${s.trend.toFixed(1)}`);
  const avg = (v) => (v == null ? "—" : Math.round(v));
  el.innerHTML = `
    <div class="card"><div class="label">Всего замеров</div><div class="big">${s.total}</div></div>
    <div class="card"><div class="label">Среднее</div><div class="big">${avg(s.avg)}</div></div>
    <div class="card"><div class="label">Мин / Макс</div><div class="big">${s.min} / ${s.max}</div></div>
    <div class="card"><div class="label">Утро (сред.)</div><div class="big">${avg(s.morning_avg)} <span class="label">×${s.morning_count}</span></div></div>
    <div class="card"><div class="label">Вечер (сред.)</div><div class="big">${avg(s.evening_avg)} <span class="label">×${s.evening_count}</span></div></div>
    <div class="card"><div class="label">Тренд (3 vs 3)</div><div class="big">${trend}</div></div>`;
}

async function switchTo(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.screen === name));
  document.querySelectorAll(".screen").forEach((s) =>
    s.hidden = (s.id !== `screen-${name}`));
  clearError();
  try {
    if (name === "today") await loadToday();
    else if (name === "history") await loadHistory(1);
    else if (name === "stats") await loadStats();
    else if (name === "chart") await loadChart();
  } catch (e) { showError(e.message); }
}

document.querySelectorAll(".tab").forEach((t) =>
  t.onclick = () => switchTo(t.dataset.screen));

/* ---- chart (Task 7 fills this in) ---- */
async function loadChart() {
  $("screen-chart").innerHTML = `<div class="hint">График загружается…</div>`;
}

switchTo("today");
```

Add static mounting at the end of `create_app` in `web/api.py` (import `os` and `StaticFiles`):

```python
import os

from fastapi.staticfiles import StaticFiles
```

and before `return app`:

```python
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_webapp_api.py -v`
Expected: PASS (18 passed). Static `/` served; `/api/me` still 403 (routes precede the mount).

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/style.css web/static/app.js web/api.py test_webapp_api.py
git commit -m "feat(web): Mini App shell, tabs, status/history/stats"
```

---

### Task 7: Интерактивный canvas-график

**Files:**
- Modify: `web/static/app.js` (`loadChart` and chart helpers)
- Test: manual (browser/canvas; no unit test in this repo)

**Interfaces:**
- Consumes: `GET /api/chart` (Task 4), DOM `#chart`, `#chart-prev`, `#chart-next`, `#chart-title`, `#chart-tip`.

- [ ] **Step 1: Replace the `loadChart` stub**

In `web/static/app.js`, replace the chart section (from `/* ---- chart (Task 7 fills this in) ---- */` to the `switchTo("today");` line) with:

```javascript
/* ---- chart (canvas, no libraries) ---- */
function initChart() {
  const c = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const cssW = c.clientWidth || c.parentElement.clientWidth || 320;
  const cssH = 320;
  c.width = Math.round(cssW * dpr);
  c.height = Math.round(cssH * dpr);
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { c, ctx, cssW, cssH };
}

function drawChart(data, points) {
  const { c, ctx, cssW, cssH } = initChart();
  ctx.clearRect(0, 0, cssW, cssH);
  const padL = 38, padR = 12, padT = 12, padB = 24;
  const plotW = cssW - padL - padR;
  const plotH = cssH - padT - padB;

  const values = points.map((p) => p.pef);
  const target = data.target_pef || 0;
  const all = values.concat([target]).filter((v) => v > 0);
  let yMax = all.length ? Math.max(...all) : 300;
  let yMin = all.length ? Math.min(...all) : 0;
  if (yMax === yMin) { yMax += 20; yMin = Math.max(0, yMin - 20); }
  const margin = Math.round((yMax - yMin) * 0.15) || 10;
  yMax += margin; yMin = Math.max(0, yMin - margin);

  const yToPx = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const n = points.length;
  const xToPx = (i) => (n <= 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW);

  const green = data.zones.green, yellow = data.zones.yellow;
  const band = (fromPct, toPct, color) => {
    if (!target) return;
    const y1 = yToPx((toPct / 100) * target);
    const y2 = yToPx((fromPct / 100) * target);
    ctx.fillStyle = color;
    ctx.fillRect(padL, Math.max(padT, y1), plotW, Math.min(cssH - padB, y2) - Math.max(padT, y1));
  };
  ctx.globalAlpha = 0.10;
  band(0, yellow, "#e5484d");
  band(yellow, green, "#e8a600");
  band(green, 200, "#2fb344");
  ctx.globalAlpha = 1;

  ctx.strokeStyle = "#999";
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, cssH - padB); ctx.lineTo(cssW - padR, cssH - padB);
  ctx.stroke();

  ctx.fillStyle = "#777"; ctx.font = "10px sans-serif";
  for (let k = 0; k <= 4; k++) {
    const v = yMin + ((yMax - yMin) * k) / 4;
    const y = yToPx(v);
    ctx.fillText(String(Math.round(v)), 4, y + 3);
  }

  if (target) {
    ctx.strokeStyle = "#2ea6ff"; ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(padL, yToPx(target)); ctx.lineTo(cssW - padR, yToPx(target)); ctx.stroke();
    ctx.setLineDash([]);
  }

  if (n >= 2) {
    ctx.strokeStyle = "#555"; ctx.lineWidth = 1.5; ctx.beginPath();
    points.forEach((p, i) => { const x = xToPx(i), y = yToPx(p.pef);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
  }

  ctx.lineWidth = 1;
  points.forEach((p, i) => {
    ctx.fillStyle = p.tod === "morning" ? "#e8a600" : "#7a5cff";
    ctx.beginPath(); ctx.arc(xToPx(i), yToPx(p.pef), 3.5, 0, Math.PI * 2); ctx.fill();
  });

  c._points = points.map((p, i) => ({ x: xToPx(i), y: yToPx(p.pef), p }));
}

function chartClick(ev) {
  const c = $("chart");
  const pts = c._points || [];
  const rect = c.getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  let best = null, bestD = 1e9;
  for (const q of pts) {
    const d = (q.x - x) ** 2 + (q.y - y) ** 2;
    if (d < bestD) { bestD = d; best = q; }
  }
  const tip = $("chart-tip");
  if (best && bestD < 900) {
    tip.hidden = false;
    tip.textContent = `${best.p.date} · ${todLabel(best.p.tod)} · ${best.p.pef}`;
  } else { tip.hidden = true; }
}

async function loadChart(year, month) {
  const q = (year && month) ? `?year=${year}&month=${month}` : "";
  const data = await api(`/api/chart${q}`);
  state.target = data.target_pef || state.target;
  state.chart = { year: data.month.slice(0, 4), month: data.month.slice(5, 7) };
  $("chart-title").textContent = data.title;
  $("chart-prev").disabled = !data.can_prev;
  $("chart-next").disabled = !data.can_next;
  $("chart-tip").hidden = true;
  if (!data.points.length) {
    initChart().ctx.clearRect(0, 0, 9999, 9999);
    $("chart").style.display = "none";
    $("screen-chart").querySelector(".hint")?.remove();
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = "В этом месяце замеров нет";
    $("screen-chart").appendChild(hint);
    return;
  }
  $("chart").style.display = "block";
  $("screen-chart").querySelector(".hint")?.remove();
  drawChart(data, data.points);
}

$("chart-prev").onclick = () => shiftMonth(-1);
$("chart-next").onclick = () => shiftMonth(1);
$("chart").addEventListener("click", chartClick);

async function shiftMonth(delta) {
  const data = await api(`/api/chart?year=${state.chart.year}&month=${state.chart.month}`);
  const months = data.available_months;
  const cur = `${state.chart.year}-${state.chart.month}`;
  let idx = months.indexOf(cur);
  if (idx === -1) {
    const sorted = [...months, cur].sort();
    idx = sorted.indexOf(cur);
  }
  const nextIdx = idx + delta;
  if (nextIdx < 0 || nextIdx >= months.length) return;
  const [y, m] = months[nextIdx].split("-");
  await loadChart(Number(y), Number(m));
}

switchTo("today");
```

- [ ] **Step 2: Verify syntax (no JS toolchain available)**

Run: `venv/bin/python - <<'PY'\nimport pathlib\nsrc = pathlib.Path("web/static/app.js").read_text()\nassert src.count("(") == src.count(")"), "unbalanced parens"\nassert src.count("{") == src.count("}"), "unbalanced braces"\nprint("app.js balance OK", len(src), "bytes")\nPY`
Expected: `app.js balance OK`.

- [ ] **Step 3: Manual smoke (server)**

Run:
```bash
timeout 15 bash -c 'WEBAPP_PORT=18082 DB_PATH=/tmp/sp2a.db BOT_TOKEN=123456789:AAFakeTokenForSmokeTest0123456789 CHILD_ID=1 PARENT_IDS=2 ./venv/bin/python bot.py > /tmp/sp2a.log 2>&1 & sleep 6; curl -s -o /dev/null -w "index=%{http_code}\n" http://localhost:18082/; curl -s http://localhost:18082/app.js | head -c 40; echo; kill %1 2>/dev/null'
```
Expected: `index=200` and `/* Mini App` printed. Clean up `/tmp/sp2a*`.

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js
git commit -m "feat(web): interactive canvas chart with month navigation"
```

---

### Task 8: Кнопка Mini App в боте

**Files:**
- Modify: `bot.py` (import `WEBAPP_URL`; add `_setup_menu_button`; call from `on_startup`)
- Test: `test_bot.py`

**Interfaces:**
- Consumes: `config.WEBAPP_URL`, `config.WEBAPP_PORT`, `bot.bot`.
- Produces: `bot._setup_menu_button()` (async).

- [ ] **Step 1: Write the failing test**

Append to `test_bot.py` (after the existing tests, at module level):

```python
class TestMenuButton:
    def test_menu_button_set_when_url(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "https://pick.example.com")
        monkeypatch.setattr(bot, "WEBAPP_PORT", 8080)
        mock = AsyncMock()
        monkeypatch.setattr(bot.bot, "set_chat_menu_button", mock)
        asyncio.run(bot._setup_menu_button())
        mock.assert_awaited_once()
        assert mock.await_args.kwargs["menu_button"].web_app.url == "https://pick.example.com"

    def test_menu_button_skipped_without_url(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "")
        mock = AsyncMock()
        monkeypatch.setattr(bot.bot, "set_chat_menu_button", mock)
        asyncio.run(bot._setup_menu_button())
        mock.assert_not_awaited()

    def test_menu_button_error_is_swallowed(self, monkeypatch):
        import asyncio
        import bot
        from unittest.mock import AsyncMock
        monkeypatch.setattr(bot, "WEBAPP_URL", "https://pick.example.com")
        monkeypatch.setattr(bot, "WEBAPP_PORT", 8080)
        monkeypatch.setattr(bot.bot, "set_chat_menu_button",
                            AsyncMock(side_effect=RuntimeError("boom")))
        asyncio.run(bot._setup_menu_button())  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest "test_bot.py::TestMenuButton" -v`
Expected: FAIL — `AttributeError: module 'bot' has no attribute '_setup_menu_button'`.

- [ ] **Step 3: Write minimal implementation**

In `bot.py`, add `WEBAPP_URL` to the `from config import (...)` block:

```python
    WEEKLY_REPORT_DAY, WEEKLY_REPORT_HOUR, TZ_OFFSET,
    WEBAPP_PORT, WEBAPP_URL,
```

Replace `on_startup` and add `_setup_menu_button`:

```python
async def on_startup():
    asyncio.create_task(scheduler_loop())
    logger.info("Бот запущен, планировщик активен")
    await _setup_menu_button()


async def _setup_menu_button():
    if not WEBAPP_URL or not WEBAPP_PORT:
        return
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="💨 Дневник", web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
        logger.info("Кнопка Mini App установлена: %s", WEBAPP_URL)
    except Exception as e:
        logger.error("Не удалось установить кнопку Mini App: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest "test_bot.py::TestMenuButton" -v && venv/bin/python -m pytest -q`
Expected: 3 passed (TestMenuButton); whole suite 118 passed, 0 failures
(baseline 90 + 10 auth + 5 new api + 3 status/summary/history + 2 chart + 3 stats/weekly + 2 static + 3 menu).

- [ ] **Step 5: Commit**

```bash
git add bot.py test_bot.py
git commit -m "feat(bot): Telegram menu button for Mini App"
```

---

### Task 9: Документация SP2a

**Files:**
- Modify: `README.md`
- Modify: `wiki.md`
- Modify: `roadmap.md`

- [ ] **Step 1: Update `README.md`**

In the "### Веб-версия (Telegram Mini App)" section, replace the body with:

```markdown
### Веб-версия (Telegram Mini App)

Переменные `.env`: `WEBAPP_HOST`, `WEBAPP_PORT` (`0` = выключить веб-сервер),
`WEBAPP_URL` (публичный HTTPS-URL). Для HTTPS запустите `./manage.sh caddy`.
Когда `WEBAPP_URL` задан, бот добавляет кнопку меню «💨 Дневник» — она открывает
Mini App с историей, графиком, статистикой и сводкой. Доступ только у ребёнка и
родителей (проверка Telegram initData). Добавление/редактирование замеров,
настройки, CSV и бэкап в Mini App — следующие подпроекты (SP2b/SP2c).
```

- [ ] **Step 2: Update `wiki.md`**

In the section added by SP1 ("## 26. Веб-слой (Mini App) и эксплуатация (`manage.sh`)"), after the "Веб-слой в процессе бота" table, add:

```markdown
#### Mini App (SP2a): чтение

- `web/auth.py` — проверка HMAC-подписи Telegram `initData` (заголовок
  `X-Telegram-Init-Data`); доступ только у `CHILD_ID` и `PARENT_IDS`, иначе 403.
- `web/api.py` — read-only эндпоинты: `/api/me`, `/api/status`, `/api/summary`,
  `/api/history`, `/api/chart`, `/api/stats`, `/api/weekly`.
- `web/static/` — `index.html`, `app.js` (vanilla JS + Telegram WebApp SDK,
  интерактивный график на `<canvas>`), `style.css`.
- Кнопка «💨 Дневник» ставится в `bot._setup_menu_button()`, если задан
  `WEBAPP_URL`. Наружу биндить только за Caddy (HTTPS обязателен для initData).
```

- [ ] **Step 3: Update `roadmap.md`**

In the "### 23. Развёртывание и бэкапы" "Сделано" line, replace the tail "REST API и фронтенд Mini App — **SP2**" with:

```markdown
SP2a (Mini App чтение: статус/история/график/статистика) — сделано 2026-09-13; запись (SP2b) и настройки/CSV/бэкап (SP2c) — далее.
```

- [ ] **Step 4: Verify no code touched**

Run: `git status --porcelain`
Expected: only `README.md`, `wiki.md`, `roadmap.md` modified.

- [ ] **Step 5: Commit**

```bash
git add README.md wiki.md roadmap.md
git commit -m "docs: Mini App read-only (SP2a)"
```

---

## Self-Review

**1. Spec coverage:**
- §2 Архитектура (auth.py/api.py/static) → Tasks 1, 2, 6, 7.
- §2.1 MenuButton → Task 8.
- §3 auth + права → Tasks 1, 2.
- §4 REST API (me/status/history/chart/stats/summary/weekly) → Tasks 2, 3, 4, 5.
- §5 Фронтенд (index/app/style, canvas-график) → Tasks 6, 7.
- §6 Обработка ошибок → Tasks 1 (403), 3 (пустые), 6 (hint/error), 8 (swallow).
- §7 Тесты → Tasks 1–6, 8.
- §8 Файлы → все покрыты.
- §9 Безопасность → auth (Task 1), docs (Task 9), бинд за Caddy упомянут.

**2. Placeholder scan:** в коде шагов нет TBD/TODO; все блоки дословные.

**3. Type consistency:** `create_app(services)` сохраняет сигнатуру SP1; `require_user`/`_resolve_user` едины в Tasks 2–5; ключи ответов (`target_pef`, `points`, `tod`, `pef`, `zones`, `total`, `total_pages`) совпадают между API (Tasks 3–5) и фронтендом (Tasks 6–7); `WEBAPP_URL` добавлен в импорт bot.py (Task 8) и используется в тесте.

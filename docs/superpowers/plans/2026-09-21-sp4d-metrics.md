# SP4D — Метрики и structured-логи: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Дать наблюдаемость без новых зависимостей: in-process реестр метрик, расширенный `/healthz`, опциональный `GET /metrics` (Prometheus-text) и текстовые логи с устойчивыми полями по запросам, ошибкам и планировщику.

**Architecture:** Новый чистый потокобезопасный модуль `metrics.py` (счётчики/гейджи + рендер Prometheus). `database.get_system_counts` отдаёт агрегаты. Web добавляет HTTP-middleware, расширяет `/healthz` и добавляет env-gated `/metrics` (опциональный bearer-токен). Бот инкрементит счётчики замеров, достижений и планировщика. Формат логов не меняется — добавляются стабильные поля `key=value`, вывод в stdout.

**Tech Stack:** Python 3.11+, stdlib, FastAPI, aiogram 3, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-sp4d-metrics-design.md`

## Global Constraints

- Новых зависимостей нет; `metrics.py` — только stdlib и не импортирует `bot.py`/`database.py`/`aiogram`.
- Метрики потокобезопасны (`threading.Lock`).
- `/metrics` выключен по умолчанию (env `METRICS_ENABLED`), опциональный `METRICS_TOKEN` (Bearer), иначе `401`.
- `/healthz` обратно совместим: успех → 200 с новыми полями; `bot_ok=false` → прежний `503 {"status":"bot down"}`.
- Персональные данные в метрики не попадают (только агрегаты/числа).
- Формат логов текстовый, вывод — stdout.
- Тесты: `venv/bin/python -m pytest test/ -q`; standalone `venv/bin/python -m pytest test/test_webapp_api.py -q`; без `.env`.
- Полная проверка: `venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web && echo ALL_GREEN`.
- Ветка: `sp4d-metrics`; коммиты `feat(...): ... (SP4D)`.

---

## File Structure

- `metrics.py` — новый: реестр + Prometheus-рендер.
- `database.py` — `get_system_counts`.
- `config.py` — `METRICS_ENABLED`, `METRICS_TOKEN`.
- `web/api.py` — HTTP-middleware, расширенный `/healthz`, `GET /metrics`.
- `bot.py` — инкременты замеров/достижений/планировщика.
- `test/test_metrics.py` — новый; `test/test_bot.py`, `test/test_webapp_api.py` — дополнения.
- `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, todo-plan — CI-список и счётчики.

---

### Task 1: `metrics.py` — реестр и Prometheus-рендер

**Files:** Create `metrics.py`; Create `test/test_metrics.py`.

**Interfaces:**
- Produces: `START_TIME`, `uptime_seconds()`, `inc(name, value=1, **labels)`, `set_gauge(name, value, **labels)`, `get_counter(name, **labels) -> int`, `get_gauge(name, **labels)`, `snapshot() -> list[dict]`, `render_prometheus() -> str`, `reset()`.

- [ ] **Step 1: Write the failing test**

```python
"""Тесты in-process метрик (SP4D)."""
import time

import pytest


@pytest.fixture(autouse=True)
def _clean():
    import metrics
    metrics.reset()
    yield
    metrics.reset()


class TestRegistry:
    def test_counter_default_zero(self):
        from metrics import get_counter
        assert get_counter("unknown_metric") == 0

    def test_inc_and_get(self):
        from metrics import inc, get_counter
        inc("hits")
        inc("hits", 2)
        assert get_counter("hits") == 3

    def test_labels_separate_series(self):
        from metrics import inc, get_counter
        inc("http_requests_total", method="GET", status="200")
        inc("http_requests_total", method="POST", status="200")
        assert get_counter("http_requests_total", method="GET", status="200") == 1
        assert get_counter("http_requests_total", method="POST", status="200") == 1
        assert get_counter("http_requests_total", method="GET", status="500") == 0

    def test_gauge_set_and_get(self):
        from metrics import set_gauge, get_gauge
        set_gauge("families_count", 3)
        set_gauge("families_count", 4)
        assert get_gauge("families_count") == 4
        assert get_gauge("missing") is None

    def test_snapshot(self):
        from metrics import inc, set_gauge, snapshot
        inc("a_total")
        set_gauge("b_gauge", 1.5)
        rows = {r["name"]: r for r in snapshot()}
        assert rows["a_total"]["type"] == "counter" and rows["a_total"]["value"] == 1
        assert rows["b_gauge"]["type"] == "gauge" and rows["b_gauge"]["value"] == 1.5

    def test_reset_keeps_uptime(self):
        from metrics import inc, get_counter, reset, uptime_seconds, START_TIME
        inc("x")
        reset()
        assert get_counter("x") == 0
        assert uptime_seconds() >= 0
        assert START_TIME <= time.time()


class TestRender:
    def test_render_format(self):
        from metrics import inc, set_gauge, render_prometheus
        inc("http_requests_total", method="GET", status="200")
        inc("http_requests_total", method="GET", status="200")
        set_gauge("families_count", 2)
        text = render_prometheus()
        assert "# TYPE http_requests_total counter" in text
        assert 'http_requests_total{method="GET",status="200"} 2' in text
        assert "# TYPE families_count gauge" in text
        assert "families_count 2" in text
        # TYPE emitted once per metric name
        assert text.count("# TYPE http_requests_total counter") == 1

    def test_label_value_escaped(self):
        from metrics import inc, render_prometheus
        inc("x_total", note='a"b')
        assert 'note="a\\"b"' in render_prometheus()

    def test_invalid_name(self):
        from metrics import inc
        with pytest.raises(ValueError):
            inc("bad-name")

    def test_invalid_label(self):
        from metrics import inc
        with pytest.raises(ValueError):
            inc("ok_total", **{"bad-label": "v"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 3: Write minimal implementation**

Create `metrics.py`:

```python
"""In-process метрики (SP4D).

Чистый потокобезопасный модуль: только stdlib. Не импортирует
bot.py/database.py/aiogram. Метрики живут в памяти процесса и сбрасываются
при рестарте.
"""
import re
import threading
import time

START_TIME = time.time()

_LOCK = threading.Lock()
_COUNTERS = {}
_GAUGES = {}

_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _check(name, labels):
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid metric name: {name!r}")
    for key in labels:
        if not _LABEL_RE.match(key):
            raise ValueError(f"invalid label name: {key!r}")


def _key(name, labels):
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name, value=1, **labels):
    _check(name, labels)
    key = _key(name, labels)
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + value


def set_gauge(name, value, **labels):
    _check(name, labels)
    with _LOCK:
        _GAUGES[_key(name, labels)] = value


def get_counter(name, **labels):
    with _LOCK:
        return _COUNTERS.get(_key(name, labels), 0)


def get_gauge(name, **labels):
    with _LOCK:
        return _GAUGES.get(_key(name, labels))


def uptime_seconds():
    return time.time() - START_TIME


def snapshot():
    with _LOCK:
        counters = dict(_COUNTERS)
        gauges = dict(_GAUGES)
    rows = [{"name": n, "type": "counter", "labels": dict(l), "value": v}
            for (n, l), v in counters.items()]
    rows += [{"name": n, "type": "gauge", "labels": dict(l), "value": v}
             for (n, l), v in gauges.items()]
    return rows


def _fmt_labels(labels):
    if not labels:
        return ""
    parts = []
    for key in sorted(labels):
        value = str(labels[key]).replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'{key}="{value}"')
    return "{" + ",".join(parts) + "}"


def _groups(store):
    grouped = {}
    for (name, labels), value in sorted(store.items()):
        grouped.setdefault(name, []).append((labels, value))
    return grouped


def render_prometheus():
    with _LOCK:
        counters = dict(_COUNTERS)
        gauges = dict(_GAUGES)
    lines = []
    for kind, store in (("counter", counters), ("gauge", gauges)):
        for name, series in _groups(store).items():
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in series:
                lines.append(f"{name}{_fmt_labels(dict(labels))} {value}")
    return "\n".join(lines) + "\n"


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _GAUGES.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics.py test/test_metrics.py
git commit -m "feat(metrics): in-process registry and Prometheus render (SP4D)"
```

---

### Task 2: `database.get_system_counts`

**Files:** Modify `database.py`; Test `test/test_bot.py` (new `TestSystemCounts`).

**Interfaces:**
- Produces: `get_system_counts(db_path) -> dict` с ключами `families`, `children`, `measurements`.

- [ ] **Step 1: Write the failing test** (append to `test/test_bot.py`)

```python
class TestSystemCounts:
    def test_counts(self):
        from database import (init_db, create_family_with_owner, add_member,
                              add_measurement, get_system_counts)
        init_db(TEST_DB)
        before = get_system_counts(TEST_DB)
        add_measurement(TEST_DB, 250, "morning", 111, 222, family_id=1)
        add_measurement(TEST_DB, 260, "evening", 111, 222, family_id=1)
        f2 = create_family_with_owner(TEST_DB, 500, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        counts = get_system_counts(TEST_DB)
        assert counts["families"] == before["families"] + 1
        assert counts["children"] == before["children"] + 1
        assert counts["measurements"] == before["measurements"] + 2

    def test_empty_db(self):
        from database import init_db, get_system_counts
        init_db(TEST_DB)
        counts = get_system_counts(TEST_DB)
        assert counts["measurements"] == 0
        assert set(counts) == {"families", "children", "measurements"}
```

> The delta form keeps the test independent of the env-seeded child (111 locally, 0 in CI).

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_bot.py::TestSystemCounts -q`
Expected: FAIL — `ImportError: cannot import name 'get_system_counts'`.

- [ ] **Step 3: Write minimal implementation**

Add to `database.py` (after `count_measurements`):

```python
def get_system_counts(db_path: str) -> dict:
    """Aggregate counts for health/metrics (no personal data)."""
    conn = get_connection(db_path)
    try:
        families = conn.execute("SELECT COUNT(*) FROM families").fetchone()[0]
        children = conn.execute(
            "SELECT COUNT(*) FROM members WHERE role = 'child'"
        ).fetchone()[0]
        measurements = conn.execute(
            "SELECT COUNT(*) FROM measurements"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"families": families, "children": children, "measurements": measurements}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_bot.py::TestSystemCounts -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): system counts for health and metrics (SP4D)"
```

---

### Task 3: Web — `/healthz`, `GET /metrics`, HTTP-middleware

**Files:** Modify `config.py`, `web/api.py`; Test `test/test_webapp_api.py`.

**Interfaces:**
- Consumes (Tasks 1–2): `metrics.*`, `get_system_counts`.
- Produces: extended `/healthz`, `GET /metrics`, request-logging middleware; `config.METRICS_ENABLED`, `config.METRICS_TOKEN`.

- [ ] **Step 1: Write the failing tests** (append to `test/test_webapp_api.py`)

```python
class TestMetrics:
    def _metric_config(self, enabled=True, token=""):
        cfg = _config()
        cfg.METRICS_ENABLED = enabled
        cfg.METRICS_TOKEN = token
        return cfg

    def test_healthz_fields(self):
        _setup_db()
        body = _client().get("/healthz").json()
        assert body["status"] == "ok"
        assert "uptime_seconds" in body
        assert body["families"] >= 1
        assert body["children"] is not None
        assert body["measurements"] is not None

    def test_healthz_bot_down(self):
        _setup_db()
        app = create_app({"config": _config(), "state": {"bot_ok": False}})
        r = TestClient(app).get("/healthz")
        assert r.status_code == 503
        assert r.json() == {"status": "bot down"}

    def test_metrics_disabled_404(self):
        _setup_db()
        assert _client(self._metric_config(enabled=False)).get("/metrics").status_code == 404

    def test_metrics_enabled(self):
        _setup_db()
        c = _client(self._metric_config(enabled=True))
        c.get("/healthz")  # middleware counts a prior request
        r = c.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in r.text

    def test_metrics_token_required(self):
        _setup_db()
        c = _client(self._metric_config(enabled=True, token="secret"))
        assert c.get("/metrics").status_code == 401
        r = c.get("/metrics", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200

    def test_request_counted(self):
        import metrics
        metrics.reset()
        _setup_db()
        _client().get("/healthz")
        assert metrics.get_counter("http_requests_total", method="GET", status="200") >= 1
```

Also update the existing `test_healthz_ok` (around line 76) to the subset form:

```python
def test_healthz_ok():
    _setup_db()
    r = _client().get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestMetrics test/test_webapp_api.py::test_healthz_ok -q`
Expected: FAIL — `/healthz` lacks fields / `/metrics` missing / healthz subset mismatch.

- [ ] **Step 3: Write minimal implementation**

In `config.py`, after the `WEBAPP_*` block:

```python
# Метрики (SP4D): /metrics выключен по умолчанию; токен — опциональная защита.
METRICS_ENABLED = os.getenv("METRICS_ENABLED", "0") == "1"
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")
```

In `web/api.py`:
- add `import time`, `import metrics`, `PlainTextResponse` to the fastapi.responses import, `Request` to the fastapi import, and `get_system_counts` to the `database` import list.

- right after `app = FastAPI(...)` in `create_app`, add the middleware:

```python
    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        metrics.inc("http_requests_total", method=request.method,
                    status=str(response.status_code))
        metrics.set_gauge("http_last_duration_ms", duration_ms)
        log = logger.warning if response.status_code >= 500 else logger.info
        log("http method=%s path=%s status=%s duration_ms=%.1f",
            request.method, request.url.path, response.status_code, duration_ms)
        return response
```

- replace `healthz` with:

```python
    @app.get("/healthz")
    async def healthz():
        state = services.get("state")
        if state is not None and not state.get("bot_ok", True):
            return JSONResponse(status_code=503, content={"status": "bot down"})
        counts = {"families": None, "children": None, "measurements": None}
        try:
            counts = await _db(get_system_counts, config.DB_PATH)
        except Exception as e:
            logger.warning("healthz: не удалось посчитать БД: %s", e)
        return {
            "status": "ok",
            "uptime_seconds": round(metrics.uptime_seconds(), 1),
            "last_scheduler_tick": metrics.get_gauge("scheduler_last_tick_timestamp"),
            **counts,
        }
```

- add the metrics endpoint after `healthz`:

```python
    @app.get("/metrics")
    async def prometheus_metrics(request: Request):
        if not getattr(config, "METRICS_ENABLED", False):
            raise HTTPException(404, "Not found")
        token = getattr(config, "METRICS_TOKEN", "") or ""
        if token and request.headers.get("Authorization", "") != f"Bearer {token}":
            raise HTTPException(401, "Unauthorized")
        try:
            counts = await _db(get_system_counts, config.DB_PATH)
            metrics.set_gauge("families_count", counts["families"])
            metrics.set_gauge("children_count", counts["children"])
            metrics.set_gauge("measurements_count", counts["measurements"])
        except Exception as e:
            logger.warning("metrics: не удалось посчитать БД: %s", e)
        metrics.set_gauge("process_uptime_seconds", metrics.uptime_seconds())
        return PlainTextResponse(metrics.render_prometheus(),
                                 media_type="text/plain; version=0.0.4")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest test/test_webapp_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py web/api.py test/test_webapp_api.py
git commit -m "feat(web): healthz fields, metrics endpoint, request middleware (SP4D)"
```

---

### Task 4: Bot — инкременты замеров, достижений и планировщика

**Files:** Modify `bot.py`; Test `test/test_bot.py` (new `TestBotMetrics`).

**Interfaces:**
- Consumes (Task 1): `metrics.inc`, `metrics.set_gauge`, `metrics.get_counter`, `metrics.get_gauge`, `metrics.reset`.
- Produces: counters `measurements_saved_total`, `achievement_notifications_total`, `scheduler_ticks_total`, `reminders_sent_total{kind}`; gauge `scheduler_last_tick_timestamp`.

- [ ] **Step 1: Write the failing tests** (append to `test/test_bot.py`)

```python
class TestBotMetrics:
    def test_persist_measurement_counts(self):
        import asyncio, bot, metrics
        from unittest.mock import AsyncMock, MagicMock, patch
        metrics.reset()
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
             patch.object(bot, "_evaluate_and_notify", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(
                cb, state, 250, "morning",
                member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert metrics.get_counter("measurements_saved_total") == 1

    def test_scheduler_tick_counts(self):
        import asyncio, bot, metrics
        from datetime import datetime, timezone, timedelta
        from unittest.mock import AsyncMock, patch
        metrics.reset()
        fake_now = datetime(2026, 9, 12, 12, 0, 5, tzinfo=timezone(timedelta(hours=5)))

        async def fake_sleep(s):
            raise asyncio.CancelledError

        with patch.object(bot, "now_tz", return_value=fake_now), \
             patch.object(bot, "_tick_targets", new=AsyncMock(return_value=[])), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                asyncio.run(bot.scheduler_loop())
            except asyncio.CancelledError:
                pass
        assert metrics.get_counter("scheduler_ticks_total") == 1
        assert metrics.get_gauge("scheduler_last_tick_timestamp") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest test/test_bot.py::TestBotMetrics -q`
Expected: FAIL — counters stay 0.

- [ ] **Step 3: Write minimal implementation**

In `bot.py`: add `import time` (near `import os`) and `import metrics` (near `import gamification`).

- In `_persist_measurement`, right after `mid` is set (after the `if replaced_id / else` block):

```python
    metrics.inc("measurements_saved_total")
```

- In `_evaluate_and_notify`, after `new` is confirmed non-empty (right after the `if not new: return`):

```python
    metrics.inc("achievement_notifications_total", value=len(new))
```

- In `_maybe_ping_child`, after the successful `bot.send_message(...)` (before `mark_reminder_sent`):

```python
    metrics.inc("reminders_sent_total", kind="child")
```

- In `_escalate_parents`, after `if not delivered: ... return`, i.e. after delivery is confirmed (before `mark_reminder_sent`):

```python
    metrics.inc("reminders_sent_total", kind="escalation")
```

- In the weekly-report block of `scheduler_loop`, inside `if delivered:` (before/after `mark_reminder_sent`):

```python
                                metrics.inc("reminders_sent_total", kind="weekly")
```

- At the top of the `while True:` loop body in `scheduler_loop`, right after `now = now_tz()`:

```python
            metrics.inc("scheduler_ticks_total")
            metrics.set_gauge("scheduler_last_tick_timestamp", time.time())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest test/test_bot.py::TestBotMetrics test/test_bot.py::TestScheduler -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): metrics for measurements, achievements, scheduler (SP4D)"
```

---

### Task 5: CI-список, документация, полная регрессия

**Files:** `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1: CI** — add `metrics.py` to both commands:
  - pyflakes: `python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web/*.py`
  - compileall: `python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web`

- [ ] **Step 2: `PROJECT.md`** — метрики (`metrics.py`), расширенный `/healthz`, env-gated `/metrics`, `METRICS_ENABLED`/`METRICS_TOKEN`; обновить число тестов.

- [ ] **Step 3: `wiki.md`** — реестр метрик, точки сбора, healthz-поля, /metrics и токен, текстовые логи; обновить число тестов.

- [ ] **Step 4: todo-plan** — отметить `4.4` как выполненный (✅ SP4D); обновить тестовые счётчики (шапка + «Метрики успеха»).

- [ ] **Step 5: Run full verification**

Run:
```bash
venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`; зафиксировать фактическое число тестов. Дополнительно `CHILD_ID=0 PARENT_IDS=0,0 CHILD_NAME=Ребёнок venv/bin/python -m pytest test/ -q`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: SP4D metrics (SP4D)"
```

---

## Self-Review

**1. Spec coverage:**
- `metrics.py` реестр + Prometheus-рендер + escaping/валидация → Task 1 ✅
- `get_system_counts` (families/children/measurements) → Task 2 ✅
- `/healthz` расширен, 503 сохранён, `/metrics` env-gated + токен, middleware и access-лог → Task 3 ✅
- Инкременты замеров/достижений, тики планировщика, last tick, напоминания → Task 4 ✅
- CI-список/документация/счётчики/регрессия → Task 5 ✅
- Вне scope (JSON-логи, Sentry, гистограммы, multi-process) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; все шаги содержат код/точные команды.

**3. Type consistency:**
- `inc(name, value=1, **labels)`, `set_gauge(name, value, **labels)`, `get_counter(name, **labels) -> int`, `get_gauge(name, **labels)`, `uptime_seconds()`, `render_prometheus()`, `reset()` — Task 1; используются в Tasks 3/4.
- `get_system_counts(db_path) -> {"families","children","measurements"}` — Task 2; используется в Task 3.
- `config.METRICS_ENABLED: bool`, `config.METRICS_TOKEN: str` — Task 3.
- Метрики: `http_requests_total{method,status}`, `http_last_duration_ms`, `families_count`, `children_count`, `measurements_count`, `process_uptime_seconds`, `scheduler_ticks_total`, `scheduler_last_tick_timestamp`, `measurements_saved_total`, `achievement_notifications_total`, `reminders_sent_total{kind}` — Tasks 3/4, документ Task 5.

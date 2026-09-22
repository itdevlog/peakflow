# SP5D — Аналитика (зоны, heatmap недели, тренд): план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Добавить вкладку «Аналитика»: pie по зонам, heatmap среднего по дням недели и линейный тренд по 14 дням.

**Architecture:** Новый чистый модуль `analytics.py` (зоны/дни недели/МНК). Эндпоинт `GET /api/analytics` (require_user, scoped) считает зоны и дни недели по всем замерам, тренд — по последним 14 дням (дневные средние + МНК). Фронтенд: новая вкладка + canvas-pie, CSS-heatmap, мини-график тренда с линией регрессии.

**Tech Stack:** Python 3.11+, FastAPI, vanilla JS/canvas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-sp5d-analytics-design.md`

## Global Constraints

- Без новых зависимостей и библиотек/CDN.
- `analytics.py` — stdlib, не импортирует `bot.py`/`database.py`/`aiogram`/`matplotlib`; может брать хелперы из `report.py`.
- Данные строго по активному ребёнку/семье; без активного ребёнка → `404`.
- Пустые данные → «Нет данных», без исключений.
- Тесты: `venv/bin/python -m pytest test/ -q`; standalone `venv/bin/python -m pytest test/test_webapp_api.py -q`; без `.env`.
- Полная проверка: `venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web && echo ALL_GREEN`.
- Ветка: `sp5d-analytics`; коммиты `feat(...): ... (SP5D)`.

---

## File Structure

- `analytics.py` (новый) — зоны, дни недели, МНК.
- `web/api.py` — `GET /api/analytics`.
- `web/static/index.html` — вкладка + секция.
- `web/static/app.js` — `loadAnalytics`, `drawPie`, `drawHeatmap`, `drawTrend`.
- `web/static/style.css` — `.heat`/`.heat-cell`.
- `test/test_analytics.py` (новый), `test/test_webapp_api.py` — эндпоинт + статика.
- `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, todo-plan — CI-список и счётчики.

---

### Task 1: `analytics.py`

**Files:** Create `analytics.py`; Create `test/test_analytics.py`.

**Interfaces:**
- Produces: `zone_distribution(rows, target, zone_green=80, zone_yellow=60) -> dict`, `weekday_averages(rows, target, zone_green=80, zone_yellow=60) -> list`, `linear_fit(values) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing test**

```python
"""Тесты аналитики (SP5D)."""
import pytest


class TestZoneDistribution:
    def test_counts(self):
        from analytics import zone_distribution
        rows = [{"pef_value": 260}, {"pef_value": 240}, {"pef_value": 130}]
        assert zone_distribution(rows, 260) == {"green": 2, "yellow": 0, "red": 1}

    def test_empty(self):
        from analytics import zone_distribution
        assert zone_distribution([], 260) == {"green": 0, "yellow": 0, "red": 0}


class TestWeekdayAverages:
    def test_grouping(self):
        from analytics import weekday_averages
        # 2026-09-21 — понедельник, 2026-09-22 — вторник
        rows = [
            {"measured_at": "2026-09-21 08:00:00", "pef_value": 240},
            {"measured_at": "2026-09-21 20:00:00", "pef_value": 260},
            {"measured_at": "2026-09-22 08:00:00", "pef_value": 200},
        ]
        out = weekday_averages(rows, 260)
        assert len(out) == 7
        assert out[0] == {"avg": 250.0, "count": 2, "zone": "green"}
        assert out[1]["avg"] == 200.0
        assert out[1]["zone"] == "yellow"
        assert all(x is None for x in out[2:])

    def test_empty(self):
        from analytics import weekday_averages
        assert weekday_averages([], 260) == [None] * 7


class TestLinearFit:
    def test_increasing(self):
        from analytics import linear_fit
        slope, intercept = linear_fit([0, 1, 2, 3])
        assert slope == pytest.approx(1.0)
        assert intercept == pytest.approx(0.0)

    def test_decreasing(self):
        from analytics import linear_fit
        slope, _ = linear_fit([10, 8, 6, 4])
        assert slope == pytest.approx(-2.0)

    def test_constant(self):
        from analytics import linear_fit
        slope, intercept = linear_fit([250, 250, 250])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(250.0)

    def test_empty(self):
        from analytics import linear_fit
        assert linear_fit([]) == (0.0, 0.0)

    def test_single(self):
        from analytics import linear_fit
        assert linear_fit([250]) == (0.0, 250.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_analytics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics'`.

- [ ] **Step 3: Write minimal implementation**

Create `analytics.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_analytics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics.py test/test_analytics.py
git commit -m "feat(analytics): zones, weekday averages, linear fit (SP5D)"
```

---

### Task 2: `GET /api/analytics`

**Files:** Modify `web/api.py`; Test `test/test_webapp_api.py` (new `TestAnalyticsApi`).

**Interfaces:**
- Consumes: `analytics.zone_distribution/weekday_averages/linear_fit`; `report.daily_average_series`; `get_measurements_between`, `_date_list`, `_effective_target`.
- Produces: `GET /api/analytics` → `{zones, weekday, trend:{n,slope,intercept,per_week,daily}}`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`)

```python
class TestAnalyticsApi:
    def test_fields_and_values(self):
        _setup_db()
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(TEST_DB)
        for v in (260, 240):
            conn.execute(
                "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
                "measured_at, added_by, source) VALUES (1, ?, ?, 'morning', ?, ?, 'manual')",
                (CHILD_ID, v, now, CHILD_ID))
        conn.commit()
        conn.close()
        body = _client().get("/api/analytics", headers=_auth(CHILD_ID)).json()
        assert set(body) == {"zones", "weekday", "trend"}
        assert body["zones"]["green"] == 2
        assert len(body["weekday"]) == 7
        assert body["trend"]["n"] == 1
        assert body["trend"]["daily"][0]["avg"] == 250.0

    def test_no_child_404(self):
        _setup_db()
        from database import create_family_with_owner
        create_family_with_owner(TEST_DB, 999, "B")
        assert _client().get("/api/analytics", headers=_auth(999)).status_code == 404

    def test_isolation(self):
        _setup_db()
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
            "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/analytics", headers=_auth(999)).json()
        assert sum(body["zones"].values()) == 0
        assert body["trend"]["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestAnalyticsApi -q`
Expected: FAIL — route missing.

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`:
- add `from analytics import zone_distribution, weekday_averages, linear_fit`.
- add the endpoint after `/api/stats`:

```python
    @app.get("/api/analytics")
    async def analytics_endpoint(auth: dict = Depends(require_user)):
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        today = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).date()
        target = await _db(_effective_target, config, auth["family_id"])
        zg = getattr(config, "ZONE_GREEN", 80)
        zy = getattr(config, "ZONE_YELLOW", 60)

        rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                         "2000-01-01", today.isoformat(), auth["family_id"])
        since = (today - timedelta(days=13)).isoformat()
        recent = await _db(get_measurements_between, config.DB_PATH, child_id,
                           since, today.isoformat(), auth["family_id"])
        dates = _date_list(since, today.isoformat())
        series = daily_average_series(recent, dates)
        daily = [{"date": d, "avg": v} for d, v in zip(dates, series) if v is not None]
        slope, intercept = linear_fit([d["avg"] for d in daily])
        return {
            "zones": zone_distribution(rows, target, zg, zy),
            "weekday": weekday_averages(rows, target, zg, zy),
            "trend": {"n": len(daily), "slope": slope, "intercept": intercept,
                      "per_week": slope * 7, "daily": daily},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestAnalyticsApi -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test/test_webapp_api.py
git commit -m "feat(web): analytics endpoint (SP5D)"
```

---

### Task 3: Вкладка «Аналитика» в Mini App

**Files:** Modify `web/static/index.html`, `web/static/app.js`, `web/static/style.css`; Test `test/test_webapp_api.py`.

**Interfaces:**
- Consumes (Task 2): `/api/analytics`.
- Produces: `loadAnalytics()`, `drawPie(canvas, zones)`, `drawHeatmap(el, weekday)`, `drawTrend(canvas, trend)`.

- [ ] **Step 1: Write the failing test** (append to `TestAnalyticsApi` in `test/test_webapp_api.py`)

```python
    def test_ui_tokens(self):
        import pathlib
        html = pathlib.Path("web/static/index.html").read_text(encoding="utf-8")
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "screen-analytics" in html and 'data-screen="analytics"' in html
        for token in ("loadAnalytics", "drawPie", "drawHeatmap", "drawTrend", "/api/analytics"):
            assert token in js
```

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestAnalyticsApi::test_ui_tokens -q`
Expected: FAIL.

- [ ] **Step 2: Implement**

`web/static/index.html` — add the tab button after the «Статистика» tab:

```html
    <button class="tab" data-screen="analytics">Аналитика</button>
```

and the section after `#screen-stats`:

```html
    <section id="screen-analytics" class="screen" hidden></section>
```

`web/static/app.js` — in `switchTo`, add:
```javascript
    else if (name === "analytics") await loadAnalytics();
```

Add functions:

```javascript
async function loadAnalytics() {
  const data = await api("/api/analytics");
  const el = $("screen-analytics");
  el.innerHTML = `
    <div class="card">
      <div class="label">Зоны</div>
      <canvas id="zone-pie" height="160"></canvas>
      <div id="zone-legend" class="label"></div>
    </div>
    <div class="card">
      <div class="label">Средний ПСВ по дням недели</div>
      <div id="weekday-heat" class="heat"></div>
    </div>
    <div class="card">
      <div class="label">Тренд (14 дней)</div>
      <canvas id="trend-line" height="140"></canvas>
      <div id="trend-label" class="label"></div>
    </div>`;
  drawPie($("zone-pie"), data.zones);
  $("zone-legend").textContent =
    `🟢 ${data.zones.green} · 🟡 ${data.zones.yellow} · 🔴 ${data.zones.red}`;
  drawHeatmap($("weekday-heat"), data.weekday);
  drawTrend($("trend-line"), data.trend);
  const t = data.trend;
  $("trend-label").textContent = t.n
    ? `${t.per_week >= 0 ? "↗" : "↘"} ${t.per_week >= 0 ? "+" : ""}${t.per_week.toFixed(1)} л/мин/нед (n=${t.n})`
    : "Нет данных";
}

function drawPie(canvas, zones) {
  const total = zones.green + zones.yellow + zones.red;
  const ctx = canvas.getContext("2d");
  const w = canvas.width = canvas.clientWidth || 200;
  const h = canvas.height = 160;
  ctx.clearRect(0, 0, w, h);
  if (!total) { ctx.fillStyle = "#777"; ctx.font = "12px sans-serif"; ctx.fillText("Нет данных", 8, 80); return; }
  const cx = w / 2, cy = h / 2, r = 60;
  let start = -Math.PI / 2;
  [["green", "#2fb344"], ["yellow", "#e8a600"], ["red", "#e5484d"]].forEach(([k, color]) => {
    const frac = zones[k] / total;
    if (!frac) return;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, start + frac * Math.PI * 2);
    ctx.closePath(); ctx.fillStyle = color; ctx.fill();
    start += frac * Math.PI * 2;
  });
}

function drawHeatmap(el, weekday) {
  const names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const colors = { green: "#2fb344", yellow: "#e8a600", red: "#e5484d" };
  el.innerHTML = weekday.map((d, i) =>
    `<div class="heat-cell" style="background:${d ? (colors[d.zone] || "#999") : "var(--card)"}">
       <div>${names[i]}</div><div>${d ? Math.round(d.avg) : "—"}</div></div>`).join("");
}

function drawTrend(canvas, trend) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width = canvas.clientWidth || 200;
  const h = canvas.height = 140;
  ctx.clearRect(0, 0, w, h);
  const daily = trend.daily || [];
  if (daily.length < 2) {
    ctx.fillStyle = "#777"; ctx.font = "12px sans-serif";
    ctx.fillText("Недостаточно данных", 8, h / 2); return;
  }
  const vals = daily.map((d) => d.avg);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = 14, plotH = h - 2 * pad;
  const x = (i) => pad + (i / (daily.length - 1)) * (w - 2 * pad);
  const y = (v) => pad + (hi === lo ? plotH / 2 : (1 - (v - lo) / (hi - lo)) * plotH);
  ctx.strokeStyle = "#2ea6ff"; ctx.lineWidth = 1.8; ctx.beginPath();
  daily.forEach((d, i) => { i ? ctx.lineTo(x(i), y(d.avg)) : ctx.moveTo(x(i), y(d.avg)); });
  ctx.stroke();
  ctx.strokeStyle = "#9aa0a6"; ctx.setLineDash([5, 4]); ctx.beginPath();
  daily.forEach((_, i) => {
    const v = trend.slope * i + trend.intercept;
    i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v));
  });
  ctx.stroke(); ctx.setLineDash([]);
}
```

`web/static/style.css` — append:

```css
.heat { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.heat-cell { border-radius: 6px; padding: 6px 2px; text-align: center;
  color: #fff; font-size: 11px; }
```

- [ ] **Step 3: Run tests**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestAnalyticsApi test/test_webapp_api.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html web/static/app.js web/static/style.css test/test_webapp_api.py
git commit -m "feat(web): analytics tab with pie, heatmap and trend (SP5D)"
```

---

### Task 4: CI-список, документация, полная регрессия

**Files:** `.github/workflows/ci.yml`, `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1: CI** — add `analytics.py` to BOTH commands:
  - pyflakes: `python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web/*.py`
  - compileall: `python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web`

- [ ] **Step 2: `PROJECT.md`** — вкладка «Аналитика», `/api/analytics`, `analytics.py`; обновить число тестов.
- [ ] **Step 3: `wiki.md`** — то же по существу; обновить число тестов.
- [ ] **Step 4: todo-plan** — отметить `5.4` как выполненный (✅ SP5D); обновить счётчики.
- [ ] **Step 5: Run full verification**

Run:
```bash
venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py analytics.py metrics.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`; зафиксировать фактическое число тестов. Дополнительно CI-подобно: `CHILD_ID=0 PARENT_IDS=0,0 CHILD_NAME=Ребёнок venv/bin/python -m pytest test/ -q`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: SP5D analytics (SP5D)"
```

---

## Self-Review

**1. Spec coverage:**
- `analytics.py` (зоны/дни недели/МНК) → Task 1 ✅
- `/api/analytics` (zones/weekday/trend, 404, изоляция) → Task 2 ✅
- Вкладка «Аналитика» (pie/heatmap/тренд) → Task 3 ✅
- CI-список/документация/счётчики/регрессия → Task 4 ✅
- Вне scope (календарная heatmap, прогноз-таблица, SP5E) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; все шаги содержат код/точные команды. `test_ui_tokens` красный до Task 3 — явно оговорено.

**3. Type consistency:**
- `zone_distribution`/`weekday_averages`/`linear_fit` — Task 1; используются в Task 2.
- `/api/analytics` поля `zones/weekday/trend{n,slope,intercept,per_week,daily}` — Task 2; потребляет Task 3.
- `loadAnalytics`/`drawPie`/`drawHeatmap`/`drawTrend` — Task 3.

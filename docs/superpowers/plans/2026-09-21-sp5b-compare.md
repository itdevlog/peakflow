# SP5B — Сравнение периодов: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Показать на одном графике текущую неделю/месяц против предыдущей, свернув замеры в среднее за день.

**Architecture:** Чистый хелпер `report.daily_average_series` сворачивает строки по дням. Новый эндпоинт `GET /api/chart/compare?period=week|month` считает границы через `report_pdf.period_bounds`, тянет два периода `get_measurements_between` и возвращает выровненные ряды. Фронтенд: кнопка «Сравнить» на экране графика переключает режим и рисует `drawCompare` (два ряда по дням) на том же canvas.

**Tech Stack:** Python 3.11+, FastAPI, vanilla JS/canvas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-sp5b-compare-design.md`

## Global Constraints

- Без новых зависимостей и библиотек/CDN.
- Данные строго по активному ребёнку и семье (`auth`), изоляция сохраняется.
- Без активного ребёнка → `404`; невалидный период → `422`.
- Сравнение не ломает обычный график (SP5A) — отдельный режим.
- Тесты: `venv/bin/python -m pytest test/ -q`; standalone `venv/bin/python -m pytest test/test_webapp_api.py -q`; без `.env`.
- Полная проверка: `venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web && echo ALL_GREEN`.
- Ветка: `sp5b-compare`; коммиты `feat(...): ... (SP5B)`.

---

## File Structure

- `report.py` — `daily_average_series`.
- `web/api.py` — `GET /api/chart/compare`, `_date_list` helper.
- `web/static/index.html` — кнопка «Сравнить».
- `web/static/app.js` — `state.chartCompare`/`compareData`, `drawCompare`, тултип сравнения, wiring.
- `test/test_webapp_api.py` — API-тесты + статические проверки JS.
- `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md` — счётчики/статус.

---

### Task 1: `report.daily_average_series`

**Files:** Modify `report.py`; Test `test/test_bot.py` (new `TestDailyAverageSeries`) or `test/test_report_pdf.py`.

**Interfaces:**
- Produces: `daily_average_series(rows: list[dict], dates: list[str]) -> list`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`, class `TestDailyAverageSeries`)

```python
class TestDailyAverageSeries:
    def test_average_per_day(self):
        from report import daily_average_series
        rows = [
            {"measured_at": "2026-09-21 08:00:00", "pef_value": 250},
            {"measured_at": "2026-09-21 20:00:00", "pef_value": 270},
            {"measured_at": "2026-09-22 08:00:00", "pef_value": 260},
        ]
        assert daily_average_series(rows, ["2026-09-21", "2026-09-22", "2026-09-23"]) == [260.0, 260.0, None]

    def test_empty_rows(self):
        from report import daily_average_series
        assert daily_average_series([], ["2026-09-21"]) == [None]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestDailyAverageSeries -q`
Expected: FAIL — `ImportError: cannot import name 'daily_average_series'`.

- [ ] **Step 3: Write minimal implementation**

In `report.py` append:

```python
def daily_average_series(rows: list, dates: list) -> list:
    """Average PEF per ISO date in ``dates`` (None when a day has no rows)."""
    buckets = {}
    for r in rows:
        key = str(r["measured_at"])[:10]
        buckets.setdefault(key, []).append(r["pef_value"])
    out = []
    for d in dates:
        vals = buckets.get(d)
        out.append(sum(vals) / len(vals) if vals else None)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestDailyAverageSeries -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report.py test/test_webapp_api.py
git commit -m "feat(report): daily average series (SP5B)"
```

---

### Task 2: `GET /api/chart/compare`

**Files:** Modify `web/api.py`; Test `test/test_webapp_api.py` (new `TestChartCompare`).

**Interfaces:**
- Consumes (Task 1): `report.daily_average_series`; `report_pdf.period_bounds`/`period_label`; `get_measurements_between`.
- Produces: `GET /api/chart/compare?period=week|month` → `{period, labels, current, previous, target_pef, zones, title}`; module helper `_date_list(start_iso, end_iso)`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`)

```python
class TestChartCompare:
    def _today(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date()

    def _rand(self, day, pef):
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, ?, ?, 'morning', ?, ?, 'manual')",
            (CHILD_ID, pef, f"{day} 08:00:00", CHILD_ID))
        conn.commit()
        conn.close()

    def test_week_compare(self):
        _setup_db()
        import report_pdf
        today = self._today()
        cur_start, cur_end = report_pdf.period_bounds("week", today)
        from datetime import date, timedelta
        prev_day = (date.fromisoformat(cur_start) - timedelta(days=1)).isoformat()
        next_day = (date.fromisoformat(cur_start) + timedelta(days=1)).isoformat()
        self._rand(cur_start, 240)
        self._rand(next_day, 260)
        self._rand(prev_day, 200)
        body = _client().get("/api/chart/compare?period=week", headers=_auth(CHILD_ID)).json()
        assert body["period"] == "week"
        assert body["labels"] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        assert len(body["current"]) == 7 and len(body["previous"]) == 7
        assert body["current"][0] == 240.0
        assert body["current"][1] == 260.0
        assert body["current"][2] is None
        assert body["previous"][6] == 200.0  # prev Sunday

    def test_month_alignment(self):
        _setup_db()
        body = _client().get("/api/chart/compare?period=month", headers=_auth(CHILD_ID)).json()
        assert body["period"] == "month"
        assert len(body["labels"]) == len(body["current"]) == len(body["previous"])
        assert body["labels"][0] == "1"
        assert set(body) == {"period", "labels", "current", "previous",
                             "target_pef", "zones", "title"}

    def test_compare_bad_period(self):
        _setup_db()
        assert _client().get("/api/chart/compare?period=year",
                             headers=_auth(CHILD_ID)).status_code == 422

    def test_compare_isolation(self):
        _setup_db()
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        # family #1 row reusing family #2's child id must not leak
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "INSERT INTO measurements (family_id, child_id, pef_value, time_of_day, "
            "measured_at, added_by, source) VALUES (1, 700, 250, 'morning', "
            "'2026-09-01 08:00:00', 700, 'manual')")
        conn.commit()
        conn.close()
        body = _client().get("/api/chart/compare?period=month", headers=_auth(999)).json()
        assert all(v is None for v in body["current"])
        assert all(v is None for v in body["previous"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestChartCompare -q`
Expected: FAIL — route missing (404).

- [ ] **Step 3: Write minimal implementation**

In `web/api.py`:
- extend `from datetime import datetime, timedelta, timezone` with `date`.
- extend the `from report import ...` line with `daily_average_series`.
- add module helper near `_month_bounds`:

```python
def _date_list(start_iso: str, end_iso: str) -> list:
    d, e = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out
```

- add the endpoint after `/api/chart`:

```python
    @app.get("/api/chart/compare")
    async def chart_compare(period: str = "week", auth: dict = Depends(require_user)):
        if period not in ("week", "month"):
            raise HTTPException(422, "Неверный период")
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        today = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).date()
        cur_start, cur_end = report_pdf.period_bounds(period, today)
        prev_ref = date.fromisoformat(cur_start) - timedelta(days=1)
        prev_start, prev_end = report_pdf.period_bounds(period, prev_ref)
        cur_rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                             cur_start, cur_end, auth["family_id"])
        prev_rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                              prev_start, prev_end, auth["family_id"])
        current = daily_average_series(cur_rows, _date_list(cur_start, cur_end))
        previous = daily_average_series(prev_rows, _date_list(prev_start, prev_end))
        length = max(len(current), len(previous))
        current += [None] * (length - len(current))
        previous += [None] * (length - len(previous))
        labels = (["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][:length]
                  if period == "week" else [str(i + 1) for i in range(length)])
        return {
            "period": period,
            "labels": labels,
            "current": current,
            "previous": previous,
            "target_pef": await _db(_effective_target, config, auth["family_id"]),
            "zones": {"green": getattr(config, "ZONE_GREEN", 80),
                      "yellow": getattr(config, "ZONE_YELLOW", 60)},
            "title": (f"{report_pdf.period_label(period, today)} vs "
                      f"{report_pdf.period_label(period, prev_ref)}"),
        }
```

> `_effective_target` is a module-level function in `web/api.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestChartCompare -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test/test_webapp_api.py
git commit -m "feat(web): period comparison endpoint (SP5B)"
```

---

### Task 3: Фронтенд — режим сравнения

**Files:** Modify `web/static/index.html`, `web/static/app.js`; Test `test/test_webapp_api.py`.

**Interfaces:**
- Consumes (Task 2): `/api/chart/compare`.
- Produces: `state.chartCompare`, `state.compareData`, `drawCompare(data)`, кнопка `#chart-compare`.

- [ ] **Step 1: Write the failing test** (append to `TestMiniAppChartUi`)

```python
    def test_compare_ui(self):
        js = self._js()
        html = self._html()
        assert "chart-compare" in html and "chart-compare" in js
        assert "chartCompare" in js
        assert "drawCompare" in js and "/api/chart/compare" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestMiniAppChartUi::test_compare_ui -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`web/static/index.html` — inside `#chart-controls`, add a third `.seg`:

```html
        <div class="seg">
          <button class="mini" id="chart-compare">Сравнить</button>
        </div>
```

`web/static/app.js`:
- extend `state`: add `chartCompare: false, compareData: null`.
- add `drawCompare(data)` (canvas):

```javascript
function drawCompare(data) {
  const { c, ctx, cssW, cssH } = initChart();
  ctx.clearRect(0, 0, cssW, cssH);
  const padL = 38, padR = 12, padT = 12, padB = 24;
  const plotW = cssW - padL - padR, plotH = cssH - padT - padB;
  const series = data.current.concat(data.previous).filter((v) => v != null);
  const target = data.target_pef || 0;
  const all = series.concat(target ? [target] : []);
  let yMax = all.length ? Math.max(...all) : 300;
  let yMin = all.length ? Math.min(...all) : 0;
  if (yMax === yMin) { yMax += 20; yMin = Math.max(0, yMin - 20); }
  const margin = Math.round((yMax - yMin) * 0.15) || 10;
  yMax += margin; yMin = Math.max(0, yMin - margin);
  const yToPx = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const n = data.labels.length;
  const xToPx = (i) => (n <= 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW);

  ctx.strokeStyle = "#999"; ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, cssH - padB); ctx.lineTo(cssW - padR, cssH - padB);
  ctx.stroke();
  ctx.fillStyle = "#777"; ctx.font = "10px sans-serif";
  for (let k = 0; k <= 4; k++) {
    const v = yMin + ((yMax - yMin) * k) / 4;
    ctx.fillText(String(Math.round(v)), 4, yToPx(v) + 3);
  }
  if (target) {
    ctx.strokeStyle = "#2ea6ff"; ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(padL, yToPx(target)); ctx.lineTo(cssW - padR, yToPx(target)); ctx.stroke();
    ctx.setLineDash([]);
  }
  const drawSeries = (arr, color, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.setLineDash(dash); ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = xToPx(i), y = yToPx(v);
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    });
    ctx.stroke(); ctx.setLineDash([]);
    arr.forEach((v, i) => {
      if (v == null) return;
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(xToPx(i), yToPx(v), 3, 0, Math.PI * 2); ctx.fill();
    });
  };
  drawSeries(data.previous, "#9aa0a6", [5, 4]);
  drawSeries(data.current, "#2ea6ff", []);
  ctx.fillStyle = "#777";
  data.labels.forEach((lab, i) => {
    if (n <= 14 || i % 2 === 0) ctx.fillText(lab, xToPx(i) - 5, cssH - padB + 14);
  });
  c._points = data.labels.map((lab, i) => ({ x: xToPx(i), i, label: lab }));
}

async function loadCompare() {
  const period = state.chartRange === "week" ? "week" : "month";
  const data = await api(`/api/chart/compare?period=${period}`);
  state.compareData = data;
  $("chart-title").textContent = data.title;
  $("chart-tip").hidden = true;
  $("chart").style.display = "block";
  drawCompare(data);
}

function toggleCompare() {
  state.chartCompare = !state.chartCompare;
  const on = state.chartCompare;
  document.querySelectorAll("[data-chart-type]").forEach((b) => { b.style.display = on ? "none" : ""; });
  $("chart-compare").classList.toggle("active", on);
  if (on) { loadCompare().catch((e) => showError(e.message)); }
  else { redrawChart(); }
}
```

- wire the button and update the range handler so it targets compare mode when active. Replace the range-button wiring with:

```javascript
document.querySelectorAll("[data-chart-range]").forEach((b) =>
  b.onclick = () => {
    state.chartRange = b.dataset.chartRange;
    if (state.chartCompare) { loadCompare().catch((e) => showError(e.message)); syncChartControls(); }
    else { redrawChart(); }
  });
$("chart-compare").onclick = toggleCompare;
```

- update `chartClick` to a full replacement that handles both modes:

```javascript
function chartClick(ev) {
  const c = $("chart");
  const rect = c.getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  const tip = $("chart-tip");
  const pts = c._points || [];

  if (state.chartCompare && state.compareData) {
    let best = null, bestD = 1e9;
    for (const q of pts) { const d = Math.abs(q.x - x); if (d < bestD) { bestD = d; best = q; } }
    if (!best) { tip.hidden = true; return; }
    const d = state.compareData;
    const fmt = (v) => (v == null ? "—" : Math.round(v));
    tip.hidden = false;
    tip.textContent = `День ${best.label} · текущий: ${fmt(d.current[best.i])} · прошлый: ${fmt(d.previous[best.i])}`;
    return;
  }

  const data = state.chartData || {};
  let best = null, bestD = 1e9;
  for (const q of pts) {
    const d = (q.x - x) ** 2 + (q.y - y) ** 2;
    if (d < bestD) { bestD = d; best = q; }
  }
  if (best && bestD < 900) {
    const p = best.p;
    const z = pointZone(p, data);
    let text = `${p.date} · ${todLabel(p.tod)} · ${p.pef} л/мин`;
    if (z.pct != null) text += ` · ${z.pct}% ${z.emoji}`;
    if (p.note) text += `\n📝 ${p.note}`;
    tip.hidden = false;
    tip.textContent = text;
  } else { tip.hidden = true; }
}
```

- in `toggleCompare`, restore the month title when leaving compare mode:

```javascript
function toggleCompare() {
  state.chartCompare = !state.chartCompare;
  const on = state.chartCompare;
  document.querySelectorAll("[data-chart-type]").forEach((b) => { b.style.display = on ? "none" : ""; });
  $("chart-compare").classList.toggle("active", on);
  if (on) {
    loadCompare().catch((e) => showError(e.message));
  } else {
    if (state.chartData && state.chartData.title) $("chart-title").textContent = state.chartData.title;
    redrawChart();
  }
}
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest test/test_webapp_api.py::TestMiniAppChartUi test/test_webapp_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/app.js test/test_webapp_api.py
git commit -m "feat(web): period comparison mode in Mini App (SP5B)"
```

---

### Task 4: Документация, счётчики, полная регрессия

**Files:** `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1: `PROJECT.md`** — сравнение периодов (`/api/chart/compare`, режим «Сравнить», среднее за день); обновить число тестов.
- [ ] **Step 2: `wiki.md`** — то же по существу; обновить число тестов.
- [ ] **Step 3: todo-plan** — отметить `5.2` как выполненный (✅ SP5B); обновить счётчики.
- [ ] **Step 4: Run full verification**

Run:
```bash
venv/bin/python -m pytest test/ -q && venv/bin/python -m pytest test/test_webapp_api.py -q && venv/bin/python -m pyflakes bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web/*.py scripts/*.py && venv/bin/python -m compileall -q bot.py database.py config.py report.py report_pdf.py gamification.py metrics.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`; зафиксировать фактическое число тестов. Дополнительно CI-подобно: `CHILD_ID=0 PARENT_IDS=0,0 CHILD_NAME=Ребёнок venv/bin/python -m pytest test/ -q`.

- [ ] **Step 5: Commit**

```bash
git add PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: SP5B period comparison (SP5B)"
```

---

## Self-Review

**1. Spec coverage:**
- Текущий vs предыдущий период (week/month), выравнивание → Tasks 2 ✅
- Среднее за день → Task 1 ✅
- Два ряда по дням, линия нормы, подписи, тултип → Task 3 ✅
- 422/404, изоляция, пустые данные → Tasks 2/3 ✅
- Документация/счётчики/регрессия → Task 4 ✅
- Вне scope (произвольные периоды, квартал, утро/вечер, экспорт) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; все шаги содержат код/точные команды.

**3. Type consistency:**
- `daily_average_series(rows, dates) -> list` — Task 1; используется в Task 2.
- `_date_list(start_iso, end_iso) -> list` — Task 2.
- `GET /api/chart/compare` поля `period/labels/current/previous/target_pef/zones/title` — Task 2; потребляет Task 3.
- `state.chartCompare`/`state.compareData`, `drawCompare(data)`, `loadCompare()`, `toggleCompare()` — Task 3.

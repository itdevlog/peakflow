# SP4A — PDF-отчёты врачу: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Дать родителю PDF-отчёт для врача по активному ребёнку за неделю/месяц/квартал — из бота и Mini App.

**Architecture:** Новый чистый модуль `report_pdf.py` (stdlib + matplotlib, без bot/database/aiogram) считает границы периода, статистику за период и собирает A4-PDF через `PdfPages`. Тело отрисовки графика выносится из `bot._render_chart_png` в `report_pdf.draw_chart` (жизненный цикл фигуры PNG остаётся в bot). Бот добавляет пункт «📄 Отчёт врачу» в Настройки; веб добавляет `GET /api/report/pdf`. Данные — `get_measurements_between` строго по `(family_id, child_id)`.

**Tech Stack:** Python 3.11+, matplotlib (уже в `requirements.txt`), aiogram 3, FastAPI, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-sp4a-pdf-reports-design.md`

## Global Constraints

- Новых зависимостей нет; `matplotlib` уже есть. `report_pdf.py` **не** импортирует `bot.py`, `database.py`, `aiogram` (можно `report.py`).
- Изоляция: любой доступ к данным — с `family_id` и активным `child_id`.
- PDF только в памяти; никаких выгрузок целой БД.
- `member=None` во всех хендлерах → прежнее поведение семьи №1 из `.env`.
- Доступ к отчёту — только родители (бот и веб).
- Пустой период → отказ (alert/404), PDF не генерируется.
- A4-вёрстка ч/б-совместима: зона дублируется словом, emoji в PDF не используются.
- Ветка подпроекта: `sp4a-pdf-reports`; коммиты `feat(...): ... (SP4A)`.
- Проверка: `python -m pytest test/ -q`; standalone `python -m pytest test/test_webapp_api.py -q`; без `.env`.
- Полная проверка: `python -m pytest test/ -q && python -m pyflakes bot.py database.py config.py report.py report_pdf.py web/*.py scripts/*.py && python -m compileall -q bot.py database.py config.py report.py report_pdf.py web && echo ALL_GREEN`.

---

## File Structure

- `report_pdf.py` — новый: периоды, статистика, `draw_chart`, `build_pdf`, `RENDER_LOCK`.
- `bot.py` — импорт `report_pdf`; вынос тела графика; `_build_report_pdf_async`; `kb_report_periods`; кнопка в `kb_settings`; `cb_report`/`cb_report_period`.
- `web/api.py` — `GET /api/report/pdf`.
- `web/static/app.js` — карточка «📄 Отчёт врачу» на экране Настройки.
- `test/test_report_pdf.py` — новый.
- `test/test_bot.py`, `test/test_webapp_api.py` — новые тесты.
- `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md` — счётчики/статус.

---

### Task 1: `report_pdf.py` — периоды

**Files:**
- Create: `report_pdf.py`
- Test: `test/test_report_pdf.py`

**Interfaces:**
- Produces: `PERIODS: tuple[str, ...]`, `period_bounds(period: str, today: date) -> tuple[str, str]`, `period_label(period: str, today: date) -> str`.

- [ ] **Step 1: Write the failing test**

```python
"""Тесты PDF-отчётов (SP4A)."""
from datetime import date

import pytest


class TestPeriodBounds:
    def test_week_monday_to_sunday(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 9, 21)) == ("2026-09-21", "2026-09-27")

    def test_week_midweek_same_bounds(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 9, 23)) == ("2026-09-21", "2026-09-27")

    def test_week_crosses_year(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 12, 31)) == ("2026-12-28", "2027-01-03")

    def test_month(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2026, 9, 21)) == ("2026-09-01", "2026-09-30")

    def test_month_february_leap(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2024, 2, 10)) == ("2024-02-01", "2024-02-29")

    def test_month_december(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2026, 12, 15)) == ("2026-12-01", "2026-12-31")

    @pytest.mark.parametrize("month,expected", [
        (1, ("2026-01-01", "2026-03-31")),
        (4, ("2026-04-01", "2026-06-30")),
        (9, ("2026-07-01", "2026-09-30")),
        (10, ("2026-10-01", "2026-12-31")),
    ])
    def test_quarter(self, month, expected):
        from report_pdf import period_bounds
        assert period_bounds("quarter", date(2026, month, 15)) == expected

    def test_invalid_period(self):
        from report_pdf import period_bounds
        with pytest.raises(ValueError):
            period_bounds("year", date(2026, 9, 21))


class TestPeriodLabel:
    def test_week_same_month(self):
        from report_pdf import period_label
        assert period_label("week", date(2026, 9, 21)) == "21–27.09.2026"

    def test_week_crosses_month(self):
        from report_pdf import period_label
        assert period_label("week", date(2026, 9, 30)) == "28.09–04.10.2026"

    def test_month(self):
        from report_pdf import period_label
        assert period_label("month", date(2026, 9, 21)) == "Сентябрь 2026"

    def test_quarter(self):
        from report_pdf import period_label
        assert period_label("quarter", date(2026, 9, 21)) == "III квартал 2026"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_report_pdf.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'report_pdf'`.

- [ ] **Step 3: Write minimal implementation**

Create `report_pdf.py`:

```python
"""PDF-отчёты врачу (SP4A).

Чистый модуль: stdlib + matplotlib. Не импортирует bot.py/database.py/aiogram.
Разрешено использовать чистые хелперы из report.py.
"""
from datetime import date, timedelta

from report import month_title

PERIODS = ("week", "month", "quarter")

_ROMAN = ("I", "II", "III", "IV")


def period_bounds(period: str, today: date) -> tuple[str, str]:
    """ISO-границы [date_from, date_to] для периода, включительно."""
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    elif period == "quarter":
        first_month = ((today.month - 1) // 3) * 3 + 1
        start = date(today.year, first_month, 1)
        last_month = first_month + 2
        if last_month == 12:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, last_month + 1, 1) - timedelta(days=1)
    else:
        raise ValueError(f"Неизвестный период: {period}")
    return start.isoformat(), end.isoformat()


def period_label(period: str, today: date) -> str:
    """Человекочитаемая подпись периода для шапки PDF."""
    if period == "week":
        start_s, end_s = period_bounds(period, today)
        start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
        if start.year != end.year:
            return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"
        if start.month != end.month:
            return f"{start:%d.%m}–{end:%d.%m.%Y}"
        return f"{start:%d}–{end:%d.%m.%Y}"
    if period == "month":
        return month_title(today.year, today.month)
    if period == "quarter":
        return f"{_ROMAN[(today.month - 1) // 3]} квартал {today.year}"
    raise ValueError(f"Неизвестный период: {period}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_report_pdf.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report_pdf.py test/test_report_pdf.py
git commit -m "feat(report_pdf): period bounds and labels (SP4A)"
```

---

### Task 2: `report_pdf.py` — статистика за период

**Files:**
- Modify: `report_pdf.py`
- Test: `test/test_report_pdf.py`

**Interfaces:**
- Consumes: `report.pef_zone`.
- Produces: `compute_stats(rows: list[dict], target: int, zone_green: int = 80, zone_yellow: int = 60) -> dict` с ключами `total, avg, min, max, morning_avg, evening_avg, zones{green,yellow,red}`.

- [ ] **Step 1: Write the failing test** (append to `test/test_report_pdf.py`)

```python
class TestComputeStats:
    def _rows(self):
        return [
            {"pef_value": 260, "time_of_day": "morning"},
            {"pef_value": 240, "time_of_day": "evening"},
            {"pef_value": 130, "time_of_day": "morning"},
        ]

    def test_basic(self):
        from report_pdf import compute_stats
        s = compute_stats(self._rows(), target=260)
        assert s["total"] == 3
        assert s["min"] == 130 and s["max"] == 260
        assert s["avg"] == pytest.approx((260 + 240 + 130) / 3)
        assert s["morning_avg"] == pytest.approx(195)
        assert s["evening_avg"] == pytest.approx(240)
        # target 260, zone_green 80 -> >=208, zone_yellow 60 -> >=156
        assert s["zones"] == {"green": 2, "yellow": 0, "red": 1}

    def test_empty(self):
        from report_pdf import compute_stats
        s = compute_stats([], target=260)
        assert s["total"] == 0
        assert s["avg"] == 0
        assert s["morning_avg"] is None
        assert s["evening_avg"] is None
        assert s["zones"] == {"green": 0, "yellow": 0, "red": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_report_pdf.py::TestComputeStats -q`
Expected: FAIL — `ImportError: cannot import name 'compute_stats'`.

- [ ] **Step 3: Write minimal implementation** (add to `report_pdf.py`)

Replace the import line `from report import month_title` with:

```python
from report import month_title, pef_zone
```

Append:

```python
_ZONE_KEYS = {"Зелёная": "green", "Жёлтая": "yellow", "Красная": "red"}


def compute_stats(rows: list[dict], target: int, zone_green: int = 80,
                  zone_yellow: int = 60) -> dict:
    """Статистика за период из уже выбранных rows (не all-time get_stats)."""
    values = [r["pef_value"] for r in rows]
    morning = [r["pef_value"] for r in rows if r["time_of_day"] == "morning"]
    evening = [r["pef_value"] for r in rows if r["time_of_day"] == "evening"]
    zones = {"green": 0, "yellow": 0, "red": 0}
    for r in rows:
        key = _ZONE_KEYS.get(pef_zone(r["pef_value"], target, zone_green, zone_yellow)[1])
        if key:
            zones[key] += 1
    return {
        "total": len(values),
        "avg": sum(values) / len(values) if values else 0,
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "morning_avg": sum(morning) / len(morning) if morning else None,
        "evening_avg": sum(evening) / len(evening) if evening else None,
        "zones": zones,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_report_pdf.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report_pdf.py test/test_report_pdf.py
git commit -m "feat(report_pdf): period stats (SP4A)"
```

---

### Task 3: `draw_chart` + `RENDER_LOCK`; рефактор `bot._render_chart_png`

**Files:**
- Modify: `report_pdf.py`, `bot.py`
- Test: `test/test_report_pdf.py`, `test/test_bot.py`

**Interfaces:**
- Produces: `report_pdf.RENDER_LOCK: threading.Lock`, `report_pdf.draw_chart(ax, rows, target, zone_green, zone_yellow, title=None, text_labels=False) -> None`.
- Preserves: `bot._render_chart_png(rows, target, title) -> bytes`, `bot._render_chart_png_async(rows, target, title) -> bytes`.

- [ ] **Step 1: Write the failing test** (append to `test/test_report_pdf.py`)

```python
class TestDrawChart:
    def _rows(self):
        return [
            {"pef_value": 240, "time_of_day": "morning", "measured_at": "2026-09-01 08:00:00"},
            {"pef_value": 260, "time_of_day": "evening", "measured_at": "2026-09-02 20:00:00"},
        ]

    def test_emoji_labels_by_default(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from report_pdf import draw_chart
        fig, ax = plt.subplots()
        try:
            draw_chart(ax, self._rows(), 260, 80, 60, title="T")
            texts = [t.get_text() for t in ax.texts]
        finally:
            plt.close(fig)
        assert any("🏆" in t for t in texts)

    def test_text_labels_for_pdf(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from report_pdf import draw_chart
        fig, ax = plt.subplots()
        try:
            draw_chart(ax, self._rows(), 260, 80, 60, text_labels=True)
            texts = [t.get_text() for t in ax.texts]
        finally:
            plt.close(fig)
        assert texts and all("🏆" not in t and "⚠" not in t for t in texts)
        assert any("Лучший" in t for t in texts)
```

Also add one regression test to `test/test_bot.py::TestReportPdf` (created in Task 5) — skip; existing `test_render_chart_png` and `test_figure_closed_on_render_error` already cover the refactor.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_report_pdf.py::TestDrawChart -q`
Expected: FAIL — `ImportError: cannot import name 'draw_chart'`.

- [ ] **Step 3: Write minimal implementation**

Add to top of `report_pdf.py` after the existing imports:

```python
import threading

RENDER_LOCK = threading.Lock()
```

Append:

```python
def draw_chart(ax, rows, target, zone_green, zone_yellow,
               title: str | None = None, text_labels: bool = False) -> None:
    """Нарисовать график ПСВ на переданном ax (без создания/закрытия фигуры)."""
    from datetime import datetime

    import matplotlib.dates as mdates

    dates = [datetime.strptime(r["measured_at"], "%Y-%m-%d %H:%M:%S") for r in rows]
    values = [r["pef_value"] for r in rows]
    morning_d = [dates[i] for i, r in enumerate(rows) if r["time_of_day"] == "morning"]
    morning_v = [values[i] for i, r in enumerate(rows) if r["time_of_day"] == "morning"]
    evening_d = [dates[i] for i, r in enumerate(rows) if r["time_of_day"] == "evening"]
    evening_v = [values[i] for i, r in enumerate(rows) if r["time_of_day"] == "evening"]

    ax.plot(dates, values, marker="o", linewidth=2, label="ПСВ",
            color="#2196F3", markersize=4, zorder=3)
    if morning_d:
        ax.scatter(morning_d, morning_v, color="#FF9800", label="Утро",
                   zorder=5, s=80, edgecolors="white", linewidth=1.5)
    if evening_d:
        ax.scatter(evening_d, evening_v, color="#9C27B0", label="Вечер",
                   zorder=5, s=80, edgecolors="white", linewidth=1.5)

    best_idx = values.index(max(values))
    worst_idx = values.index(min(values))
    if text_labels:
        best_text, worst_text = f"Лучший {max(values)}", f"Худший {min(values)}"
    else:
        best_text, worst_text = f"🏆 {max(values)}", f"⚠️ {min(values)}"
    ax.annotate(best_text, (dates[best_idx], values[best_idx]),
                textcoords="offset points", xytext=(0, 12), ha="center",
                fontsize=9, fontweight="bold", color="green")
    ax.annotate(worst_text, (dates[worst_idx], values[worst_idx]),
                textcoords="offset points", xytext=(0, -14), ha="center",
                fontsize=9, fontweight="bold", color="red")

    if target:
        ax.axhline(y=target, color="green", linestyle="--",
                   label=f"Норма ({target})", linewidth=1.5, zorder=2)
        ax.axhline(y=int(target * zone_green / 100), color="orange",
                   linestyle=":", alpha=0.5, linewidth=1)
        ax.axhline(y=int(target * zone_yellow / 100), color="yellow",
                   linestyle=":", alpha=0.5, linewidth=1)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.set_ylabel("ПСВ (л/мин)")
    if title:
        ax.set_title(f"Пикфлоуметрия — {title}")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
```

In `bot.py`, add the import next to `from report import (...)`:

```python
import report_pdf
```

Replace the body of `bot._render_chart_png` (currently the inline plotting from `dates = ...` through `plt.tight_layout()`) so the function becomes:

```python
def _render_chart_png(rows: list, target: int, title: str) -> bytes:
    """Render measurements to PNG bytes (matplotlib Agg, in-memory)."""
    with report_pdf.RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(10, 5))
        try:
            report_pdf.draw_chart(ax, rows, target, ZONE_GREEN, ZONE_YELLOW, title=title)
            fig.autofmt_xdate()
            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120)
        finally:
            plt.close(fig)
    return buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_report_pdf.py test/test_bot.py -q`
Expected: PASS, включая `test_render_chart_png` и `test_figure_closed_on_render_error`.

- [ ] **Step 5: Commit**

```bash
git add report_pdf.py bot.py test/test_report_pdf.py
git commit -m "refactor(bot): extract chart drawing into report_pdf (SP4A)"
```

---

### Task 4: `report_pdf.build_pdf` — сборка PDF

**Files:**
- Modify: `report_pdf.py`
- Test: `test/test_report_pdf.py`

**Interfaces:**
- Consumes: `compute_stats`, `draw_chart`, `RENDER_LOCK`, `report.tod_label`, `report.pct_of`.
- Produces: `build_pdf(rows, *, target, child_name, period_label, zone_green=80, zone_yellow=60) -> bytes`.

- [ ] **Step 1: Write the failing test** (append to `test/test_report_pdf.py`)

```python
class TestBuildPdf:
    def _rows(self, n=3):
        return [
            {
                "pef_value": 250 + i,
                "time_of_day": "morning" if i % 2 == 0 else "evening",
                "measured_at": f"2026-09-{i + 1:02d} 08:00:00",
                "note": "болел" if i == 0 else "",
            }
            for i in range(n)
        ]

    def test_returns_pdf(self):
        from report_pdf import build_pdf
        data = build_pdf(self._rows(), target=260, child_name="Motya",
                         period_label="Сентябрь 2026")
        assert data[:5] == b"%PDF-"

    def test_multipage_for_many_rows(self):
        from report_pdf import build_pdf
        data = build_pdf(self._rows(80), target=260, child_name="M", period_label="P")
        assert data[:5] == b"%PDF-"
        assert data.count(b"/Type /Page") >= 3

    def test_empty_rows(self):
        from report_pdf import build_pdf
        data = build_pdf([], target=260, child_name="M", period_label="P")
        assert data[:5] == b"%PDF-"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_report_pdf.py::TestBuildPdf -q`
Expected: FAIL — `ImportError: cannot import name 'build_pdf'`.

- [ ] **Step 3: Write minimal implementation**

First, extend the `report.py` import in `report_pdf.py`:

```python
from report import month_title, pct_of, pef_zone, tod_label
```

Add the `io` import next to `threading`:

```python
import io
import threading
```

Append:

```python
ROWS_PER_PAGE = 35
_TABLE_HEADERS = ("Дата", "Время", "Период", "ПСВ", "%", "Зона", "Заметка")


def _chunks(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _avg_line(label: str, avg) -> str:
    return f"{label}: —" if avg is None else f"{label}: {avg:.0f} л/мин"


def _header_page(rows, target, child_name, period_label, stats, zone_green, zone_yellow):
    from datetime import datetime

    from matplotlib import pyplot as plt

    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait, inches
    fig.text(0.5, 0.955, "Отчёт по пикфлоуметрии", ha="center",
             fontsize=16, fontweight="bold")
    fig.text(0.07, 0.915, f"Ребёнок: {child_name}", fontsize=11)
    fig.text(0.07, 0.888, f"Период: {period_label}", fontsize=11)
    fig.text(0.07, 0.861, f"Целевая ПСВ: {target} л/мин", fontsize=11)
    fig.text(0.93, 0.915, f"Сформирован: {datetime.now():%d.%m.%Y}",
             ha="right", fontsize=9)

    ax = fig.add_axes([0.09, 0.55, 0.84, 0.27])
    if rows:
        draw_chart(ax, rows, target, zone_green, zone_yellow, text_labels=True)
    else:
        ax.axis("off")
        ax.text(0.5, 0.5, "Нет данных за период", ha="center", va="center", fontsize=12)

    fig.text(0.07, 0.49, "Статистика за период", fontsize=12, fontweight="bold")
    lines = [
        f"Всего замеров: {stats['total']}",
        f"Среднее: {stats['avg']:.0f} л/мин",
        f"Минимум: {stats['min']} л/мин    Максимум: {stats['max']} л/мин",
        _avg_line("Утро", stats["morning_avg"]),
        _avg_line("Вечер", stats["evening_avg"]),
        (f"Зоны: зелёная — {stats['zones']['green']}, "
         f"жёлтая — {stats['zones']['yellow']}, красная — {stats['zones']['red']}"),
    ]
    for i, line in enumerate(lines):
        fig.text(0.07, 0.455 - i * 0.022, line, fontsize=10)
    return fig


def _table_page(rows, target, zone_green, zone_yellow):
    from matplotlib import pyplot as plt

    fig = plt.figure(figsize=(8.27, 11.69))
    ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.axis("off")
    cell = []
    for r in rows:
        ts = r["measured_at"].replace("T", " ")
        zone = pef_zone(r["pef_value"], target, zone_green, zone_yellow)[1]
        cell.append([
            ts[:10], ts[11:16], tod_label(r["time_of_day"]),
            str(r["pef_value"]), f"{pct_of(r['pef_value'], target)}%",
            zone, (r.get("note") or "—"),
        ])
    table = ax.table(cellText=cell, colLabels=_TABLE_HEADERS,
                     loc="upper center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    return fig


def build_pdf(rows, *, target, child_name, period_label,
              zone_green: int = 80, zone_yellow: int = 60) -> bytes:
    """Собрать A4-PDF (шапка+график+статистика, затем постраничная таблица)."""
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    stats = compute_stats(rows, target, zone_green, zone_yellow)
    buf = io.BytesIO()
    with RENDER_LOCK, PdfPages(buf) as pdf:
        fig = _header_page(rows, target, child_name, period_label,
                           stats, zone_green, zone_yellow)
        pdf.savefig(fig)
        plt.close(fig)
        for chunk in _chunks(rows, ROWS_PER_PAGE):
            tfig = _table_page(chunk, target, zone_green, zone_yellow)
            pdf.savefig(tfig)
            plt.close(tfig)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_report_pdf.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report_pdf.py test/test_report_pdf.py
git commit -m "feat(report_pdf): A4 PDF builder (SP4A)"
```

---

### Task 5: Бот — кнопка «📄 Отчёт врачу» и генерация

**Files:**
- Modify: `bot.py`
- Test: `test/test_bot.py` (new `TestReportPdf`)

**Interfaces:**
- Consumes: `report_pdf.period_bounds`, `report_pdf.period_label`, `report_pdf.build_pdf`, `get_measurements_between`, `get_effective_target`, `_ctx`, `_child_name`, `_is_parent_member`, `_no_child_reply`.
- Produces: `bot.kb_report_periods() -> InlineKeyboardMarkup`, `bot._build_report_pdf_async(rows, target, child_name, period_label) -> bytes`, handlers `cb_report`, `cb_report_period`.

- [ ] **Step 1: Write the failing test** (append to `test/test_bot.py`)

```python
class TestReportPdf:
    def test_settings_has_report_button(self):
        import bot
        cbs = [b.callback_data for row in bot.kb_settings(260).inline_keyboard for b in row]
        assert "report" in cbs

    def test_report_period_keyboard(self):
        import bot
        cbs = [b.callback_data for row in bot.kb_report_periods().inline_keyboard for b in row]
        assert {"report_week", "report_month", "report_quarter"} <= set(cbs)

    def test_cb_report_parent_only(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock(); cb.from_user.id = 700; cb.answer = AsyncMock()
        asyncio.run(bot.cb_report(cb, member={"role": "child", "telegram_id": 700,
                                              "family_id": 1}))
        cb.answer.assert_awaited()

    def test_cb_report_period_sends_pdf(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.data = "report_month"
        cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        member = {"role": "parent", "telegram_id": 500, "family_id": 1}
        with patch.object(bot, "get_measurements_between", return_value=[{"pef_value": 250}]), \
             patch.object(bot, "resolve_active_child", return_value=111), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_member", return_value={"name": "Motya"}), \
             patch.object(bot, "_build_report_pdf_async",
                          new=AsyncMock(return_value=b"%PDF-1.4")):
            asyncio.run(bot.cb_report_period(cb, member=member))
        cb.message.answer_document.assert_awaited()

    def test_cb_report_period_empty_alerts(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.data = "report_month"
        cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer_document = AsyncMock()
        cb.message.delete = AsyncMock()
        member = {"role": "parent", "telegram_id": 500, "family_id": 1}
        with patch.object(bot, "get_measurements_between", return_value=[]), \
             patch.object(bot, "resolve_active_child", return_value=111), \
             patch.object(bot, "_build_report_pdf_async",
                          new=AsyncMock(return_value=b"%PDF-1.4")):
            asyncio.run(bot.cb_report_period(cb, member=member))
        cb.answer.assert_awaited()
        cb.message.answer_document.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_bot.py::TestReportPdf -q`
Expected: FAIL — `AttributeError: module 'bot' has no attribute 'kb_report_periods'`.

- [ ] **Step 3: Write minimal implementation**

Add the «📄 Отчёт врачу» button to `kb_settings` in `bot.py`, right after the CSV row:

```python
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export")],
        [InlineKeyboardButton(text="📄 Отчёт врачу", callback_data="report")],
```

Add `kb_report_periods` and the async helper next to `kb_export_periods`:

```python
def kb_report_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Неделя", callback_data="report_week")],
        [InlineKeyboardButton(text="🗓️ Месяц", callback_data="report_month")],
        [InlineKeyboardButton(text="📆 Квартал", callback_data="report_quarter")],
        [InlineKeyboardButton(text="⬅️ Настройки", callback_data="settings")],
    ])


async def _build_report_pdf_async(rows, target, child_name, period_label) -> bytes:
    """Собрать PDF в отдельном потоке (matplotlib CPU-bound)."""
    return await asyncio.to_thread(
        report_pdf.build_pdf, rows, target=target, child_name=child_name,
        period_label=period_label, zone_green=ZONE_GREEN, zone_yellow=ZONE_YELLOW,
    )
```

Add the handlers after `cb_export_month`:

```python
@router.callback_query(F.data == "report")
async def cb_report(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    family_id, child_id = await _ctx(member)
    if child_id is None:
        await _no_child_reply(callback, member)
        return
    await respond(callback, "📄 Отчёт врачу — выберите период:", kb=kb_report_periods())


@router.callback_query(F.data.regexp(r"^report_(week|month|quarter)$"))
async def cb_report_period(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    period = callback.data[len("report_"):]
    family_id, child_id = await _ctx(member)
    if child_id is None:
        await _no_child_reply(callback, member)
        return
    today = now_tz().date()
    date_from, date_to = report_pdf.period_bounds(period, today)
    rows = await _db(get_measurements_between, DB_PATH, child_id, date_from, date_to,
                     family_id=family_id)
    if not rows:
        await callback.answer("📭 Нет записей за период.", show_alert=True)
        return
    target = await _db(get_effective_target, family_id=family_id)
    name = await _child_name(member, child_id)
    label = report_pdf.period_label(period, today)
    try:
        pdf = await _build_report_pdf_async(rows, target, name, label)
    except Exception as e:
        logger.error("Ошибка генерации PDF-отчёта: %s", e)
        await callback.answer("Не удалось создать отчёт.", show_alert=True)
        return
    await answer_callback(callback)
    await callback.message.answer_document(
        BufferedInputFile(pdf, filename=f"peakflow_report_{name}_{period}.pdf"),
        caption=f"📄 Отчёт врачу — {label}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
        ]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_bot.py::TestReportPdf test/test_bot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): doctor PDF report button (SP4A)"
```

---

### Task 6: Web API — `GET /api/report/pdf`

**Files:**
- Modify: `web/api.py`
- Test: `test/test_webapp_api.py` (new `TestReportPdfApi`)

**Interfaces:**
- Consumes: `report_pdf.PERIODS`, `report_pdf.period_bounds`, `report_pdf.period_label`, `report_pdf.build_pdf`, `get_measurements_between`, `_effective_target`, `_child_name`, `_content_disposition`, `require_parent`.
- Produces: `GET /api/report/pdf?period=week|month|quarter`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`)

```python
class TestReportPdfApi:
    def _family_two_without_data(self):
        from database import create_family_with_owner, add_member
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        return f2

    def test_report_pdf_ok(self):
        _setup_db()
        add_measurement(TEST_DB, 250, "morning", CHILD_ID, CHILD_ID, family_id=1)
        r = _client().get("/api/report/pdf?period=month", headers=_auth(222))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content[:5] == b"%PDF-"

    def test_report_pdf_bad_period(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=year", headers=_auth(222))
        assert r.status_code == 422

    def test_report_pdf_child_forbidden(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(CHILD_ID))
        assert r.status_code == 403

    def test_report_pdf_empty(self):
        _setup_db()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(222))
        assert r.status_code == 404

    def test_report_pdf_isolation_no_leak(self):
        _setup_db()
        # family #1 has a measurement this month; family #2 has a child but no data
        add_measurement(TEST_DB, 111, "morning", CHILD_ID, CHILD_ID, family_id=1)
        self._family_two_without_data()
        r = _client().get("/api/report/pdf?period=month", headers=_auth(999))
        assert r.status_code == 404, "family #2 must not receive family #1 data"

    def test_report_pdf_family_two_own_data(self):
        _setup_db()
        f2 = self._family_two_without_data()
        add_measurement(TEST_DB, 250, "morning", 700, 999, family_id=f2)
        r = _client().get("/api/report/pdf?period=month", headers=_auth(999))
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test/test_webapp_api.py::TestReportPdfApi -q`
Expected: FAIL — `404`/`405` on `/api/report/pdf` (route missing).

- [ ] **Step 3: Write minimal implementation**

Add the import in `web/api.py` next to `from report import ...`:

```python
import report_pdf
```

Add the endpoint right after `export_csv` (before `backup`):

```python
    @app.get("/api/report/pdf")
    async def report_pdf_endpoint(period: str = "month",
                                  auth: dict = Depends(require_parent)):
        if period not in report_pdf.PERIODS:
            raise HTTPException(422, "Неверный период")
        child_id = auth["active_child_id"]
        if child_id is None:
            raise HTTPException(404, "Нет активного ребёнка")
        today = datetime.now(timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))).date()
        date_from, date_to = report_pdf.period_bounds(period, today)
        rows = await _db(get_measurements_between, config.DB_PATH, child_id,
                         date_from, date_to, auth["family_id"])
        if not rows:
            raise HTTPException(404, "Нет записей за период")
        target = await _db(_effective_target, config, auth["family_id"])
        child = _child_name(auth)
        label = report_pdf.period_label(period, today)
        try:
            pdf = await asyncio.to_thread(
                report_pdf.build_pdf, rows, target=target, child_name=child,
                period_label=label,
                zone_green=getattr(config, "ZONE_GREEN", 80),
                zone_yellow=getattr(config, "ZONE_YELLOW", 60),
            )
        except Exception as e:
            logger.error("Ошибка генерации PDF-отчёта: %s", e)
            raise HTTPException(500, "Не удалось создать отчёт")
        stamp = datetime.now(
            timezone(timedelta(hours=getattr(config, "TZ_OFFSET", 0)))
        ).strftime("%Y%m%d_%H%M")
        filename = f"peakflow_report_{child}_{period}_{stamp}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": _content_disposition(filename)},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test/test_webapp_api.py::TestReportPdfApi -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test/test_webapp_api.py
git commit -m "feat(web): PDF report endpoint (SP4A)"
```

---

### Task 7: Mini App — кнопка отчёта на экране Настройки

**Files:**
- Modify: `web/static/app.js`
- Test: `test/test_webapp_api.py` (`test_app_js_has_report_button`)

**Interfaces:**
- Consumes: `GET /api/report/pdf?period=...`, существующая функция `download(path, fallbackName)`.
- Produces: карточка с кнопками `data-report`.

- [ ] **Step 1: Write the failing test** (append to `test/test_webapp_api.py`, inside `TestReportPdfApi`)

```python
    def test_app_js_has_report_button(self):
        import pathlib
        js = pathlib.Path("web/static/app.js").read_text(encoding="utf-8")
        assert "/api/report/pdf" in js and "data-report" in js
```

Run: `python -m pytest test/test_webapp_api.py::TestReportPdfApi::test_app_js_has_report_button -q`
Expected: FAIL — `/api/report/pdf` отсутствует в `app.js`.

- [ ] **Step 2: Edit `web/static/app.js`**

In `loadSettings`, insert this card into `el.innerHTML` right after the CSV card (the one containing `${periodBtns}`):

```html
    <div class="card">
      <div class="label">📄 Отчёт врачу (PDF)</div>
      <div class="row" style="flex-wrap:wrap;gap:6px">
        <button class="mini" data-report="week">Неделя</button>
        <button class="mini" data-report="month">Месяц</button>
        <button class="mini" data-report="quarter">Квартал</button>
      </div>
    </div>`;
```

Add the click wiring next to the existing `[data-csv]` wiring:

```javascript
  el.querySelectorAll("[data-report]").forEach((b) =>
    b.onclick = () => download(`/api/report/pdf?period=${b.dataset.report}`, "peakflow_report.pdf")
      .catch((e) => showError(e.message)));
```

- [ ] **Step 3: Run the static test to verify it passes**

Run: `python -m pytest test/test_webapp_api.py::TestReportPdfApi::test_app_js_has_report_button -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js
git commit -m "feat(web): doctor report buttons in Mini App settings (SP4A)"
```

---

### Task 8: Документация и полная регрессия

**Files:**
- Modify: `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`

- [ ] **Step 1: Update `PROJECT.md`** — добавить SP4A: модуль `report_pdf.py`, эндпоинт `GET /api/report/pdf`, кнопка «📄 Отчёт врачу», периоды неделя/месяц/квартал.

- [ ] **Step 2: Update `wiki.md`** — назначение `report_pdf.py` (`period_bounds`/`period_label`/`compute_stats`/`draw_chart`/`build_pdf`/`RENDER_LOCK`), A4-отчёт, изоляция по `(family_id, child_id)`.

- [ ] **Step 3: Update `docs/superpowers/specs/2026-09-17-todo-plan.md`** — отметить `4.1` как выполненный (✅ SP4A); обновить фактическое число тестов в разделе «Метрики успеха» и в шапке.

- [ ] **Step 4: Run full verification**

Run:
```bash
python -m pytest test/ -q && python -m pytest test/test_webapp_api.py -q && python -m pyflakes bot.py database.py config.py report.py report_pdf.py web/*.py scripts/*.py && python -m compileall -q bot.py database.py config.py report.py report_pdf.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`, все тесты зелёные. Зафиксировать фактическое число тестов из вывода pytest.

- [ ] **Step 5: Commit**

```bash
git add PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: SP4A PDF reports (SP4A)"
```

---

## Self-Review

**1. Spec coverage:**
- Периоды неделя/месяц/квартал → Task 1 ✅
- Статистика за период (avg/min/max/зоны/утро/вечер) → Task 2 ✅
- Вынос `draw_chart` + `RENDER_LOCK`, сохранение PNG-поведения → Task 3 ✅
- A4-вёрстка (шапка+график+статистика+постраничная таблица), ч/б, пустой период → Task 4 ✅
- Бот: кнопка, экран периодов, только родители, alert при пустом периоде, `to_thread` → Task 5 ✅
- Web: `GET /api/report/pdf`, 422/404/403/500, `application/pdf`, изоляция → Task 6 ✅
- Mini App: карточка с периодами → Task 7 ✅
- Документация/счётчики/регрессия → Task 8 ✅
- Вне scope (доступ врача, геймификация, метрики, кастомные диапазоны) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; все шаги содержат код или точную команду. Статический `app.js`-тест создаётся в Task 7 (вместе с правкой JS), поэтому ни одна задача не оставляет набор красным.

**3. Type consistency:**
- `period_bounds(period, today) -> (str, str)`, `period_label(period, today) -> str` — Task 1, используются в Tasks 5/6.
- `compute_stats(rows, target, zone_green=80, zone_yellow=60)` — Task 2.
- `draw_chart(ax, rows, target, zone_green, zone_yellow, title=None, text_labels=False)` — Task 3, используется в Task 4.
- `build_pdf(rows, *, target, child_name, period_label, zone_green=80, zone_yellow=60)` — Task 4; бот-обёртка `_build_report_pdf_async(rows, target, child_name, period_label)` — Task 5; веб вызывает `build_pdf` напрямую — Task 6.
- `RENDER_LOCK` — Task 3, используется в Tasks 3/4.
- `kb_report_periods()`, `cb_report_period` — Task 5.

# SP3D — Завершение Фазы 2: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Обслуживать напоминания/отчёты всех семей, добавить dry-run миграции, убрать env-зависимости в отображении автора и добить изоляцию (child-scoped `get_measurement_by_id`, тесты chart/export).

**Architecture:** `database.list_families()` + существующие `list_family_children`/`get_reminder_hours` позволяют `scheduler_loop` итерировать семьи и детей (in-process). `get_measurement_by_id` получает `child_id`. Автор записи резолвится из `members`. Dry-run — внешний скрипт на копии БД.

**Tech Stack:** Python 3.11+, sqlite3, pytest, aiogram 3, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-17-sp3d-finalize-design.md`

## Global Constraints

- `member=None`/семья №1 — поведение без изменений; env-fallback только при `member is None`.
- Все data-вызовы tenant-aware (`family_id`, активный `child_id`); SQL параметризованный; DB через `_db`/`asyncio.to_thread`.
- Флаги напоминаний уже ключ `(child_id, date)` — не менять схему.
- Dry-run никогда не пишет в исходную БД (только копия).
- Тесты: `pytest test/ -q`, standalone `pytest test/test_webapp_api.py -q`; pyflakes+compileall.
- Не выносить планировщик/бот/веб в отдельные процессы (Фаза 3).

---

## File Structure

- `database.py` — `list_families`, `get_measurement_by_id(child_id)`.
- `bot.py` — автор записи из `members`; мульти-семейный `scheduler_loop`.
- `web/api.py` — передавать `child_id` в `get_measurement_by_id` (если используется).
- `scripts/migration_dry_run.py` — dry-run.
- `test/` — новые тесты; docs.

---

### Task 1: `get_measurement_by_id` child-scoped

**Files:** `database.py`; `test/test_bot.py` (`TestMeasurementByIdChildScope`).

**Interfaces:** `get_measurement_by_id(db_path, measurement_id, family_id=DEFAULT_FAMILY_ID, child_id=None) -> dict | None`

- [ ] **Step 1: Failing test**
```python
class TestMeasurementByIdChildScope:
    def test_child_scope_hides_sibling(self):
        from database import create_family_with_owner, add_member, add_measurement, get_measurement_by_id
        fid = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, fid, "child", "Маша")
        add_member(TEST_DB, 701, fid, "child", "Петя")
        mid_a = add_measurement(TEST_DB, 240, "morning", 700, 500, family_id=fid)
        assert get_measurement_by_id(TEST_DB, mid_a, family_id=fid, child_id=700)["pef_value"] == 240
        assert get_measurement_by_id(TEST_DB, mid_a, family_id=fid, child_id=701) is None

    def test_no_child_id_keeps_family_behavior(self):
        from database import add_measurement, get_measurement_by_id
        mid = add_measurement(TEST_DB, 250, "morning", 111, 222)
        assert get_measurement_by_id(TEST_DB, mid)["pef_value"] == 250
```
- [ ] **Step 2: RED** — `pytest test/test_bot.py::TestMeasurementByIdChildScope -q`.
- [ ] **Step 3: Implement** — добавить `child_id=None`; если задан, `AND child_id = ?`.
- [ ] **Step 4: GREEN.** Pass `child_id` where edit/delete read by id in bot.py (`_save_edit_any`, `cb_delete`, `cb_edit_any`) using `_ctx(member)`.
- [ ] **Step 5: Commit** — `feat(db): child-scoped get_measurement_by_id (SP3D)`.

---

### Task 2: Автор записи из `members`

**Files:** `bot.py`; `test/test_bot.py` (`TestAuthorFromMembers`).

**Interfaces:** `_history_line(m, target, member=None)`; `_format_history_lines(measurements, target, member=None)`; `_user_display_name(user_id, members_map=None)`.

- [ ] **Step 1: Failing test**
```python
class TestAuthorFromMembers:
    def test_history_line_marks_db_parent(self):
        from bot import _history_line
        m = {"pef_value": 240, "time_of_day": "morning", "measured_at": "2026-08-05 08:00:00",
             "added_by": 500, "note": None, "source": "manual"}
        line = _history_line(m, 260, member={"role": "parent", "family_id": 2, "telegram_id": 999},
                             author_roles={500: "parent"})
        assert "👨" in line  # parent marker, not env-based
```
> Точный набор параметров — на усмотрение исполнителя; главное: роль/имя автора берутся из `members`
> (карта `{telegram_id: role}`/`{telegram_id: name}`), fallback env только при `member is None`.
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** — резолвить авторов через `_db`/`get_member` (или одной картой на рендер); обновить `_history_line`, `_format_history_lines`, `_user_display_name`, CSV `display_name`.
- [ ] **Step 4: GREEN**; full suite.
- [ ] **Step 5: Commit** — `refactor(bot): resolve measurement author from members (SP3D)`.

---

### Task 3: Тесты изоляции chart/export (Mini App)

**Files:** `test/test_webapp_api.py` (`TestExportChartIsolation`).

- [ ] **Step 1: Failing test**
```python
class TestExportChartIsolation:
    def test_chart_isolated(self):
        from database import create_family_with_owner, add_member, add_measurement
        _setup_db()
        f2 = create_family_with_owner(TEST_DB, 999, "B")
        add_member(TEST_DB, 700, f2, "child", "Маша")
        add_measurement(TEST_DB, 250, "morning", 700, 999, family_id=f2)
        add_measurement(TEST_DB, 111, "morning", CHILD_ID, CHILD_ID, family_id=1)
        body = _client().get("/api/chart", headers=_auth(999)).json()
        assert [p["pef"] for p in body["points"]] == [250]

    def test_export_isolated(self):
        # аналогично: /api/export/csv семьи 2 не содержит строк семьи 1
        ...
```
- [ ] **Step 2: RED** (если найдётся утечка — фикс в `web/api.py`).
- [ ] **Step 3: Implement/verify.** Endpointы уже scoped (2C); при утечке — исправить.
- [ ] **Step 4: GREEN** (полный + standalone).
- [ ] **Step 5: Commit** — `test(web): chart/export family isolation (SP3D)`.

---

### Task 4: Мульти-семейный планировщик

**Files:** `database.py` (`list_families`), `bot.py`; `test/test_bot.py` (`TestMultiFamilyScheduler`).

**Interfaces:** `list_families(db_path) -> list[dict]`; изменённые `_maybe_ping_child`/`_escalate_parents`/`_send_weekly_report`/`scheduler_loop` принимают `family_id`/`child_id` явно (вместо `_ctx(None)`).

- [ ] **Step 1: Failing test**
```python
class TestMultiFamilyScheduler:
    def test_two_families_both_pinged(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, patch
        # семьи 1 и 2 с детьми; patch list_families -> [1,2];
        # patch get_reminder_hours: family1 child_morning=8, family2 child_morning=9
        # at 8:00 -> only family1 child pinged; at 9:00 -> family2 child pinged
        ...
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.**
  - `list_families(db_path) -> list` (SELECT * FROM families ORDER BY id).
  - `scheduler_loop`: `for family_id in list_families(): hours = get_reminder_hours(family_id); for child in list_family_children(family_id): _maybe_ping_child(child, hours, family_id, ...); _escalate_parents(...)`, weekly per family.
  - `_maybe_ping_child`/`_escalate_parents` принимают `child_id`/`family_id` (не вызывать `_ctx(None)`).
  - Уведомления — `_family_parents(member=None, family_id)`.
- [ ] **Step 4: GREEN** (полный прогон; семья №1 регрессия).
- [ ] **Step 5: Commit** — `feat(bot): multi-family scheduler (SP3D)`.

---

### Task 5: Dry-run миграции

**Files:** `scripts/migration_dry_run.py`, `scripts/__init__.py` (если нужно); `test/test_bot.py` (`TestMigrationDryRun`).

**Interfaces:** `dry_run(db_path) -> dict` (для теста); CLI `python -m scripts.migration_dry_run [DB_PATH]`.

- [ ] **Step 1: Failing test**
```python
class TestMigrationDryRun:
    def test_dry_run_reports_without_touching_source(self):
        import sqlite3, shutil
        from database import init_db
        # создаём v1-подобную БД (measurements user_id и т.п.) в TEST_DB
        # копию пути запоминаем: до/после init_db? Нет: dry_run сам копирует
        from scripts.migration_dry_run import dry_run
        before = open(TEST_DB, "rb").read()
        report = dry_run(TEST_DB)
        after = open(TEST_DB, "rb").read()
        assert before == after            # исходная БД не изменена
        assert "measurements" in report["tables"]
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** `dry_run`:
  - `tempfile.mkstemp`; `shutil.copy2(db_path, tmp)`;
  - `init_db(tmp)`;
  - собрать отчёт: `version_before` (PRAGMA до миграции — считать до copy/init), `version_after`, таблицы+counts, `families`, `members`, `orphan_child_ids` (`SELECT DISTINCT child_id FROM measurements WHERE child_id NOT IN (SELECT telegram_id FROM members)`);
  - удалить tmp в finally; вернуть dict.
  - CLI печатает отчёт JSON/текстом; исходную БД не трогает.
- [ ] **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(scripts): migration dry-run utility (SP3D)`.

---

### Task 6: Документация

**Files:** `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1:** `PROJECT.md`/`wiki.md`: мульти-семейный планировщик, dry-run, child-scoped `get_measurement_by_id`, автор из `members`.
- [ ] **Step 2:** todo-plan: отметить 2D выполненным (spec/plan SP3D); отметить 2.8/2.10 закрытыми; обновить число тестов.
- [ ] **Step 3:** `pytest test/ -q && pyflakes ... && compileall ... && echo ALL_GREEN`.
- [ ] **Step 4: Commit** — `docs: finalize Phase 2 (SP3D)`.

---

## Self-Review

**Spec coverage:** D1→T1; D2→T2; D3→T3; D4→T4; D5→T5; D6→T6 ✅
**Placeholder scan:** тесты/код приведены; T2/T3/T4 частично описательны из-за вариативности рендера — исполнитель следует образцу существующих тестов и правилу «из `members`, fallback env».
**Type consistency:** `get_measurement_by_id(..., child_id=None)`; `list_families(db) -> list[dict]`; `_maybe_ping_child(child, hours, family_id, hour, minute, today)`; `dry_run(db_path) -> dict`.

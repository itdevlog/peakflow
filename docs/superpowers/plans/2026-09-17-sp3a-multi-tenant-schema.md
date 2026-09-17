# SP3A — Мульти-тенантная схема и миграция v1→v2: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести SQLite-схему PeakFlow на мульти-тенантную модель (families/members, `measurements.child_id`+`family_id`, family-scoped settings/reminders) с миграцией прод-данных в семью №1 и без изменения production call-sites.

**Architecture:** `init_db` получает версионированную миграцию до `SCHEMA_VERSION = 2`. Таблицы, у которых меняется первичный ключ (`settings`, `reminders_sent`) или колонка-владелец (`measurements`), пересоздаются паттерном new→copy→drop→rename в одной транзакции. Доступ к данным становится tenant-aware: каждая функция принимает `family_id` (и/или `child_id`), но `family_id` идёт последним параметром со значением по умолчанию `DEFAULT_FAMILY_ID = 1`, поэтому существующие вызовы бота/Mini App остаются валидными. Регистрация семей и выбор активного ребёнка — вне этого плана (2B/2C).

**Tech Stack:** Python 3.11+, sqlite3 (WAL), pytest, aiogram 3 (не меняется в 2A), FastAPI (не меняется в 2A).

**Spec:** `docs/superpowers/specs/2026-09-17-sp3a-multi-tenant-schema-design.md`

## Global Constraints

- `SCHEMA_VERSION` поднять с `1` до `2`.
- `DEFAULT_FAMILY_ID = 1` — колонка/константа, единый источник.
- Обратная совместимость сигнатур: `family_id: int = DEFAULT_FAMILY_ID` всегда последним параметром; `child_id` занимает прежнюю позицию `user_id`.
- Миграция идемпотентна: повторный `init_db` не меняет данные.
- Перед миграцией непустой v1-БД вызывается `backup_db` в `<db>.v1.bak`; при ошибке бэкапа миграция прерывается (исключение наружу).
- Весь SQL — параметризованный; никакого `f-string` для значений пользователя.
- Legacy-таблицы `users` и `parent_child_links` **не удаляются** и кодом после миграции не читаются.
- Тесты запускаются без `.env` (`test/conftest.py`); файловая БД `test_peakflow.db`; фикстура `setup_db` чистит все суффиксы.
- Команды: `pytest test/ -q`, `python -m pyflakes bot.py database.py config.py report.py web/*.py`, `python -m compileall -q bot.py database.py config.py report.py web`.

---

## File Structure

- `database.py` — единственное место SQL и миграций. Здесь: `DEFAULT_FAMILY_ID`, `SCHEMA_VERSION = 2`, `_migrate_to_v2`, доступоры families/members, tenant-aware замеры/настройки/напоминания.
- `config.py` — без изменений в 2A (`.env` остаётся для сидинга и bootstrap).
- `bot.py`, `web/api.py` — **без изменений в 2A** (дефолтный `family_id`); проверяется регрессией.
- `test/test_bot.py` — обновление схемных тестов (`user_id`→`child_id`), новые тесты доступа/миграции/изоляции.
- `test/test_webapp_export.py` — сырые INSERT-ы `user_id`→`child_id`.
- `test/conftest.py` — без изменений.
- `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md` — документация схемы v2.

---

### Task 1: Таблицы families/members и доступоры

**Files:**
- Modify: `database.py` (создание таблиц в `init_db`, новые функции)
- Test: `test/test_bot.py` (новый класс `TestFamiliesAndMembers`)

**Interfaces:**
- Consumes: `get_connection(db_path)`, `_now()` из `database.py`.
- Produces:
  - `DEFAULT_FAMILY_ID: int = 1`
  - `create_family(db_path: str, name: str) -> int`
  - `get_family(db_path: str, family_id: int) -> dict | None`
  - `add_member(db_path: str, telegram_id: int, family_id: int, role: str, name: str) -> None`
  - `get_member(db_path: str, telegram_id: int) -> dict | None` → `{"telegram_id","family_id","role","name","created_at"}`
  - `list_family_children(db_path: str, family_id: int) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
class TestFamiliesAndMembers:
    def test_create_and_get_family(self):
        from database import create_family, get_family
        fid = create_family(TEST_DB, "Ивановы")
        fam = get_family(TEST_DB, fid)
        assert fam["name"] == "Ивановы"
        assert get_family(TEST_DB, 99999) is None

    def test_add_and_get_member(self):
        from database import create_family, add_member, get_member
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        m = get_member(TEST_DB, 222)
        assert m["family_id"] == fid and m["role"] == "parent" and m["name"] == "Олег"
        assert get_member(TEST_DB, 555) is None

    def test_add_member_is_upsert(self):
        from database import create_family, add_member, get_member
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        add_member(TEST_DB, 222, fid, "parent", "Олег Петров")
        assert get_member(TEST_DB, 222)["name"] == "Олег Петров"

    def test_list_family_children(self):
        from database import create_family, add_member, list_family_children
        fid = create_family(TEST_DB, "Семья")
        add_member(TEST_DB, 111, fid, "child", "Маша")
        add_member(TEST_DB, 222, fid, "parent", "Олег")
        add_member(TEST_DB, 333, fid, "child", "Петя")
        kids = list_family_children(TEST_DB, fid)
        assert sorted(k["name"] for k in kids) == ["Маша", "Петя"]

    def test_default_family_constants(self):
        import database
        assert database.DEFAULT_FAMILY_ID == 1
        assert database.SCHEMA_VERSION == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestFamiliesAndMembers -q`
Expected: FAIL — `ImportError: cannot import name 'create_family'` (и `SCHEMA_VERSION == 2` ещё не выполнен).

- [ ] **Step 3: Implement minimal code in `database.py`**

Bump version and add tables in `init_db` (вставь после создания `families`/`members`, см. ниже) и функции:

```python
DEFAULT_FAMILY_ID = 1

# Версия схемы БД (PRAGMA user_version). 2 = мульти-тенант (families/members).
SCHEMA_VERSION = 2
```

Внутри `init_db`, рядом с другими `CREATE TABLE IF NOT EXISTS`:

```python
    c.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            telegram_id INTEGER PRIMARY KEY,
            family_id INTEGER NOT NULL REFERENCES families(id),
            role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_members_family ON members(family_id)")
```

Новые функции (добавь после `get_effective_target`, перед reminder hours):

```python
def create_family(db_path: str, name: str) -> int:
    conn = get_connection(db_path)
    cur = conn.execute("INSERT INTO families (name) VALUES (?)", (name,))
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid


def get_family(db_path: str, family_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM families WHERE id = ?", (family_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_member(db_path: str, telegram_id: int, family_id: int, role: str, name: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
        "role = excluded.role, name = excluded.name",
        (telegram_id, family_id, role, name)
    )
    conn.commit()
    conn.close()


def get_member(db_path: str, telegram_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM members WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_family_children(db_path: str, family_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM members WHERE family_id = ? AND role = 'child' ORDER BY telegram_id",
        (family_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

> Примечание: `SCHEMA_VERSION = 2` сам по себе пока НЕ миграцию делает (миграция — Task 5). Чтобы существующие тесты не падали на этом шаге, в Step 4 запускается только новый класс.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestFamiliesAndMembers -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): add families/members tables and accessors (SP3A)"
```

---

### Task 2: Пересборка `measurements` под v2 (child_id, family_id) и tenant-aware запросы

**Files:**
- Modify: `database.py` (`_migrate_to_v2` каркас, `init_db`, все функции измерений)
- Test: `test/test_bot.py` (`TestMeasurementV2`, обновить `TestDatabase`)

**Interfaces:**
- Consumes: `DEFAULT_FAMILY_ID`, `_AUTO_FILTER`, `_today_str()`, `_now()`.
- Produces (сигнатуры с `child_id` на прежнем месте, `family_id` — последним kwarg):
  - `add_measurement(db_path, pef_value, time_of_day, child_id, added_by, source="manual", family_id=DEFAULT_FAMILY_ID) -> int`
  - `add_or_replace_measurement(db_path, pef_value, time_of_day, child_id, added_by, force=False, source="manual", family_id=DEFAULT_FAMILY_ID) -> tuple`
  - `get_last_of_tod(db_path, child_id, time_of_day, family_id=DEFAULT_FAMILY_ID) -> dict | None`
  - `replace_auto_measurement(db_path, child_id, time_of_day, pef_value, added_by, family_id=DEFAULT_FAMILY_ID) -> int | bool` (обновить, НЕ удалять — используется в `bot.py:464`)
  - `edit_measurement(db_path, measurement_id, new_value, child_id, family_id=DEFAULT_FAMILY_ID) -> bool`
  - `delete_measurement(db_path, measurement_id, child_id, family_id=DEFAULT_FAMILY_ID) -> bool`
  - `set_note(db_path, measurement_id, note, child_id, family_id=DEFAULT_FAMILY_ID) -> bool`
  - `get_measurement_by_id(db_path, measurement_id, family_id=DEFAULT_FAMILY_ID) -> dict | None`
  - `get_last_measurement(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> dict | None`
  - `get_all_measurements(db_path, child_id, include_auto=False, family_id=DEFAULT_FAMILY_ID) -> list`
  - `get_recent_measurements(db_path, child_id, limit=2, family_id=DEFAULT_FAMILY_ID) -> list`
  - `get_previous_of_tod(db_path, child_id, time_of_day, before_id, family_id=DEFAULT_FAMILY_ID) -> dict | None`
  - `get_today_measurements(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> list`
  - `has_today_measurement(db_path, child_id, time_of_day, skip_auto=False, family_id=DEFAULT_FAMILY_ID) -> bool`
  - `get_last_two_weeks(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> tuple`
  - `get_measurements_paginated(db_path, child_id, page=1, per_page=10, family_id=DEFAULT_FAMILY_ID) -> tuple`
  - `get_stats(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> dict`
  - `get_measurements_for_chart(db_path, child_id, days=30, family_id=DEFAULT_FAMILY_ID) -> list`
  - `get_measurements_for_month(db_path, child_id, year, month, include_auto=False, family_id=DEFAULT_FAMILY_ID) -> list`
  - `get_available_months(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> list`
  - `get_measurements_between(db_path, child_id, date_from, date_to, family_id=DEFAULT_FAMILY_ID) -> list`

- [ ] **Step 1: Write the failing test**

```python
class TestMeasurementV2:
    def test_child_id_column_and_family_scope(self):
        import sqlite3
        from database import init_db, add_measurement
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
        conn.close()
        assert "child_id" in cols and "user_id" not in cols
        assert "family_id" in cols

    def test_measurements_are_isolated_by_family(self):
        from database import add_measurement, get_all_measurements
        add_measurement(TEST_DB, 240, "morning", 111, 222, family_id=1)
        add_measurement(TEST_DB, 300, "morning", 111, 222, family_id=2)
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=1)] == [240]
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=2)] == [300]

    def test_add_and_get_last_default_family(self):
        from database import add_measurement, get_last_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222)
        m = get_last_measurement(TEST_DB, 111)
        assert m["pef_value"] == 250 and m["family_id"] == 1 and m["child_id"] == 111

    def test_has_today_isolated_by_family(self):
        from database import add_measurement, has_today_measurement
        add_measurement(TEST_DB, 250, "morning", 111, 222, family_id=1)
        assert has_today_measurement(TEST_DB, 111, "morning", family_id=1)
        assert not has_today_measurement(TEST_DB, 111, "morning", family_id=2)

    def test_paginated_isolated_by_family(self):
        from database import add_measurement, get_measurements_paginated
        add_measurement(TEST_DB, 250, "morning", 111, 222, family_id=1)
        add_measurement(TEST_DB, 300, "morning", 111, 222, family_id=2)
        items, total, pages = get_measurements_paginated(TEST_DB, 111, 1, 10, family_id=1)
        assert total == 1 and items[0]["pef_value"] == 250

    def test_measurements_index_created(self):
        import sqlite3
        from database import init_db
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_meas_family_child_time'"
        ).fetchall()
        conn.close()
        assert rows, "index idx_meas_family_child_time missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestMeasurementV2 -q`
Expected: FAIL — `user_id` ещё в схеме / `family_id` в INSERT не поддерживается.

- [ ] **Step 3: Implement in `database.py`**

3a. Добавь каркас миграции и rebuild-хелпер:

```python
def _table_columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


_MEASUREMENTS_V2_DDL = """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        family_id INTEGER NOT NULL DEFAULT 1,
        child_id INTEGER NOT NULL,
        pef_value INTEGER NOT NULL,
        time_of_day TEXT NOT NULL CHECK(time_of_day IN ('morning', 'evening', 'unknown')),
        measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        added_by INTEGER,
        note TEXT,
        source TEXT DEFAULT 'manual'
    )
"""


def _rebuild_measurements_v2(conn):
    """Ensure measurements is v2 (fresh DB → create; v1 → rebuild)."""
    cols = _table_columns(conn, "measurements")
    if not cols:                      # fresh DB: create v2 directly
        conn.execute(_MEASUREMENTS_V2_DDL)
        return
    if "child_id" in cols and "family_id" in cols:
        return                        # already v2
    conn.execute("ALTER TABLE measurements RENAME TO measurements_v1")
    conn.execute(_MEASUREMENTS_V2_DDL)
    has_old = "child_id" if "child_id" in cols else "user_id"
    conn.execute(
        f"INSERT INTO measurements (id, family_id, child_id, pef_value, time_of_day, "
        f"measured_at, added_by, note, source) "
        f"SELECT id, ?, {has_old}, pef_value, time_of_day, measured_at, added_by, "
        f"COALESCE(note, NULL), COALESCE(source, 'manual') FROM measurements_v1",
        (DEFAULT_FAMILY_ID,)
    )
    conn.execute("DROP TABLE measurements_v1")


def _migrate_to_v2(conn):
    """Idempotent v1 -> v2 migration; owns table creation for fresh DBs."""
    _rebuild_measurements_v2(conn)
    _rebuild_settings_v2(conn)
    _rebuild_reminders_v2(conn)
```

> `_rebuild_settings_v2` и `_rebuild_reminders_v2` определяются в Task 3/4. Чтобы Task 2 не падал, временно определи заглушки, которые создают v2-таблицы (для `reminders_sent` — со всеми флагами, иначе падают существующие тесты напоминаний; старые сигнатуры `(db, date, type)` продолжают работать за счёт `child_id DEFAULT 0`):
>
> ```python
> def _rebuild_settings_v2(conn):
>     conn.execute("""CREATE TABLE IF NOT EXISTS settings (
>         family_id INTEGER NOT NULL DEFAULT 1, key TEXT NOT NULL, value TEXT NOT NULL,
>         PRIMARY KEY (family_id, key))""")
>
> _REMINDER_FLAGS = ("morning_reminder", "evening_reminder", "weekly_report",
>                    "child_morning_reminder", "child_evening_reminder",
>                    "auto_morning", "auto_evening")
>
> def _rebuild_reminders_v2(conn):
>     cols = _table_columns(conn, "reminders_sent")
>     if "child_id" in cols:
>         return
>     conn.execute("ALTER TABLE reminders_sent RENAME TO reminders_v1")
>     flags = ",\n        ".join(f"{f} INTEGER DEFAULT 0" for f in _REMINDER_FLAGS)
>     conn.execute(f"""CREATE TABLE reminders_sent (
>         child_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
>         {flags}, PRIMARY KEY (child_id, date))""")
>     present = [f for f in _REMINDER_FLAGS if f in cols]
>     sel = ", ".join(present)
>     conn.execute(
>         f"INSERT INTO reminders_sent (child_id, date{', ' + sel if sel else ''}) "
>         f"SELECT 0, date{', ' + sel if sel else ''} FROM reminders_v1")
>     conn.execute("DROP TABLE reminders_v1")
> ```
>
> Если `reminders_sent` ещё нет (чистая БД) — `_rebuild_reminders_v2` создаёт её v2-форму. Task 4 заменит заглушку версией с сидингом из `.env CHILD_ID`.

3b. В `init_db` **удали** старые `CREATE TABLE measurements` и `CREATE TABLE reminders_sent`, а также `ALTER TABLE measurements ...` для `time_of_day`/`added_by`/`note`/`source`. Порядок: сначала `families`/`members`, затем `_seed_default_family`, затем `_migrate_to_v2`, затем индексы:

```python
    _seed_default_family(c)                      # Task 5; заглушка pass до Task 5
    _migrate_to_v2(c)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_meas_family_child_time
        ON measurements(family_id, child_id, measured_at)
    """)
```

> До Task 5 вставь заглушку `def _seed_default_family(conn): pass` рядом с rebuild-функциями.

3c. Обнови тело функций измерений. Все SQL, где было `user_id = ?`, заменить на `child_id = ? AND family_id = ?`, добавив параметр. Пример для `get_last_measurement`:

```python
def get_last_measurement(db_path: str, child_id: int,
                         family_id: int = DEFAULT_FAMILY_ID) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM measurements WHERE child_id = ? AND family_id = ? ORDER BY id DESC LIMIT 1",
        (child_id, family_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
```

Для `add_measurement`:

```python
def add_measurement(db_path: str, pef_value: int, time_of_day: str,
                    child_id: int, added_by: int, source: str = "manual",
                    family_id: int = DEFAULT_FAMILY_ID) -> int:
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO measurements (family_id, pef_value, time_of_day, child_id, added_by, measured_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (family_id, pef_value, time_of_day, child_id, added_by, now_str, source)
    )
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return mid
```

Аналогично по всем функциям из списка Interfaces. **Проверь каждую** через `grep -n "user_id" database.py` — после правок не должно остаться вхождений, кроме legacy-таблицы `users` (её нет в новом коде) и комментариев.

3d. Замени в `add_or_replace_measurement` все `user_id` на `child_id` + `family_id`:

```python
        if not force:
            existing = conn.execute(
                f"SELECT id FROM measurements WHERE child_id = ? AND family_id = ? "
                f"AND time_of_day = ? AND measured_at LIKE ? AND {_AUTO_FILTER} "
                f"ORDER BY id DESC LIMIT 1",
                (child_id, family_id, time_of_day, f"{today}%")
            ).fetchone()
```

и INSERT/UPDATE — с `family_id`. Аналогично обнови `replace_auto_measurement`:

```python
def replace_auto_measurement(db_path: str, child_id: int, time_of_day: str,
                             pef_value: int, added_by: int,
                             family_id: int = DEFAULT_FAMILY_ID):
    today = _today_str()
    conn = get_connection(db_path)
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "UPDATE measurements SET pef_value = ?, added_by = ?, source = 'manual', "
        "measured_at = ? WHERE child_id = ? AND family_id = ? AND time_of_day = ? "
        "AND source = 'auto' AND measured_at LIKE ? RETURNING id",
        (pef_value, added_by, now_str, child_id, family_id, time_of_day, f"{today}%")
    ).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else False
```

3e. В тестах `test/test_bot.py` обнови сырые SQL с `user_id` на `child_id` (строки ~173, 188, 206, 220, 261, 271, 1053, 1056, 1059, 1213, 1266) и переименуй `test_index_created` на проверку `idx_meas_family_child_time`. В `test_webapp_export.py` (строки ~66–72) замени `user_id` на `child_id`.

- [ ] **Step 4: Run full test suite**

Run: `pytest test/ -q`
Expected: все проходят (236+); новые `TestMeasurementV2` — PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py test/test_webapp_export.py
git commit -m "feat(db): rebuild measurements for multi-tenant (child_id, family_id)"
```

---

### Task 3: Пересборка `settings` под (family_id, key)

**Files:**
- Modify: `database.py`
- Test: `test/test_bot.py` (обновить `TestDatabase.test_settings_get_set`, `test_get_effective_target_from_db`; новый `TestSettingsFamilyScope`)

**Interfaces:**
- Consumes: `DEFAULT_FAMILY_ID`, `get_connection`.
- Produces:
  - `get_setting(db_path, key, default="", family_id=DEFAULT_FAMILY_ID) -> str`
  - `set_setting(db_path, key, value, family_id=DEFAULT_FAMILY_ID) -> None`
  - `get_effective_target(db_path, fallback, family_id=DEFAULT_FAMILY_ID) -> int`
  - `get_reminder_hours(db_path, family_id=DEFAULT_FAMILY_ID) -> dict`

- [ ] **Step 1: Write the failing test**

```python
class TestSettingsFamilyScope:
    def test_settings_isolated_by_family(self):
        from database import set_setting, get_setting
        set_setting(TEST_DB, "target_pef", "300", family_id=1)
        set_setting(TEST_DB, "target_pef", "400", family_id=2)
        assert get_setting(TEST_DB, "target_pef", family_id=1) == "300"
        assert get_setting(TEST_DB, "target_pef", family_id=2) == "400"

    def test_effective_target_per_family(self):
        from database import set_setting, get_effective_target
        set_setting(TEST_DB, "target_pef", "333", family_id=1)
        assert get_effective_target(TEST_DB, 260, family_id=1) == 333
        assert get_effective_target(TEST_DB, 260, family_id=2) == 260

    def test_reminder_hours_per_family(self):
        from database import set_setting, get_reminder_hours
        set_setting(TEST_DB, "reminder_child_morning", "6", family_id=1)
        set_setting(TEST_DB, "reminder_child_morning", "9", family_id=2)
        assert get_reminder_hours(TEST_DB, family_id=1)["child_morning"] == 6
        assert get_reminder_hours(TEST_DB, family_id=2)["child_morning"] == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestSettingsFamilyScope -q`
Expected: FAIL — `get_setting() got an unexpected keyword argument 'family_id'`.

- [ ] **Step 3: Implement in `database.py`**

Замени заглушку `_rebuild_settings_v2` на реализацию:

```python
def _rebuild_settings_v2(conn):
    """v1->v2: settings(key PK) -> settings(family_id, key) PK."""
    cols = _table_columns(conn, "settings")
    if "family_id" in cols:
        return
    conn.execute("ALTER TABLE settings RENAME TO settings_v1")
    conn.execute("""
        CREATE TABLE settings (
            family_id INTEGER NOT NULL DEFAULT 1,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (family_id, key)
        )
    """)
    conn.execute(
        "INSERT INTO settings (family_id, key, value) SELECT ?, key, value FROM settings_v1",
        (DEFAULT_FAMILY_ID,)
    )
    conn.execute("DROP TABLE settings_v1")
```

Обнови `get_setting`/`set_setting`/`get_effective_target`/`get_reminder_hours`:

```python
def get_setting(db_path: str, key: str, default: str = "",
                family_id: int = DEFAULT_FAMILY_ID) -> str:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT value FROM settings WHERE family_id = ? AND key = ?", (family_id, key)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(db_path: str, key: str, value: str,
                family_id: int = DEFAULT_FAMILY_ID) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO settings (family_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(family_id, key) DO UPDATE SET value = excluded.value",
        (family_id, key, value)
    )
    conn.commit()
    conn.close()
```

`get_effective_target(db_path, fallback, family_id=DEFAULT_FAMILY_ID)` и `get_reminder_hours(db_path, family_id=DEFAULT_FAMILY_ID)` — прокинуть `family_id` в `get_setting`.

Обнови дефолтный сидинг в `init_db`: замени

```python
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_pef', '260')")
```

на

```python
    c.execute(
        "INSERT OR IGNORE INTO settings (family_id, key, value) VALUES (?, 'target_pef', '260')",
        (DEFAULT_FAMILY_ID,)
    )
```

Обнови фикстуру `setup_db` в `test/test_bot.py` (строка ~23) на тот же INSERT с `family_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestSettingsFamilyScope test/test_bot.py::TestDatabase -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): family-scoped settings (family_id, key)"
```

---

### Task 4: Пересборка `reminders_sent` под (child_id, date)

**Files:**
- Modify: `database.py`
- Test: `test/test_bot.py` (новый `TestRemindersFamilyScope`)

**Interfaces:**
- Consumes: `DEFAULT_FAMILY_ID`, `get_connection`, `_reminder_column`.
- Produces:
  - `mark_reminder_sent(db_path, date_str, reminder_type, child_id, ...)` → **child_id передаётся явно**, дефолт невозможен (это ключ). Сигнатура: `mark_reminder_sent(db_path, date_str, reminder_type, child_id) -> None`
  - `was_reminder_sent(db_path, date_str, reminder_type, child_id) -> bool`

> **Важно:** здесь `child_id` обязателен (без дефолта), потому что без него запись неоднозначна. Обнови **все** вызовы в `bot.py` (4 места) — см. Step 3c.

- [ ] **Step 1: Write the failing test**

```python
class TestRemindersFamilyScope:
    def test_reminders_isolated_by_child(self):
        from database import mark_reminder_sent, was_reminder_sent
        mark_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert not was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 222)

    def test_flags_not_reset_by_other_type_same_child(self):
        from database import mark_reminder_sent, was_reminder_sent
        mark_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        mark_reminder_sent(TEST_DB, "2026-09-17", "evening_missing", 111)
        mark_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "morning_missing", 111)
        assert was_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestRemindersFamilyScope -q`
Expected: FAIL — `mark_reminder_sent() missing positional argument` / старый PK `date`.

- [ ] **Step 3: Implement in `database.py`**

3a. Замени заглушку `_rebuild_reminders_v2`:

```python
def _rebuild_reminders_v2(conn):
    """v1->v2: reminders_sent(date PK) -> (child_id, date) PK."""
    cols = _table_columns(conn, "reminders_sent")
    if "child_id" in cols:
        return
    flags = ("morning_reminder", "evening_reminder", "weekly_report",
             "child_morning_reminder", "child_evening_reminder",
             "auto_morning", "auto_evening")
    existing = _table_columns(conn, "reminders_sent")
    present = [f for f in flags if f in existing]
    conn.execute("ALTER TABLE reminders_sent RENAME TO reminders_v1")
    cols_def = ",\n            ".join(f"{f} INTEGER DEFAULT 0" for f in flags)
    conn.execute(f"""
        CREATE TABLE reminders_sent (
            child_id INTEGER NOT NULL DEFAULT 0,
            date TEXT NOT NULL,
            {cols_def},
            PRIMARY KEY (child_id, date)
        )
    """)
    sel_flags = ", ".join(present)
    conn.execute(
        f"INSERT INTO reminders_sent (child_id, date{f', {sel_flags}' if sel_flags else ''}) "
        f"SELECT ?, date{f', {sel_flags}' if sel_flags else ''} FROM reminders_v1",
        (0,)
    )
    conn.execute("DROP TABLE reminders_v1")
```

> `child_id = 0` для исторических строк — намеренно: это «неизвестный ребёнок», чтобы не приписать чужие напоминания. Активный ребёнок получит свои записи с Task 4; для сидинга см. Task 5 (перенос при наличии `.env CHILD_ID`).

3b. Обнови `mark_reminder_sent` / `was_reminder_sent`:

```python
def mark_reminder_sent(db_path: str, date_str: str, reminder_type: str, child_id: int) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO reminders_sent (child_id, date) VALUES (?, ?) "
        "ON CONFLICT(child_id, date) DO NOTHING",
        (child_id, date_str)
    )
    column = _reminder_column(reminder_type)
    if column:
        conn.execute(
            f"UPDATE reminders_sent SET {column} = 1 WHERE child_id = ? AND date = ?",
            (child_id, date_str)
        )
    conn.commit()
    conn.close()


def was_reminder_sent(db_path: str, date_str: str, reminder_type: str, child_id: int) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM reminders_sent WHERE child_id = ? AND date = ?", (child_id, date_str)
    ).fetchone()
    conn.close()
    if not row:
        return False
    column = _reminder_column(reminder_type)
    return bool(row[column]) if column and column in row.keys() else False
```

3c. Обнови вызовы в `bot.py`: `mark_reminder_sent(DB_PATH, today, flag)` → `(DB_PATH, today, flag, CHILD_ID)`, аналогично `was_reminder_sent`. Затронуты `_maybe_ping_child`, `_escalate_parents`, `scheduler_loop` (weekly). В `_escalate_parents` auto-флаги — тоже `CHILD_ID`.

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py bot.py test/test_bot.py
git commit -m "feat(db): child-scoped reminders_sent (child_id, date)"
```

---

### Task 5: Бэкап перед миграцией, сидинг семьи №1, идемпотентность

**Files:**
- Modify: `database.py` (`init_db`, `_seed_default_family`, backup hook)
- Test: `test/test_bot.py` (`TestMigrationV2`)

**Interfaces:**
- Consumes: `backup_db`, `create_family`, `add_member`, `get_member`.
- Produces:
  - `_seed_default_family(conn)` — наполняет `families`/`members` из legacy `users`/`parent_child_links`, иначе из env (`CHILD_ID`, `PARENT_IDS`, `CHILD_NAME`).
  - `init_db` вызывает `backup_db` при `user_version < 2` и непустой БД.

- [ ] **Step 1: Write the failing test**

```python
class TestMigrationV2:
    def _make_v1_db(self):
        import sqlite3
        from database import init_db
        for ext in ["", "-wal", "-shm", "-journal"]:
            import os
            if os.path.exists(TEST_DB + ext):
                os.remove(TEST_DB + ext)
        conn = sqlite3.connect(TEST_DB)
        conn.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, pef_value INTEGER NOT NULL, "
            "time_of_day TEXT NOT NULL, measured_at TIMESTAMP, added_by INTEGER, note TEXT)"
        )
        conn.execute(
            "CREATE TABLE reminders_sent (date TEXT PRIMARY KEY, morning_reminder INTEGER DEFAULT 0, "
            "evening_reminder INTEGER DEFAULT 0, weekly_report INTEGER DEFAULT 0)"
        )
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, "
                     "role TEXT, child_name TEXT, age INTEGER, target_pef INTEGER, created_at TIMESTAMP, "
                     "reminder_morning INTEGER, reminder_evening INTEGER)")
        conn.execute("INSERT INTO measurements (user_id, pef_value, time_of_day, measured_at, added_by) "
                     "VALUES (111, 240, 'morning', '2026-01-01 08:00:00', 222)")
        conn.execute("INSERT INTO users (user_id, first_name, role, child_name) VALUES (111, 'Маша', 'child', 'Маша')")
        conn.execute("INSERT INTO users (user_id, first_name, role) VALUES (222, 'Олег', 'parent')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('target_pef', '300')")
        conn.execute("INSERT INTO reminders_sent (date, morning_reminder) VALUES ('2026-01-01', 1)")
        conn.commit()
        conn.close()

    def test_migration_preserves_measurements_and_settings(self):
        import sqlite3
        from database import init_db, SCHEMA_VERSION
        self._make_v1_db()
        init_db(TEST_DB)
        conn = sqlite3.connect(TEST_DB)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        row = conn.execute("SELECT family_id, child_id, pef_value FROM measurements").fetchone()
        setting = conn.execute("SELECT value FROM settings WHERE key='target_pef' AND family_id=1").fetchone()
        conn.close()
        assert version == SCHEMA_VERSION == 2
        assert row == (1, 111, 240)
        assert setting[0] == "300"

    def test_migration_seeds_family_from_legacy_users(self):
        from database import init_db, get_member, get_family
        self._make_v1_db()
        init_db(TEST_DB)
        assert get_family(TEST_DB, 1) is not None
        assert get_member(TEST_DB, 111)["role"] == "child"
        assert get_member(TEST_DB, 222)["role"] == "parent"

    def test_migration_is_idempotent(self):
        from database import init_db
        self._make_v1_db()
        init_db(TEST_DB)
        init_db(TEST_DB)  # must not fail or duplicate
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        assert conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM families").fetchone()[0] == 1
        conn.close()

    def test_backup_created_before_migration(self, tmp_path):
        import os
        from unittest.mock import patch
        import database
        self._make_v1_db()
        with patch.object(database, "backup_db") as m:
            database.init_db(TEST_DB)
            m.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestMigrationV2 -q`
Expected: FAIL — семьи не сидируются / `backup_db` не вызывается.

- [ ] **Step 3: Implement in `database.py`**

3a. Сидинг:

```python
def _seed_default_family(conn) -> None:
    """Create family #1 and its members from legacy users, else from env."""
    existing = conn.execute("SELECT id FROM families WHERE id = ?", (DEFAULT_FAMILY_ID,)).fetchone()
    if existing:
        return
    conn.execute("INSERT INTO families (id, name) VALUES (?, ?)", (DEFAULT_FAMILY_ID, "Семья"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    seeded = False
    if "user_id" in cols:
        for r in conn.execute("SELECT user_id, role, first_name, child_name FROM users"):
            role = (r["role"] or "").strip()
            if role not in ("parent", "child"):
                continue
            name = r["child_name"] or r["first_name"] or ""
            conn.execute(
                "INSERT OR IGNORE INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?)",
                (r["user_id"], DEFAULT_FAMILY_ID, role, name)
            )
            seeded = True
    if not seeded:
        # Fallback: seed from .env (config snapshots at import time).
        import config
        child_id = getattr(config, "CHILD_ID", 0)
        parent_ids = getattr(config, "PARENT_IDS", []) or []
        child_name = getattr(config, "CHILD_NAME", "Ребёнок")
        if child_id:
            conn.execute(
                "INSERT OR IGNORE INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'child', ?)",
                (child_id, DEFAULT_FAMILY_ID, child_name)
            )
        for pid in parent_ids:
            if pid:
                conn.execute(
                    "INSERT OR IGNORE INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'parent', 'Родитель')",
                    (pid, DEFAULT_FAMILY_ID)
                )
```

3b. Бэкап-хук в `init_db` — в самом начале, до изменений:

```python
def init_db(db_path: str):
    # Back up a non-empty pre-v2 database before migrating it.
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        probe = sqlite3.connect(db_path)
        try:
            version = probe.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            version = 0
        probe.close()
        if version < SCHEMA_VERSION:
            backup_db(db_path, db_path + ".v1.bak")
    conn = get_connection(db_path)
    ...
```

Добавь `import os` в начало `database.py`.

3c. Вызови сидинг после создания families/members и до `_migrate_to_v2`:

```python
    _seed_default_family(c)
    c.execute("CREATE INDEX IF NOT EXISTS idx_members_family ON members(family_id)")
    _migrate_to_v2(c)
```

3d. В `_rebuild_reminders_v2` замени `child_id = 0` на `config.CHILD_ID`, если он задан:

```python
    import config
    seed_child = getattr(config, "CHILD_ID", 0) or 0
    ...
        (seed_child,)
```

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS (все, включая `TestMigrationV2`).

- [ ] **Step 5: Verify on a copy of the prod DB (manual, no data loss)**

```bash
cp peakflow.db /tmp/peakflow-migration-check.db
source venv/bin/activate && python -c "
import database, sqlite3
database.init_db('/tmp/peakflow-migration-check.db')
c = sqlite3.connect('/tmp/peakflow-migration-check.db')
print('version', c.execute('PRAGMA user_version').fetchone()[0])
print('measurements', c.execute('SELECT COUNT(*) FROM measurements').fetchone()[0])
print('families', c.execute('SELECT id, name FROM families').fetchall())
print('members', c.execute('SELECT telegram_id, role, name FROM members').fetchall())
print('orphan child_ids', c.execute('SELECT DISTINCT child_id FROM measurements WHERE child_id NOT IN (SELECT telegram_id FROM members)').fetchall())
c.close()
"
```
Expected: `version 2`, `measurements 183`, семья 1 существует, участники перенесены. Если `orphan child_ids` непуст — зафиксировать в отчёте (исторические user_id вне `.env`), данные не теряются.

- [ ] **Step 6: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): v1->v2 migration, default family seeding, pre-migration backup"
```

---

### Task 6: Тесты изоляции семей (сквозные)

**Files:**
- Test: `test/test_bot.py` (новый `TestFamilyIsolation`)

**Interfaces:**
- Consumes: всё из Task 1–5.

- [ ] **Step 1: Write the failing test**

```python
class TestFamilyIsolation:
    def test_two_families_do_not_see_each_other(self):
        from database import (create_family, add_member, add_measurement,
                              set_setting, mark_reminder_sent,
                              get_all_measurements, get_stats, get_setting,
                              get_today_measurements, has_today_measurement,
                              was_reminder_sent)
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        add_measurement(TEST_DB, 240, "morning", 111, 111, family_id=f1)
        add_measurement(TEST_DB, 300, "morning", 111, 111, family_id=f2)
        set_setting(TEST_DB, "target_pef", "240", family_id=f1)
        set_setting(TEST_DB, "target_pef", "400", family_id=f2)
        mark_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)

        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=f1)] == [240]
        assert [m["pef_value"] for m in get_all_measurements(TEST_DB, 111, family_id=f2)] == [300]
        assert get_stats(TEST_DB, 111, family_id=f1)["total"] == 1
        assert get_setting(TEST_DB, "target_pef", family_id=f1) == "240"
        assert get_setting(TEST_DB, "target_pef", family_id=f2) == "400"
        assert was_reminder_sent(TEST_DB, "2026-09-17", "weekly", 111)
        assert get_today_measurements(TEST_DB, 111, family_id=f2)[0]["pef_value"] == 300

    def test_delete_only_touches_own_family(self):
        from database import create_family, add_measurement, delete_measurement, get_all_measurements
        f1 = create_family(TEST_DB, "A")
        f2 = create_family(TEST_DB, "B")
        mid1 = add_measurement(TEST_DB, 240, "morning", 111, 111, family_id=f1)
        mid2 = add_measurement(TEST_DB, 300, "morning", 111, 111, family_id=f2)
        assert delete_measurement(TEST_DB, mid2, 111, family_id=f1) is False
        assert len(get_all_measurements(TEST_DB, 111, family_id=f2)) == 1
        assert delete_measurement(TEST_DB, mid1, 111, family_id=f1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestFamilyIsolation -q`
Expected: FAIL, если какой-то запрос игнорирует `family_id` (например, `get_stats` или `delete_measurement`).

- [ ] **Step 3: Fix any leaking queries in `database.py`**

Найди запросы без фильтра `family_id` среди functions measurement/settings/reminders и добавь его. Проверка: `grep -n "FROM measurements" database.py` — в каждой строке должен быть `family_id = ?`. Исправляй минимально.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestFamilyIsolation -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "test(db): cross-family isolation coverage"
```

---

### Task 7: Документация, версия, полная регрессия

**Files:**
- Modify: `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`

- [ ] **Step 1: Update `PROJECT.md` (раздел «База данных»)**

Добавь таблицы `families`, `members`; отметь, что `measurements` теперь имеет `family_id`/`child_id`; указать `SCHEMA_VERSION = 2` и `DEFAULT_FAMILY_ID = 1`; упомянуть миграцию v1→v2 и бэкап `*.v1.bak`.

- [ ] **Step 2: Update `wiki.md` (раздел про database.py)**

Опиши tenant-aware доступоры и правило «`family_id` — последний параметр, дефолт 1».

- [ ] **Step 3: Update план `docs/superpowers/specs/2026-09-17-todo-plan.md`**

Отметь подпроект 2A выполненным со ссылкой на spec/plan; оставь 2B/2C/2D.

- [ ] **Step 4: Full verification**

Run:
```bash
source venv/bin/activate && python -m pytest test/ -q && python -m pyflakes bot.py database.py config.py report.py web/*.py && python -m compileall -q bot.py database.py config.py report.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`, все тесты проходят.

- [ ] **Step 5: Commit**

```bash
git add PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: multi-tenant schema v2 (SP3A)"
```

---

## Self-Review

**1. Spec coverage:**
- Новые таблицы `families`/`members` → Task 1 ✅
- `measurements.user_id → child_id`, `+family_id` → Task 2 ✅
- `settings` per family → Task 3 ✅
- `reminders_sent` per child → Task 4 ✅
- Миграция v1→v2 + бэкап + сидинг семьи №1 → Task 5 ✅
- Изоляция семей → Task 6 ✅
- Data layer tenant-aware + совместимость → Task 2–4 ✅
- Документация/версия → Task 7 ✅
- Вне scope (регистрация, выбор ребёнка, doctor, PostgreSQL) — не включено ✅

**2. Placeholder scan:** плейсхолдеров нет; заглушки `_rebuild_settings_v2`/`_rebuild_reminders_v2` — намеренные промежуточные артефакты TDD, заменяются в Task 3/4 с тестами.

**3. Type consistency:**
- `child_id` везде на прежней позиции `user_id`; `family_id` — последний kwarg с `DEFAULT_FAMILY_ID`, кроме `mark_reminder_sent`/`was_reminder_sent` (child_id обязателен).
- `get_stats` возвращает тот же dict, добавляя фильтрацию по family.
- `DEFAULT_FAMILY_ID` и `SCHEMA_VERSION` определены в Task 1 и используются далее.

**Отклонение от spec (согласовано):** `family_id` идёт последним параметром с дефолтом 1 вместо второго позиционного — ради обратной совместимости 300+ call-sites. Spec обновить при реализации Task 7.

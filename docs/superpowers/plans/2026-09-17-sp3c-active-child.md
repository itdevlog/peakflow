# SP3C — Активный ребёнок и tenant-aware данные: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Сделать все обращения к данным tenant-aware (`family_id` + активный ребёнок), добавить выбор активного ребёнка и снять временный gate для семей ≠ №1.

**Architecture:** Схема v4 добавляет `members.active_child_id`. Хелпер `_ctx(member)` в bot.py возвращает `(family_id, child_id)`; при `member=None` — прежние `.env`-значения (семья №1), что сохраняет существующие тесты. Все data-вызовы получают `family_id` и `child_id` активного ребёнка; уведомления — родителям семьи из `members`. Web-слой берёт `family_id`/`active_child_id` из `member` и добавляет эндпоинты выбора ребёнка. Временный gate из 2B удаляется.

**Tech Stack:** Python 3.11+, sqlite3, pytest, aiogram 3, FastAPI, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-17-sp3c-active-child-design.md`

## Global Constraints

- `SCHEMA_VERSION` 3 → 4; миграция идемпотентна.
- `member=None` во всех хелперах/хендлерах → поведение семьи №1 из `.env` (регрессия исключена).
- Все data-функции вызываются с `family_id` и `child_id` активного ребёнка; параметризованный SQL.
- `child_id is None` (нет детей) → подсказка «добавьте ребёнка», без данных.
- Уведомления — родителям семьи (`members.role='parent'`), не env `PARENT_IDS`.
- Удалить `REG_SOON_MESSAGE` и gate-и 2B.
- Ребёнок не может выбрать активного ребёнка (активный = сам).
- Тесты: `pytest test/ -q`; standalone `pytest test/test_webapp_api.py -q`; без `.env`.
- Команды проверки: `python -m pytest test/ -q && python -m pyflakes bot.py database.py config.py report.py web/*.py && python -m compileall -q bot.py database.py config.py report.py web && echo ALL_GREEN`.

---

## File Structure

- `database.py` — `active_child_id`, `set_active_child`, `resolve_active_child`, `count_family_children`.
- `bot.py` — `_ctx`/`_child_name`/`_family_parents`; tenant-aware хендлеры; выбор ребёнка; снятие gate.
- `web/api.py` — `_resolve_user` даёт `active_child_id`; `/api/children`, `/api/active-child`; scoped data-эндпоинты; снятие gate.
- `web/notify.py` — получатели из `members`.
- `web/static/app.js` — селектор ребёнка.
- `test/` — новые тесты; `PROJECT.md`/`wiki.md`/todo-plan — документация.

---

### Task 1: Схема v4 и доступоры активного ребёнка

**Files:** `database.py`; `test/test_bot.py` (`TestActiveChild`).

**Interfaces:**
- `set_active_child(db_path, telegram_id, child_id) -> bool`
- `resolve_active_child(db_path, member) -> int | None`
- `count_family_children(db_path, family_id) -> int`

- [ ] **Step 1: Failing test**

```python
class TestActiveChild:
    def test_schema_v4(self):
        import database
        assert database.SCHEMA_VERSION == 4

    def test_active_child_column(self):
        import sqlite3
        from database import init_db
        init_db(TEST_DB)
        c = sqlite3.connect(TEST_DB)
        cols = [r[1] for r in c.execute("PRAGMA table_info(members)")]
        c.close()
        assert "active_child_id" in cols

    def test_set_active_child_validates_same_family(self):
        from database import create_family_with_owner, add_member, set_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        f2 = create_family_with_owner(TEST_DB, 501, "B")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f2, "child", "Петя")
        assert set_active_child(TEST_DB, 500, 700) is True
        assert get_member(TEST_DB, 500)["active_child_id"] == 700
        assert set_active_child(TEST_DB, 500, 701) is False  # чужой ребёнок
        assert set_active_child(TEST_DB, 500, 999) is False

    def test_resolve_active_child_for_child_member(self):
        from database import create_family_with_owner, add_member, resolve_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 700)) == 700

    def test_resolve_active_child_defaults_to_first(self):
        from database import create_family_with_owner, add_member, resolve_active_child, get_member
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f1, "child", "Петя")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) in (700, 701)

    def test_resolve_active_child_none_without_children(self):
        from database import create_family_with_owner, resolve_active_child, get_member
        create_family_with_owner(TEST_DB, 500, "A")
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) is None

    def test_resolve_active_child_respects_selection(self):
        from database import (create_family_with_owner, add_member, set_active_child,
                              resolve_active_child, get_member)
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 701, f1, "child", "Петя")
        set_active_child(TEST_DB, 500, 701)
        assert resolve_active_child(TEST_DB, get_member(TEST_DB, 500)) == 701

    def test_count_family_children(self):
        from database import create_family_with_owner, add_member, count_family_children
        f1 = create_family_with_owner(TEST_DB, 500, "A")
        add_member(TEST_DB, 700, f1, "child", "Маша")
        add_member(TEST_DB, 222, f1, "parent", "Олег")
        assert count_family_children(TEST_DB, f1) == 1
```

- [ ] **Step 2: RED** — `pytest test/test_bot.py::TestActiveChild -q` → ImportError/version.

- [ ] **Step 3: Implement** — `SCHEMA_VERSION = 4`; в `init_db` после `_create_invites_v3` добавь `_add_active_child_v4(conn)`:

```python
def _add_active_child_v4(conn):
    try:
        conn.execute("ALTER TABLE members ADD COLUMN active_child_id INTEGER")
    except sqlite3.OperationalError:
        pass
```

Доступоры (после `list_family_children`):

```python
def count_family_children(db_path: str, family_id: int) -> int:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM members WHERE family_id = ? AND role = 'child'", (family_id,)
    ).fetchone()
    conn.close()
    return row[0]


def set_active_child(db_path: str, telegram_id: int, child_id: int) -> bool:
    member = get_member(db_path, telegram_id)
    if not member or member["role"] != "parent":
        return False
    child = get_member(db_path, child_id)
    if not child or child["role"] != "child" or child["family_id"] != member["family_id"]:
        return False
    conn = get_connection(db_path)
    conn.execute("UPDATE members SET active_child_id = ? WHERE telegram_id = ?", (child_id, telegram_id))
    conn.commit()
    conn.close()
    return True


def resolve_active_child(db_path: str, member) -> Optional[int]:
    if not member:
        return None
    if member["role"] == "child":
        return member["telegram_id"]
    children = list_family_children(db_path, member["family_id"])
    ids = [c["telegram_id"] for c in children]
    selected = member.get("active_child_id") if hasattr(member, "get") else None
    if selected in ids:
        return selected
    return ids[0] if ids else None
```

- [ ] **Step 4: GREEN** — `pytest test/test_bot.py::TestActiveChild -q` → PASS.
- [ ] **Step 5: Commit** — `feat(db): active child column and accessors (SP3C)`.

---

### Task 2: Контекстные хелперы (`_ctx`, `_child_name`, `_family_parents`)

**Files:** `bot.py`; `test/test_bot.py` (`TestTenantContext`).

**Interfaces:**
- `_ctx(member) -> tuple[int, int | None]`
- `_child_name(member, child_id) -> str`
- `_family_parents(member, family_id) -> list[int]`
- `_add_child_hint()` текст подсказки.

- [ ] **Step 1: Failing test**

```python
class TestTenantContext:
    def test_ctx_fallback_env(self, monkeypatch):
        import asyncio, bot
        monkeypatch.setattr(bot, "CHILD_ID", 111)
        assert asyncio.run(bot._ctx(None)) == (bot.DEFAULT_FAMILY_ID, 111)

    def test_ctx_child_self(self):
        import asyncio, bot
        assert asyncio.run(bot._ctx({"role": "child", "telegram_id": 700, "family_id": 2})) == (2, 700)

    def test_ctx_parent_uses_resolve(self, monkeypatch):
        import asyncio, bot
        from unittest.mock import patch
        with patch.object(bot, "resolve_active_child", return_value=700):
            assert asyncio.run(bot._ctx({"role": "parent", "telegram_id": 500, "family_id": 2})) == (2, 700)

    def test_child_name_fallback(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "CHILD_NAME", "Motya")
        assert bot._child_name(None, None) == "Motya"

    def test_family_parents_env_fallback(self, monkeypatch):
        import asyncio, bot
        monkeypatch.setattr(bot, "PARENT_IDS", [222, 333])
        assert asyncio.run(bot._family_parents(None, 1)) == [222, 333]
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.** Импортируй `resolve_active_child`, `list_family_children`, `DEFAULT_FAMILY_ID` из `database`; `count_family_children`.

```python
async def _ctx(member):
    """(family_id, active_child_id). member=None → семья №1 из .env."""
    if not member:
        return DEFAULT_FAMILY_ID, CHILD_ID
    if member["role"] == "child":
        return member["family_id"], member["telegram_id"]
    return member["family_id"], await _db(resolve_active_child, DB_PATH, member)


def _child_name(member, child_id, child_name=None) -> str:
    if not member or child_id is None:
        return CHILD_NAME
    return child_name or CHILD_NAME


async def _family_parents(member, family_id) -> list:
    if not member:
        return list(PARENT_IDS)
    return [p["telegram_id"] for p in await _db(list_family_parents, DB_PATH, family_id)]
```

> В `database.py` добавь `list_family_parents`:
> ```python
> def list_family_parents(db_path: str, family_id: int) -> list:
>     conn = get_connection(db_path)
>     rows = conn.execute(
>         "SELECT * FROM members WHERE family_id = ? AND role = 'parent' ORDER BY telegram_id",
>         (family_id,)
>     ).fetchall()
>     conn.close()
>     return [dict(r) for r in rows]
> ```
> Вызывающий код, где уже есть список детей, передаёт имя активного ребёнка через
> `_child_name(member, child_id, child_name=...)`; иначе fallback на env `CHILD_NAME`.

- [ ] **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(bot): tenant context helpers (SP3C)`.

---

### Task 3: Tenant-aware ядро бота (статус, меню, добавление/правка/заметки)

**Files:** `bot.py`; `test/test_bot.py` (`TestTenantAwareCore`).

**Interfaces:** Consumes `_ctx`, `_child_name`, `_family_parents`.

- [ ] **Step 1: Failing test**

```python
class TestTenantAwareCore:
    def test_status_block_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import patch
        seen = {}
        def fake_today(db, child_id, family_id=bot.DEFAULT_FAMILY_ID):
            seen["child_id"] = child_id
            seen["family_id"] = family_id
            return []
        with patch.object(bot, "get_today_measurements", side_effect=fake_today), \
             patch.object(bot, "get_recent_measurements", return_value=[]), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700):
            asyncio.run(bot.build_status_block(member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert seen.get("child_id") == 700
        assert seen.get("family_id") == 2

    def test_add_measurement_uses_context(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        state = MagicMock()
        state.get_data = AsyncMock(return_value={"input_context": "add"})
        state.update_data = AsyncMock(); state.set_state = AsyncMock(); state.clear = AsyncMock()
        calls = {}
        def fake_add(db, pef, tod, child_id, added_by, source="manual", family_id=1):
            calls["child_id"] = child_id; calls["family_id"] = family_id; return 1
        with patch.object(bot, "respond", new=AsyncMock()), \
             patch.object(bot, "has_today_measurement", return_value=False), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "add_measurement", side_effect=fake_add), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_previous_of_tod", return_value=None), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(cb, state, 250, "morning",
                                                 member={"role": "parent", "telegram_id": 500, "family_id": 2}))
        assert calls.get("child_id") == 700 and calls.get("family_id") == 2
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.**
  - `build_status_block(member=None)`: `family_id, child_id = await _ctx(member)`; заменить `CHILD_ID`→`child_id`, добавить `family_id=family_id` в `get_today_measurements`/`get_recent_measurements`; `target = await _db(get_effective_target, DB_PATH, TARGET_PEF, family_id=family_id)`; имя — `_child_name(member, child_id, ...)`.
  - `send_main_menu(message_or_callback, user_id, member=None)`: вызывать `build_status_block(member)`; `kb_main` строит кнопку выбора ребёнка при `count_family_children(family_id) > 1`.
  - `cb_add`/`cb_add_force`/`_save_measurement`/`_persist_measurement`/`cb_pick_tod`: `_ctx(member)`; `has_today_measurement(..., child_id, ..., family_id=family_id)`; `add_or_replace_measurement`/`replace_auto_measurement`/`add_measurement` — с `child_id` и `family_id`; уведомления — `_family_parents` и `_child_name`.
  - `_save_edit_last`/`_save_edit_any`/`cb_edit_any`/`cb_delete`/`cb_delete_confirm`/`input_note`/`cb_edit_last`: `edit_measurement`/`delete_measurement`/`set_note`/`get_measurement_by_id` — с `child_id`/`family_id` из `_ctx`; `member` прокинуть в `send_main_menu`.
  - Проверить `grep -n "CHILD_ID\|CHILD_NAME\|PARENT_IDS" bot.py`: остаться должны только fallback внутри `_ctx`/`_child_name`/`_family_parents`, `main()` (валидация env) и `get_effective_target` fallback.

- [ ] **Step 4: GREEN**; **Step 5: Commit** — `refactor(bot): tenant-aware core handlers (SP3C)`.

---

### Task 4: Tenant-aware история, график, сводка, статистика, неделя

**Files:** `bot.py`; `test/test_bot.py` (`TestTenantAwareViews`).

- [ ] **Step 1: Failing test** — `_show_history` с `member={"role":"parent","family_id":2,...}` и `resolve_active_child→700` вызывает `get_measurements_paginated(DB_PATH, 700, ..., family_id=2)`.
```python
class TestTenantAwareViews:
    def test_history_uses_active_child(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 500; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        seen = {}
        def fake_pag(db, child_id, page=1, per_page=10, family_id=1):
            seen["child_id"]=child_id; seen["family_id"]=family_id; return [], 0, 1
        with patch.object(bot, "get_measurements_paginated", side_effect=fake_pag), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "resolve_active_child", return_value=700), \
             patch.object(bot, "respond", new=AsyncMock()):
            asyncio.run(bot._show_history(cb, 1, member={"role":"parent","telegram_id":500,"family_id":2}))
        assert seen.get("child_id")==700 and seen.get("family_id")==2
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** — `_show_history`, `cb_summary`, `cb_stats`, `_send_month_chart`, `cb_chart_download`, `_send_weekly_report` принимают `member`; `_ctx`; передают `family_id`/`child_id`; имя — `_child_name`.
- [ ] **Step 4: GREEN**; **Step 5: Commit** — `refactor(bot): tenant-aware views (SP3C)`.

---

### Task 5: Tenant-aware экспорт, бэкап, настройки, планировщик, уведомления

**Files:** `bot.py`; `test/test_bot.py` (`TestTenantAwareOps`).

- [ ] **Step 1: Failing test** — уведомление родителю при замере идёт по `_family_parents` (патч `bot.bot.send_message`, `_family_parents` возвращает [222]) и не шлёт env `PARENT_IDS`; эскалация/недельный отчёт — то же.
```python
class TestTenantAwareOps:
    def test_measurement_notifies_family_parents(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.from_user.id = 700; cb.answer = AsyncMock()
        cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        state = MagicMock(); state.get_data = AsyncMock(return_value={})
        state.update_data = AsyncMock(); state.set_state = AsyncMock()
        sent = []
        async def fake_parents(member, family_id): return [222]
        async def fake_send(pid, text, **kw): sent.append(pid)
        with patch.object(bot, "respond", new=AsyncMock()), \
             patch.object(bot, "add_or_replace_measurement", return_value=(1, "ok")), \
             patch.object(bot, "replace_auto_measurement", return_value=False), \
             patch.object(bot, "add_measurement", return_value=1), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_previous_of_tod", return_value=None), \
             patch.object(bot, "_family_parents", side_effect=fake_parents), \
             patch.object(bot, "_ctx", new=AsyncMock(return_value=(2, 700))), \
             patch.object(bot.bot, "send_message", side_effect=fake_send), \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot._persist_measurement(cb, state, 240, "morning", member={"role":"child","telegram_id":700,"family_id":2}))
        assert 222 in sent
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** — `cb_export*`, `cb_backup`, `cb_settings`, `cb_reminders`, `input_reminder_hour`, `cb_change_target`, `input_target`, scheduler (`_maybe_ping_child`, `_escalate_parents`, `scheduler_loop`): `member`/family; `get_*`/`set_setting`/`backup_db`/`get_reminder_hours`/`mark_reminder_sent`/`was_reminder_sent` — с `family_id`/`child_id`; уведомления — `_family_parents`; `CHILD_NAME` → `_child_name`.
- [ ] **Step 4: GREEN**; **Step 5: Commit** — `refactor(bot): tenant-aware ops and notifications (SP3C)`.

---

### Task 6: Выбор ребёнка в боте и снятие gate

**Files:** `bot.py`; `test/test_bot.py` (`TestChildSelector`, `TestGateRemoved`).

- [ ] **Step 1: Failing test**
```python
class TestChildSelector:
    def test_main_menu_has_child_button_when_multi(self):
        import bot
        kb = bot.kb_main(is_parent_user=True, show_child_button=True)
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "pick_child" in cbs

    def test_set_child_sets_active(self):
        import asyncio, bot
        from unittest.mock import AsyncMock, MagicMock, patch
        cb = MagicMock(); cb.data = "set_child_700"; cb.from_user.id = 500
        cb.answer = AsyncMock(); cb.message = MagicMock(); cb.message.answer = AsyncMock(); cb.message.delete = AsyncMock()
        with patch.object(bot, "set_active_child", return_value=True) as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()) as menu:
            asyncio.run(bot.cb_set_child(cb, member={"role":"parent","telegram_id":500,"family_id":2}))
        m.assert_called_once_with(bot.DB_PATH, 500, 700)
        menu.assert_awaited()


class TestGateRemoved:
    def test_no_reg_soon_in_source(self):
        import pathlib
        assert "REG_SOON_MESSAGE" not in pathlib.Path("bot.py").read_text()
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.**
  - `kb_main(is_parent_user, show_child_button=False)`; `send_main_menu` вычисляет `count_family_children(family_id) > 1`.
  - `cb_pick_child` (список детей, `set_child_<id>`), `cb_set_child` (safe-parse, `set_active_child`, вернуть меню).
  - Удалить `REG_SOON_MESSAGE` и все gate-ветки в `MemberMiddleware`, `cmd_start`, `cmd_cancel`, `catch_all`, `send_main_menu`; `catch_all` для family-2 снова показывает меню.
  - «⚙️ Настройки» → «🧒 Дети»: пометить активного ребёнка.
- [ ] **Step 4: GREEN** (полный прогон); **Step 5: Commit** — `feat(bot): child selector and remove interim gate (SP3C)`.

---

### Task 7: Tenant-aware Mini App и эндпоинты выбора ребёнка

**Files:** `web/api.py`, `web/notify.py`; `test/test_webapp_api.py`, `test/test_webapp_settings.py`, `test/test_webapp_write.py`.

- [ ] **Step 1: Failing test**
```python
def test_children_and_active_child_endpoint():
    _setup_db()
    from database import create_family_with_owner, add_member
    create_family_with_owner(TEST_DB, 999, "Новые")
    add_member(TEST_DB, 700, 2, "child", "Маша")   # id семьи из create_family_with_owner
    r = _client().get("/api/children", headers=_auth(999))
    assert r.status_code == 200 and "children" in r.json()

def test_status_scoped_to_active_child():
    _setup_db()
    # семья 2 с ребёнком 700 и своим замером; семья 1 с замером 111
    ...
    body = _client().get("/api/status", headers=_auth(999)).json()
    assert all(m["child_id"] == 700 for m in body["today"])
```
- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.**
  - `_resolve_user`: убрать 403-gate; вернуть `active_child_id` (`resolve_active_child`) и `family_id`; `member`.
  - `require_user` не бросает 403 для family≠1.
  - Все data-эндпоинты: `family_id = auth["family_id"]`, `child_id = auth["active_child_id"]`; заменить `config.CHILD_ID`/`config.CHILD_NAME` (замеры, история, график, статистика, статус, настройки, экспорт, бэкап).
  - `GET /api/children` → список детей семьи + активный; `PUT /api/active-child` → `set_active_child` (родитель).
  - `web/notify.py`: получатели — родители семьи (параметр `recipients`), имя — активного ребёнка; убрать env `PARENT_IDS`.
  - `/api/me` отдаёт `children` и `active_child_id`.
- [ ] **Step 4: GREEN** (полный + standalone web); **Step 5: Commit** — `feat(web): tenant-aware Mini App and child selection (SP3C)`.

---

### Task 8: Селектор ребёнка во фронтенде

**Files:** `web/static/app.js`.

- [ ] **Step 1: Implement** (JS без рантайма — аккуратная правка + Python-проверка статики):
  - Из `/api/me` сохранить `state.children`, `state.activeChildId`.
  - Если `children.length > 1` — показать селектор в шапке; `change` → `PUT /api/active-child` → перезагрузить текущий экран.
  - Если детей нет — подсказка «Добавьте ребёнка в боте».
- [ ] **Step 2: Python-тест** (`test/test_webapp_api.py`), что `/api/me` содержит `children` и `active_child_id`.
- [ ] **Step 3: Commit** — `feat(web): child selector in Mini App (SP3C)`.

---

### Task 9: Документация и полная регрессия

**Files:** `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`.

- [ ] **Step 1:** `PROJECT.md`: `active_child_id`, `SCHEMA_VERSION=4`, снятие gate, выбор ребёнка.
- [ ] **Step 2:** `wiki.md`: `_ctx`/`_child_name`/`_family_parents`, выбор ребёнка, tenant-aware.
- [ ] **Step 3:** todo-plan: 2C выполнен; остаются 2D, Фазы 3–5.
- [ ] **Step 4:** `pytest test/ -q && pyflakes ... && compileall ... && echo ALL_GREEN`.
- [ ] **Step 5: Commit** — `docs: active child and tenant-aware data (SP3C)`.

---

## Self-Review

**1. Spec coverage:**
- Схема v4 + доступоры → Task 1 ✅
- `_ctx`/`_child_name`/`_family_parents` → Task 2 ✅
- Tenant-aware ядро → Task 3 ✅
- Виды (история/график/сводка/статистика/неделя) → Task 4 ✅
- Экспорт/бэкап/настройки/планировщик/уведомления → Task 5 ✅
- Выбор ребёнка + снятие gate → Task 6 ✅
- Mini App + эндпоинты + notify → Task 7 ✅
- Фронтенд → Task 8 ✅
- Документация/регрессия → Task 9 ✅
- Вне scope (аналитика по всем детям, doctor, PostgreSQL) — не включено ✅

**2. Placeholder scan:** для механических замен даны правило + список точек; ключевые новые хелперы и тесты приведены кодом. Task 2 содержит промежуточную формулировку `_family_parents` — исполнитель реализует через новый `list_family_parents` (код приведён).

**3. Type consistency:**
- `_ctx` → `(family_id: int, child_id: int | None)` — Task 2, используется Tasks 3–7.
- `_child_name(member, child_id, child_name=None)` — Task 2.
- `resolve_active_child(db, member) -> int | None`; `set_active_child(db, uid, child_id) -> bool`; `count_family_children(db, family_id) -> int` — Task 1, используются далее.
- `list_family_parents(db, family_id) -> list` — Task 2.
- `SCHEMA_VERSION=4` — Task 1, docs Task 9.

**Отклонение/риск:** Task 2 `_child_name` для родителя требует имени активного ребёнка; вызывающий код передаёт `child_name` (уже есть список детей). Если имя недоступно — fallback `CHILD_NAME`/«Ребёнок».

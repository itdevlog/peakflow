# SP3B — Регистрация, инвайты и роли из БД: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать новым семьям самостоятельно регистрироваться в боте (создание семьи, вход по invite-коду, карточки детей) и перевести проверки ролей бота и Mini App на роль из таблицы `members`.

**Architecture:** `SCHEMA_VERSION = 3` добавляет таблицу `invites` (многоразовые бессрочные токены: один `parent` на семью + по карточке `child` на ребёнка). `create_family_with_owner` и `join_by_invite` — атомарные операции. Роль доставляется в хендлеры через aiogram-middleware `MemberMiddleware`, который один раз на update читает `members` в `asyncio.to_thread` и кладёт `member` в `data`; хелпер `_role(member, uid)` возвращает роль из БД, а при `member is None` — fallback на `.env` (сохраняет текущее поведение семьи №1 и существующие тесты). Web-слой резолвит `family_id`/роль из `members`.

**Tech Stack:** Python 3.11+, sqlite3 (WAL), pytest, aiogram 3, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-17-sp3b-registration-design.md`

## Global Constraints

- `SCHEMA_VERSION` поднять с `2` до `3`.
- Токен инвайта: `secrets.token_urlsafe(8)`; уникальность через PK; коллизия → retry (до 5 раз).
- Инвайты многоразовые и бессрочные; родительский — один на семью (`role='parent'`); карточка ребёнка — `role='child'` с именем.
- `create_family_with_owner`/`join_by_invite` атомарны (`BEGIN IMMEDIATE`) и идемпотентны по `telegram_id`.
- Роль в рантайме: `_role(member, uid)` — из `members`; при `member is None` fallback `is_parent`/`is_child` из `.env`.
- Middleware: единственный запрос к БД на update, через `asyncio.to_thread`.
- Legacy `users`/`parent_child_links` не трогаем.
- Все SQL параметризованные.
- Сигнатуры существующих функций-измерений/настроек не ломаем.
- Тесты: `pytest test/ -q`; без `.env` (conftest); файл `test_peakflow.db`.
- Команды: `python -m pyflakes bot.py database.py config.py report.py web/*.py`, `python -m compileall -q bot.py database.py config.py report.py web`.

---

## File Structure

- `database.py` — таблица `invites` + доступоры + атомарные `create_family_with_owner`/`join_by_invite` + миграция v3.
- `bot.py` — `MemberMiddleware`, `_role`, регистрационные хендлеры, экраны «Участники»/«Дети», рефакторинг ролевых проверок.
- `web/api.py` — роль/семья из `members` в `_resolve_user`.
- `test/test_bot.py`, `test/test_webapp_api.py` — новые тесты.
- `PROJECT.md`, `wiki.md`, спеки/план — документация.

---

### Task 1: Таблица `invites` и доступоры

**Files:**
- Modify: `database.py` (schema v3, `_migrate_to_v3`, функции)
- Test: `test/test_bot.py` (новый класс `TestInvites`)

**Interfaces:**
- Produces:
  - `create_invite(db_path, family_id, role, name="") -> str`
  - `get_invite(db_path, token) -> dict | None`
  - `get_family_invite(db_path, family_id) -> dict | None`
  - `list_child_cards(db_path, family_id) -> list[dict]`
  - `delete_invite(db_path, token) -> bool`
  - `regenerate_family_invite(db_path, family_id) -> str`

- [ ] **Step 1: Write the failing test**

```python
class TestInvites:
    def test_schema_version_is_3(self):
        import database
        assert database.SCHEMA_VERSION == 3

    def test_create_and_get_invite(self):
        from database import create_family, create_invite, get_invite
        fid = create_family(TEST_DB, "Семья")
        token = create_invite(TEST_DB, fid, "parent")
        inv = get_invite(TEST_DB, token)
        assert inv["family_id"] == fid and inv["role"] == "parent"
        assert get_invite(TEST_DB, "nope") is None

    def test_child_card_has_name(self):
        from database import create_family, create_invite, list_child_cards
        fid = create_family(TEST_DB, "Семья")
        create_invite(TEST_DB, fid, "child", "Маша")
        cards = list_child_cards(TEST_DB, fid)
        assert len(cards) == 1 and cards[0]["name"] == "Маша"

    def test_tokens_are_unique(self):
        from database import create_family, create_invite
        fid = create_family(TEST_DB, "Семья")
        tokens = {create_invite(TEST_DB, fid, "child", f"c{i}") for i in range(20)}
        assert len(tokens) == 20

    def test_delete_invite_invalidates(self):
        from database import create_family, create_invite, get_invite, delete_invite
        fid = create_family(TEST_DB, "Семья")
        token = create_invite(TEST_DB, fid, "parent")
        assert delete_invite(TEST_DB, token) is True
        assert get_invite(TEST_DB, token) is None
        assert delete_invite(TEST_DB, token) is False

    def test_regenerate_family_invite_replaces(self):
        from database import (create_family, create_invite, get_family_invite,
                              regenerate_family_invite, get_invite)
        fid = create_family(TEST_DB, "Семья")
        old = create_invite(TEST_DB, fid, "parent")
        new = regenerate_family_invite(TEST_DB, fid)
        assert new != old
        assert get_invite(TEST_DB, old) is None
        assert get_family_invite(TEST_DB, fid)["token"] == new

    def test_get_family_invite_none_when_absent(self):
        from database import create_family, get_family_invite
        fid = create_family(TEST_DB, "Семья")
        assert get_family_invite(TEST_DB, fid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestInvites -q`
Expected: FAIL — `ImportError: cannot import name 'create_invite'`.

- [ ] **Step 3: Implement in `database.py`**

`import secrets` в начало. `SCHEMA_VERSION = 3`. Добавь в `_migrate_to_v2`-цепочку отдельную `_create_invites_v3(conn)` (вызывается из `init_db` после `_migrate_to_v2`), создающую таблицу идемпотентно:

```python
def _create_invites_v3(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            token TEXT PRIMARY KEY,
            family_id INTEGER NOT NULL REFERENCES families(id),
            role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invites_family ON invites(family_id)")
```

Доступоры:

```python
def create_invite(db_path: str, family_id: int, role: str, name: str = "") -> str:
    conn = get_connection(db_path)
    try:
        for _ in range(5):
            token = secrets.token_urlsafe(8)
            try:
                conn.execute(
                    "INSERT INTO invites (token, family_id, role, name) VALUES (?, ?, ?, ?)",
                    (token, family_id, role, name)
                )
                conn.commit()
                return token
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Не удалось создать уникальный invite-токен")
    finally:
        conn.close()


def get_invite(db_path: str, token: str) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_family_invite(db_path: str, family_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM invites WHERE family_id = ? AND role = 'parent' "
        "ORDER BY created_at DESC LIMIT 1",
        (family_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_child_cards(db_path: str, family_id: int) -> list:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM invites WHERE family_id = ? AND role = 'child' ORDER BY created_at",
        (family_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_invite(db_path: str, token: str) -> bool:
    conn = get_connection(db_path)
    cur = conn.execute("DELETE FROM invites WHERE token = ?", (token,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def regenerate_family_invite(db_path: str, family_id: int) -> str:
    conn = get_connection(db_path)
    conn.execute("DELETE FROM invites WHERE family_id = ? AND role = 'parent'", (family_id,))
    conn.commit()
    conn.close()
    return create_invite(db_path, family_id, "parent")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestInvites -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): invites table and accessors (SP3B)"
```

---

### Task 2: `create_family_with_owner` и `join_by_invite`

**Files:**
- Modify: `database.py`
- Test: `test/test_bot.py` (новый `TestRegistrationAccessors`)

**Interfaces:**
- Consumes: `create_invite`, `get_invite`, `add_member`, `get_member`, `create_family`.
- Produces:
  - `create_family_with_owner(db_path, telegram_id, name) -> int` (family_id; атомарно; идемпотентно)
  - `join_by_invite(db_path, token, telegram_id, name=None) -> dict | None` (`{family_id, role, name}` или None)

- [ ] **Step 1: Write the failing test**

```python
class TestRegistrationAccessors:
    def test_create_family_with_owner(self):
        from database import create_family_with_owner, get_member, get_family_invite
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        m = get_member(TEST_DB, 500)
        assert m["family_id"] == fid and m["role"] == "parent" and m["name"] == "Ивановы"
        assert get_family_invite(TEST_DB, fid) is not None

    def test_create_family_idempotent(self):
        from database import create_family_with_owner, get_member
        fid1 = create_family_with_owner(TEST_DB, 500, "Ивановы")
        fid2 = create_family_with_owner(TEST_DB, 500, "Другое")
        assert fid1 == fid2
        assert get_member(TEST_DB, 500)["family_id"] == fid1

    def test_join_parent_invite(self):
        from database import create_family_with_owner, get_family_invite, join_by_invite, get_member
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = get_family_invite(TEST_DB, fid)["token"]
        res = join_by_invite(TEST_DB, token, 600)
        assert res == {"family_id": fid, "role": "parent", "name": "Родитель"}
        assert get_member(TEST_DB, 600)["role"] == "parent"

    def test_join_child_card_uses_card_name(self):
        from database import (create_family_with_owner, create_invite,
                              join_by_invite, get_member, get_family)
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = create_invite(TEST_DB, fid, "child", "Маша")
        res = join_by_invite(TEST_DB, token, 700)
        assert res["role"] == "child" and res["name"] == "Маша"
        assert get_member(TEST_DB, 700)["name"] == "Маша"

    def test_join_unknown_token_returns_none(self):
        from database import join_by_invite
        assert join_by_invite(TEST_DB, "nope", 700) is None

    def test_join_is_idempotent(self):
        from database import create_family_with_owner, get_family_invite, join_by_invite, get_member
        fid = create_family_with_owner(TEST_DB, 500, "Ивановы")
        token = get_family_invite(TEST_DB, fid)["token"]
        join_by_invite(TEST_DB, token, 600)
        join_by_invite(TEST_DB, token, 600)
        import sqlite3
        conn = sqlite3.connect(TEST_DB)
        n = conn.execute("SELECT COUNT(*) FROM members WHERE telegram_id = 600").fetchone()[0]
        conn.close()
        assert n == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestRegistrationAccessors -q`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement in `database.py`**

```python
def create_family_with_owner(db_path: str, telegram_id: int, name: str) -> int:
    """Create a family with the caller as parent. Idempotent per telegram_id."""
    existing = get_member(db_path, telegram_id)
    if existing:
        return existing["family_id"]
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("INSERT INTO families (name) VALUES (?)", (name,))
        fid = cur.lastrowid
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, 'parent', ?)",
            (telegram_id, fid, name)
        )
        token = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO invites (token, family_id, role, name) VALUES (?, ?, 'parent', '')",
            (token, fid)
        )
        conn.commit()
        return fid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def join_by_invite(db_path: str, token: str, telegram_id: int,
                   name: Optional[str] = None) -> Optional[dict]:
    """Join a family by invite token. Returns {family_id, role, name} or None."""
    invite = get_invite(db_path, token)
    if not invite:
        return None
    role = invite["role"]
    member_name = name if name is not None else (
        invite["name"] or ("Родитель" if role == "parent" else "Ребёнок")
    )
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO members (telegram_id, family_id, role, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET family_id = excluded.family_id, "
            "role = excluded.role, name = excluded.name",
            (telegram_id, invite["family_id"], role, member_name)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"family_id": invite["family_id"], "role": role, "name": member_name}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestRegistrationAccessors -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py test/test_bot.py
git commit -m "feat(db): atomic create_family_with_owner and join_by_invite (SP3B)"
```

---

### Task 3: `_role` + `MemberMiddleware`

**Files:**
- Modify: `bot.py` (middleware, `_role`, регистрация middleware)
- Test: `test/test_bot.py` (новый `TestMemberMiddleware`)

**Interfaces:**
- Produces:
  - `_role(member: dict | None, uid: int) -> str` (`"parent"|"child"|"unknown"`)
  - `class MemberMiddleware(BaseMiddleware)` с `__call__`
- Consumes: `get_member` (через `_db`).

- [ ] **Step 1: Write the failing test**

```python
class TestMemberMiddleware:
    def test_role_from_member(self):
        import bot
        assert bot._role({"role": "parent"}, 999) == "parent"
        assert bot._role({"role": "child"}, 999) == "child"

    def test_role_fallback_to_env_for_family_one(self, monkeypatch):
        import bot
        monkeypatch.setattr(bot, "is_parent", lambda uid: uid == 222)
        monkeypatch.setattr(bot, "is_child", lambda uid: uid == 111)
        assert bot._role(None, 222) == "parent"
        assert bot._role(None, 111) == "child"
        assert bot._role(None, 555) == "unknown"

    def test_middleware_injects_member(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        captured = {}

        async def handler(event, data):
            captured.update(data)
            return "ok"

        event = MagicMock()
        event.from_user.id = 111
        with patch.object(bot, "get_member", return_value={"role": "child", "family_id": 1}):
            result = asyncio.run(bot.MemberMiddleware()(handler, event, {}))

        assert result == "ok"
        assert captured["member"] == {"role": "child", "family_id": 1}

    def test_middleware_none_when_no_user(self):
        import asyncio
        import bot
        from unittest.mock import MagicMock

        captured = {}

        async def handler(event, data):
            captured.update(data)

        event = MagicMock()
        event.from_user = None
        asyncio.run(bot.MemberMiddleware()(handler, event, {}))
        assert captured["member"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestMemberMiddleware -q`
Expected: FAIL — `bot._role` missing.

- [ ] **Step 3: Implement in `bot.py`**

Импорт: `from aiogram import BaseMiddleware` (добавить к существующему импорту aiogram); `get_member` в импорт из `database`.

```python
def _role(member, uid: int) -> str:
    """Роль участника: из БД (member), иначе fallback на .env (семья №1)."""
    if member:
        return member["role"]
    if is_parent(uid):
        return "parent"
    if is_child(uid):
        return "child"
    return "unknown"


def _is_parent_member(member, uid: int) -> bool:
    return _role(member, uid) == "parent"


class MemberMiddleware(BaseMiddleware):
    """Inject the caller's member row (role, family_id) from the DB."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        uid = getattr(user, "id", None)
        data["member"] = await _db(get_member, DB_PATH, uid) if uid else None
        return await handler(event, data)
```

Регистрация: после создания `router`/`dp` (рядом с `dp.include_router(router)`):

```python
member_middleware = MemberMiddleware()
router.message.middleware(member_middleware)
router.callback_query.middleware(member_middleware)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/test_bot.py::TestMemberMiddleware -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): MemberMiddleware and _role helper (SP3B)"
```

---

### Task 4: Ролевые проверки из БД в хендлерах

**Files:**
- Modify: `bot.py` (все ролевые проверки в callback/message хендлерах)
- Test: `test/test_bot.py` (новый `TestHandlerRolesFromDb`)

**Interfaces:**
- Consumes: `_is_parent_member`, `_role`.
- Изменяемые проверки: вместо `if not is_parent(callback.from_user.id):` → `if not _is_parent_member(member, callback.from_user.id):`, добавив `member=None` в сигнатуру хендлера.

**Затронутые хендлеры (проверить все):** `cb_edit_any` (761), `cb_delete_confirm` (792), `cb_delete` (813), `cb_settings` (867), `cb_reminders` (906), `cb_rem_set` (915), `cb_change_target` (974), `cb_export` (1042), `cb_export_all` (1070), `cb_export_month` (1083), `cb_backup` (1106), `cb_summary` (1305), `cb_weekly` (1350), `cb_edit_last` (680), а также `cmd_start`/`cmd_cancel`/`catch_all` и `send_main_menu`/`build_status_block`-вызовы, где роль влияет на клавиатуру.

- [ ] **Step 1: Write the failing test**

```python
class TestHandlerRolesFromDb:
    def _cb(self, uid, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_new_family_parent_allowed_via_member(self):
        """A parent of a new family (not in .env) may open settings."""
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(999, "settings")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_effective_target", return_value=260), \
             patch.object(bot, "get_all_measurements", return_value=[]):
            asyncio.run(bot.cb_settings(cb, member={"role": "parent", "family_id": 2}))

        assert "Настройки" in sent.get("text", "")

    def test_unknown_user_rejected(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(999, "settings")
        with patch.object(bot, "is_parent", return_value=False), \
             patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cb_settings(cb, member=None))

        cb.message.answer.assert_not_called()
        cb.answer.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestHandlerRolesFromDb -q`
Expected: FAIL — `cb_settings() got an unexpected keyword argument 'member'`.

- [ ] **Step 3: Implement in `bot.py`**

Механически по каждому затронутому хендлеру: добавить параметр `member=None` после `state`/последнего параметра и заменить `is_parent(<uid>)` на `_is_parent_member(member, <uid>)`. Пример для `cb_settings`:

```python
@router.callback_query(F.data == "settings")
async def cb_settings(callback: types.CallbackQuery, member=None):
    if not _is_parent_member(member, callback.from_user.id):
        await callback.answer("⚠️ Только для родителей.", show_alert=True)
        return
    ...
```

Для `send_main_menu`/`kb_main` роль вычислять через `_role(member, user_id)` при передаче `is_parent_user`. В `cmd_start` заменить `if not is_parent(uid) and not is_child(uid)` на `_role(member, uid) == "unknown"` и передавать `member` в `send_main_menu`. Аналогично `cmd_cancel` и `catch_all`.

Запусти `grep -n "is_parent(\|is_child(" bot.py` и убедись, что в рантайм-путях остались только вхождения внутри `_role` (fallback) и `_user_display_name` (его переведи на роль из `member`, если передан). `is_parent`/`is_child` в `config.py` остаются.

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS — все существующие тесты (вызовы без `member` работают через fallback) + новые.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "refactor(bot): handler role checks use member from DB with env fallback (SP3B)"
```

---

### Task 5: Регистрация в боте

**Files:**
- Modify: `bot.py` (`cmd_start`, FSM-состояния регистрации, deep-link)
- Test: `test/test_bot.py` (новый `TestRegistrationFlow`)

**Interfaces:**
- Consumes: `create_family_with_owner`, `join_by_invite`, `get_member`.
- Produces:
  - FSM `Registration`: `entering_family_name`, `entering_invite_code`, `adding_child_name`.
  - Callback `reg_create`, `reg_join`.
  - `/start <token>` deep-link.

- [ ] **Step 1: Write the failing test**

```python
class TestRegistrationFlow:
    def test_unknown_user_sees_registration(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock

        msg = MagicMock()
        msg.from_user.id = 999
        msg.text = "/start"
        msg.answer = AsyncMock()

        state = MagicMock()
        state.clear = AsyncMock()

        with __import__("unittest.mock").mock.patch.object(bot, "is_parent", return_value=False), \
             __import__("unittest.mock").mock.patch.object(bot, "is_child", return_value=False):
            asyncio.run(bot.cmd_start(msg, state, member=None))

        sent = " ".join(str(c.args[0]) for c in msg.answer.await_args_list if c.args)
        assert "семью" in sent.lower() or "код" in sent.lower()

    def test_deep_link_joins(self):
        import asyncio
        import bot
        from unittest.mock import AsyncMock, MagicMock, patch

        msg = MagicMock()
        msg.from_user.id = 700
        msg.text = "/start TOKEN123"
        msg.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()

        with patch.object(bot, "join_by_invite",
                          return_value={"family_id": 2, "role": "child", "name": "Маша"}) as m, \
             patch.object(bot, "send_main_menu", new=AsyncMock()):
            asyncio.run(bot.cmd_start(msg, state, member=None))
        m.assert_called_once_with(bot.DB_PATH, "TOKEN123", 700)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestRegistrationFlow -q`
Expected: FAIL — `cmd_start() got unexpected keyword 'member'` / нет ветки регистрации.

- [ ] **Step 3: Implement in `bot.py`**

Добавь FSM-состояния:

```python
class Registration(StatesGroup):
    entering_family_name = State()
    entering_invite_code = State()
```

`cmd_start`:

```python
@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext, member=None):
    uid = message.from_user.id
    # Deep-link: /start <token>
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        token = parts[1].strip()
        result = await _db(join_by_invite, DB_PATH, token, uid)
        if result:
            await message.answer(f"✅ Вы вошли в семью как {result['role']}.", parse_mode="Markdown")
            await state.clear()
            await send_main_menu(message, uid)
            return
        await message.answer("❌ Код не найден. Попробуйте ещё раз.")
        # fall through to registration screen
    role = _role(member, uid)
    if role == "unknown":
        await message.answer(
            "👋 Добро пожаловать в Пикфлоуметр!\n\n"
            "Создайте семью или войдите по коду приглашения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Создать семью", callback_data="reg_create")],
                [InlineKeyboardButton(text="🔑 Войти по коду", callback_data="reg_join")],
            ]),
        )
        return
    who = "👨‍👧 Родитель" if role == "parent" else f"👶 {escape_md(CHILD_NAME)}"
    await message.answer(f"✅ Привет! Вы вошли как *{who}*", parse_mode="Markdown")
    await send_main_menu(message, uid)
    await state.clear()
```

Хендлеры `reg_create`/`reg_join` (FSM), `input_family_name` (создаёт семью, показывает invite), `input_invite_code` (join). Добавь их текст по образцу существующих FSM-хендлеров. Неизвестные текстовые вводы → `_FSM_HINTS` для новых состояний.

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): self-service registration and invite deep-links (SP3B)"
```

---

### Task 6: Экраны «Участники» и «Дети»

**Files:**
- Modify: `bot.py` (кнопки в `kb_settings`, хендлеры участников/детей)
- Test: `test/test_bot.py` (новый `TestFamilyManagement`)

**Interfaces:**
- Consumes: `get_family_invite`, `regenerate_family_invite`, `list_family_children`, `list_child_cards`, `create_invite`, `delete_invite`.
- Produces: callbacks `members`, `add_child`, `del_child_`, `regen_invite`.

- [ ] **Step 1: Write the failing test**

```python
class TestFamilyManagement:
    def _cb(self, uid, data):
        from unittest.mock import AsyncMock, MagicMock
        cb = MagicMock()
        cb.data = data
        cb.from_user.id = uid
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.delete = AsyncMock()
        return cb

    def test_members_screen_shows_invite(self):
        import asyncio
        import bot
        from unittest.mock import patch

        cb = self._cb(500, "members")
        sent = {}

        async def fake_respond(callback, text, kb=None, parse_mode="Markdown"):
            sent["text"] = text

        with patch.object(bot, "respond", side_effect=fake_respond), \
             patch.object(bot, "get_family_invite",
                          return_value={"token": "ABC", "role": "parent"}), \
             patch.object(bot, "list_family_children", return_value=[]), \
             patch.object(bot, "list_child_cards", return_value=[]):
            asyncio.run(bot.cb_members(cb, member={"role": "parent", "family_id": 2}))

        assert "ABC" in sent.get("text", "")

    def test_add_child_creates_card(self):
        import asyncio
        import bot
        from unittest.mock import patch

        msg = __import__("unittest.mock").MagicMock()
        msg.from_user.id = 500
        msg.text = "Маша"
        msg.answer = __import__("unittest.mock").AsyncMock()
        state = __import__("unittest.mock").MagicMock()
        state.get_data = __import__("unittest.mock").AsyncMock(
            return_value={"family_id": 2})
        state.clear = __import__("unittest.mock").AsyncMock()

        with patch.object(bot, "create_invite", return_value="TOK") as c:
            asyncio.run(bot.input_child_name(msg, state, member={"role": "parent", "family_id": 2}))
        c.assert_called_once_with(bot.DB_PATH, 2, "child", "Маша")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_bot.py::TestFamilyManagement -q`
Expected: FAIL — `cb_members` missing.

- [ ] **Step 3: Implement in `bot.py`**

- В `kb_settings` добавь кнопки «👨‍👩‍👧 Участники» (`members`) и «🧒 Дети» (`children`).
- `cb_members` (только родитель): показывает список членов семьи, invite-код (`get_family_invite`), кнопки «🔑 Перегенерировать» (`regen_invite`), «⬅️ Назад».
- `cb_children` (только родитель): список карточек `list_child_cards`, кнопки «➕ Добавить» (`add_child`), «🗑️ <имя>» (`del_child_<token>`), «⬅️ Назад».
- `cb_add_child` → FSM `Registration.adding_child_name`, затем `input_child_name` читает имя, вызывает `create_invite(family_id, "child", name)` и показывает токен.
- `cb_del_child` парсит токен (safe parse), вызывает `delete_invite`, перерисовывает экран.
- `cb_regen_invite` вызывает `regenerate_family_invite(family_id)` и показывает новый токен.

Все — через `_is_parent_member(member, uid)`, `family_id` из `member`.

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py test/test_bot.py
git commit -m "feat(bot): family members and children management screens (SP3B)"
```

---

### Task 7: Mini App — роль и семья из БД

**Files:**
- Modify: `web/api.py` (`_resolve_user`)
- Test: `test/test_webapp_api.py` (новый `TestWebRolesFromDb`)

**Interfaces:**
- Consumes: `get_member`.
- Produces: `_resolve_user` возвращает `{"user", "role", "family_id", "member"}`; неизвестный → 403.

- [ ] **Step 1: Write the failing test**

```python
class TestWebRolesFromDb:
    def test_member_of_new_family_gets_role_from_db(self):
        from database import create_family_with_owner
        _setup_db()
        fid = create_family_with_owner(TEST_DB, 999, "Новые")
        body = _client().get("/api/me", headers=_auth(999)).json()
        assert body["role"] == "parent"

    def test_stranger_forbidden(self):
        _setup_db()
        r = _client().get("/api/me", headers=_auth(888))
        assert r.status_code == 403
```

> Примечание: `_auth` из `test_webapp_api.py` строит корректный initData. Тест `create_family_with_owner` пишет в `TEST_DB`, который `_setup_db()` инициализирует.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/test_webapp_api.py::TestWebRolesFromDb -q`
Expected: FAIL — `999` не в `.env` → 403.

- [ ] **Step 3: Implement in `web/api.py`**

```python
    def _resolve_user(init_data: str | None) -> dict:
        token = getattr(config, "BOT_TOKEN", "") or ""
        if not token or not init_data:
            raise HTTPException(403, "Нет доступа")
        user = get_user_from_init_data(init_data, token)
        if not user:
            raise HTTPException(403, "Нет доступа")
        uid = user.get("id")
        member = get_member(config.DB_PATH, uid)
        if member:
            role = member["role"]
            family_id = member["family_id"]
        else:
            # Fallback for family #1 before its members are read (defensive).
            if uid == getattr(config, "CHILD_ID", 0):
                role, family_id = "child", 1
            elif uid in (getattr(config, "PARENT_IDS", []) or []):
                role, family_id = "parent", 1
            else:
                raise HTTPException(403, "Нет доступа")
        return {"user": user, "role": role, "family_id": family_id, "member": member}
```

Импортировать `get_member`. Далее по возможности использовать `auth["family_id"]`/`auth["member"]` в эндпоинтах вместо env (в 2B минимально: `/api/me` отдаёт роль уже из БД; полный перенос family_id в каждый эндпоинт — 2C, т.к. активный ребёнок — 2C).

- [ ] **Step 4: Run full suite**

Run: `pytest test/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/api.py test/test_webapp_api.py
git commit -m "feat(web): resolve role and family from members table (SP3B)"
```

---

### Task 8: Документация и полная регрессия

**Files:**
- Modify: `PROJECT.md`, `wiki.md`, `docs/superpowers/specs/2026-09-17-todo-plan.md`

- [ ] **Step 1: Update `PROJECT.md`**
  - Таблица `invites`; `SCHEMA_VERSION = 3`.
  - Раздел про регистрацию: создание семьи, invite-код, карточки детей, deep-link.
  - Роли определяются из `members`; `.env` — только сидинг семьи №1/fallback.

- [ ] **Step 2: Update `wiki.md`**
  - Поток `/start` (регистрация/вход), `MemberMiddleware`, `_role`, экраны участников/детей.

- [ ] **Step 3: Update `docs/superpowers/specs/2026-09-17-todo-plan.md`**
  - Отметить 2B выполненным со ссылкой на spec/plan; оставить 2C/2D.

- [ ] **Step 4: Full verification**

Run:
```bash
source venv/bin/activate && python -m pytest test/ -q && python -m pyflakes bot.py database.py config.py report.py web/*.py && python -m compileall -q bot.py database.py config.py report.py web && echo ALL_GREEN
```
Expected: `ALL_GREEN`.

- [ ] **Step 5: Commit**

```bash
git add PROJECT.md wiki.md docs/superpowers/specs/2026-09-17-todo-plan.md
git commit -m "docs: self-service registration and DB roles (SP3B)"
```

---

## Self-Review

**1. Spec coverage:**
- Таблица `invites` + доступоры → Task 1 ✅
- `create_family_with_owner`/`join_by_invite` → Task 2 ✅
- Роли из БД (middleware + `_role`) → Task 3, 4, 7 ✅
- Регистрация/инвайты/deep-link → Task 5 ✅
- Карточки детей и управление участниками → Task 6 ✅
- Mini App роль/семья из БД → Task 7 ✅
- Документация/версия → Task 8 ✅
- Вне scope (2C выбор ребёнка, doctor, PostgreSQL) — не включено ✅

**2. Placeholder scan:** конкретные тесты и код приведены; описательные шаги (Task 6/8) содержат имена функций и callbacks. «По образцу существующих» для FSM-хендлеров Task 5 отсылает к уже существующим FSM в файле (waiting_note) — исполнитель видит их в коде.

**3. Type consistency:**
- `_role(member, uid)` → `"parent"|"child"|"unknown"`; используется в Task 3, 4, 5.
- `member` — `dict` с ключами `role`, `family_id` (из `members`); согласовано в Tasks 3–7.
- `create_invite(db, family_id, role, name="")`, `join_by_invite(...) -> {family_id, role, name}` — согласовано Task 1/2/5.
- `SCHEMA_VERSION=3` определён Task 1, используется Task 8.

---

## Execution Handoff

План готов: `docs/superpowers/plans/2026-09-17-sp3b-registration.md`.

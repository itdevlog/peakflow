# SP3B — Регистрация, инвайты и роли из БД

> **Дата:** 2026-09-17
> **Подпроект:** Фаза 2B
> **Статус:** дизайн на ревью
> **Зависимости:** SP3A (схема v2, смёржена в main, 266 тестов)
> **Не входит:** выбор активного ребёнка (2C), роль doctor (Фаза 4), PostgreSQL (Фаза 3)

---

## 1. Цель

Перевести бота с «семьи из `.env`» на самопропускную регистрацию:

- неизвестный пользователь в `/start` может **создать семью** или **войти по коду**;
- родитель создаёт **карточки детей** и приглашает второго родителя;
- проверки роли в боте и Mini App читают **роль из БД** (`members`), а не `CHILD_ID`/`PARENT_IDS` из `.env`;
- текущая семья №1 (засеяна SP3A) уже состоит из участников — регистрация ей не показывается, поведение не меняется.

**Успех:** новая семья регистрируется в боте без правки `.env`; данные семей изолированы; семья №1 работает как раньше; все тесты зелёные.

---

## 2. Схема v3 — приглашения

```sql
CREATE TABLE invites (
    token      TEXT PRIMARY KEY,
    family_id  INTEGER NOT NULL REFERENCES families(id),
    role       TEXT NOT NULL CHECK(role IN ('parent', 'child')),
    name       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_invites_family ON invites(family_id);
```

- **Родительское приглашение семьи** — одна строка `role='parent'`, `name=''`, многоразовая, бессрочная.
- **Карточка ребёнка** — строка `role='child'`, `name='Маша'`; ребёнок входит по этому токену и становится участником с этим именем.
- **Отзыв:** регенерация родительского токена (delete + create) или удаление карточки ребёнка.
- Токен: `secrets.token_urlsafe(8)` (короткий, удобно диктовать), уникальность через PK; коллизия → retry.

`SCHEMA_VERSION` → 3. Миграция идемпотентная (создаёт `invites`, генерирует родительский токен для семьи №1 при наличии).

---

## 3. Доступоры (`database.py`)

```python
create_invite(db_path, family_id, role, name="") -> str      # возвращает токен
get_invite(db_path, token) -> dict | None                    # {token, family_id, role, name}
get_family_invite(db_path, family_id) -> dict | None         # role='parent'
list_child_cards(db_path, family_id) -> list[dict]           # role='child'
delete_invite(db_path, token) -> bool
regenerate_family_invite(db_path, family_id) -> str
create_family_with_owner(db_path, telegram_id, name) -> int  # семья + владелец-родитель + родительский инвайт
join_by_invite(db_path, token, telegram_id, name=None) -> dict | None
    # None если токен не найден; иначе создаёт/обновляет member, возвращает {family_id, role, name}
```

`create_family_with_owner` и `join_by_invite` — атомарные (одна транзакция), idempotent по telegram_id (повторный вход не дублирует).

---

## 4. Роли из БД (bot.py)

**Проблема:** `config.is_parent/is_child` читают `.env`; они вызываются в ~18 хендлерах синхронно. Прямая замена на синхронный запрос к БД вернула бы блокирующие вызовы, убранные в Фазе 1.

**Решение:** aiogram-middleware `MemberMiddleware` на `router.message`/`router.callback_query`:

```python
async def __call__(self, handler, event, data):
    uid = getattr(event.from_user, "id", None)
    data["member"] = await asyncio.to_thread(get_member, DB_PATH, uid) if uid else None
    return await handler(event, data)
```

- Хендлеры объявляют параметр `member: dict | None = None` (aiogram передаёт его как kwarg из `data`).
- Единый хелпер вместо разрозненных проверок:

```python
def _role(member: dict | None, uid: int) -> str:
    """Роль участника: из БД (member), иначе fallback на .env (семья №1)."""
    if member:
        return member["role"]
    if is_parent(uid):
        return "parent"
    if is_child(uid):
        return "child"
    return "unknown"
```

- `_role(member, uid) == "parent"` заменяет `is_parent(uid)` в проверках доступа.
- **Обратная совместимость тестов:** прямой вызов хендлера без `member` (как в текущих тестах) даёт `member=None` → `_role` падает на `.env`-проверку, т.е. поведение семьи №1 сохраняется; менять существующие тесты не требуется.
- `_user_display_name`, `kb_main(is_parent_user)` получают роль через `_role`.
- `config.is_parent`/`is_child` остаются как fallback для семьи №1 и для сидинга; для новых семей роли идут только из `members`.

**Совместимость:** для семьи №1 `member` уже существует после SP3A — все проверки дают те же результаты.

---

## 5. Регистрация (bot.py)

`/start`:
- `member` есть → главное меню (как сейчас).
- `member` нет → экран:
  - **«🏠 Создать семью»** → FSM: спросить имя семьи → `create_family_with_owner` (инициатор становится родителем) → показать родительский invite-код и главное меню.
  - **«🔑 Войти по коду»** → FSM: ввести токен → `join_by_invite`:
    - `role='parent'` → участник-родитель;
    - `role='child'` → участник-ребёнок с именем карточки;
    - токен не найден → ошибка, повтор.

Deep-link: `/start <token>` → сразу `join_by_invite(token)`.

**Родительские настройки («⚙️ Настройки»):**
- «👨‍👩‍👧 Участники»: список членов семьи, кнопка «🔑 Пригласить родителя» (показать/перегенерировать токен).
- «🧒 Дети»: список карточек, «➕ Добавить ребёнка» (FSM: имя → карточка + токен), «🗑️ Удалить карточку».

---

## 6. Mini App (`web/api.py`, `web/auth.py`)

- `_resolve_user`: после валидации initData → `get_member(uid)`; роль из `member["role"]`; `family_id` из `member["family_id"]`.
- Не-член → 403 (для не-зарегистрированных Mini App недоступен).
- Все запросы к данным используют `member["family_id"]`; `child_id` — активного ребёнка (в 2B: первый ребёнок семьи; выбор — 2C).
- `CHILD_NAME`, `CHILD_ID`, `PARENT_IDS` больше не источник ролей в web.

---

## 7. Критерии приёмки

- [ ] Неизвестный пользователь видит экран регистрации; создаёт семью и становится родителем.
- [ ] Второй родитель входит по коду; ребёнок входит по карточке с именем.
- [ ] Повторный вход идемпотентен (не дублирует члена).
- [ ] Роли в боте и Mini App определяются из БД; семья №1 работает без изменений.
- [ ] Данные семей изолированы (уже покрыто SP3A; добавить проверку для новых семей).
- [ ] Родитель может перегенерировать код (старый перестаёт действовать) и удалить карточку ребёнка.
- [ ] Все существующие тесты зелёные; новые тесты на регистрацию/инвайты/роли.
- [ ] `pyflakes` + `compileall` чисто.
- [ ] Документация обновлена.

---

## 8. Тестирование (TDD)

1. `invites`: create/get/list/delete/regenerate; уникальность токена; роль/имя.
2. `create_family_with_owner`: семья + родитель + invite; идемпотентность.
3. `join_by_invite`: parent/child/неизвестный токен; повторный вход; имя ребёнка из карточки.
4. Middleware: `member` инжектится; неизвестный → None.
5. Хендлеры: родительские проверки по `member`; экран регистрации для неизвестных.
6. Web: роль и family_id из БД; не-член → 403.
7. Регрессия: семья №1, полный прогон.

---

## 9. Риски и решения

| Риск | Решение |
|------|---------|
| Массовая правка ~18 хендлеров | middleware + `member` в data; правки механические (`is_parent(uid)` → `member["role"]`) |
| Возврат блокирующих вызовов БД | lookup только в middleware через `asyncio.to_thread`, один раз на update |
| Семья №1 ломается | миграция не трогает существующих членов; тест регрессии |
| Токен угадывается | `secrets.token_urlsafe(8)` + PK; при желании позже TTL |
| Двойной вход по карточке двумя аккаунтами | допустимо (общая карточка); при необходимости — 2C |

---

## 10. Вне scope 2B

- Выбор активного ребёнка в UI (2C).
- Несколько детей на графиках/сводках (2C).
- Роль doctor, PostgreSQL, биллинг.
- TTL/одноразовость инвайтов.

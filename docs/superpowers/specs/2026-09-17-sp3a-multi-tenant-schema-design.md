# SP3A — Мульти-тенантная схема и миграция v1→v2 (SQLite)

> **Дата:** 2026-09-17
> **Подпроект:** Фаза 2A (фундамент мульти-тенанта)
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–1 (закоммичены, 236 тестов)
> **Не входит (2B–2D):** регистрация/инвайты в боте, выбор активного ребёнка, изоляция в UI

---

## 1. Цель

Заложить мульти-тенантную схему данных на SQLite так, чтобы:

- данные разных семей были изолированы на уровне схемы и запросов;
- существующая прод-БД (183 замера, дети Маша/Матвей, родители) переносилась в первую семью без потерь;
- бот и Mini App продолжали работать в single-family режиме, пока 2B не добавит регистрацию;
- PostgreSQL (Фаза 3) можно было подключить поверх этой же модели без переделки домена.

**Успех:** после миграции все текущие функции работают как раньше, тесты изоляции доказывают, что данные семьи A не видны семье B, прод-данные на месте.

---

## 2. Терминология

- **Семья (family)** — тенант. Владеет участниками и замерами.
- **Участник (member)** — Telegram-пользователь с ролью `parent` или `child`, привязанный к одной семье.
- **Ребёнок (child)** — участник с ролью `child`; замеры принадлежат конкретному ребёнку.
- **Активный ребёнок** — текущий контекст отображения (в 2A всегда единственный/первый; выбор — в 2C).

---

## 3. Схема v2

### 3.1 Новые таблицы

```sql
CREATE TABLE families (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE members (
    telegram_id INTEGER PRIMARY KEY,
    family_id   INTEGER NOT NULL REFERENCES families(id),
    role        TEXT NOT NULL CHECK(role IN ('parent', 'child')),
    name        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_members_family ON members(family_id);
```

### 3.2 Изменения существующих таблиц

```sql
-- measurements: user_id → child_id, + family_id
-- SQLite не переименовывает/не меняет столбцы в CHECK через ALTER, поэтому
-- таблица перестраивается паттерном new → copy → drop → rename:
--   1. ALTER TABLE measurements RENAME TO measurements_v1;
--   2. CREATE TABLE measurements (... child_id ..., family_id ..., CHECK(time_of_day
--      IN ('morning','evening','unknown')));
--   3. INSERT INTO measurements (...) SELECT ... FROM measurements_v1
--      (child_id из user_id/child_id, family_id = COALESCE(family_id, 1));
--   4. DROP TABLE measurements_v1.
-- Свежая БД создаёт v2-таблицу сразу (rebuild — no-op).
CREATE INDEX idx_meas_family_child_time ON measurements(family_id, child_id, measured_at);

-- settings: ключ теперь в разрезе семьи
-- v1: settings(key TEXT PK, value TEXT)
-- v2: settings(family_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(family_id, key))
--     перенос: family_id = 1

-- reminders_sent: в разрезе ребёнка
-- v1: reminders_sent(date TEXT PK, флаги...)
-- v2: reminders_sent(child_id INTEGER, date TEXT, флаги..., PRIMARY KEY(child_id, date))
--     перенос: child_id = CHILD_ID (единственный ребёнок v1)
```

> **Реализация v2 `settings`/`reminders_sent`:** SQLite не умеет менять PK через ALTER, поэтому создаём новую таблицу, копируем данные из старой и переименовываем (стандартный паттерн `*_new` → `DROP old` → `RENAME`). Всё в одной транзакции.

### 3.3 Итоговые таблицы v2

| Таблица | PK | Ключевые колонки |
|---------|-----|------------------|
| `families` | id | name, created_at |
| `members` | telegram_id | family_id, role, name |
| `measurements` | id | family_id, child_id, pef_value, time_of_day, measured_at, added_by, note, source |
| `settings` | (family_id, key) | value |
| `reminders_sent` | (child_id, date) | morning_reminder, evening_reminder, weekly_report, child_*, auto_* |

Legacy `users` и `parent_child_links` **остаются** (не удаляем) как источник сидинга; после миграции код их не читает. Удаление — отдельным решением позже.

---

## 4. Миграция v1→v2

`init_db` при `user_version < 2` выполняет:

1. **Бэкап-хук:** если БД не пустая и версия < 2 — делать `backup_db` в `*.v1.bak` перед изменениями (защита прод-данных). При ошибке бэкапа — прервать миграцию.
2. Создать `families`, `members`.
3. **Сидинг семьи №1** (id=1, name=«Семья»):
   - участники из `.env` (`CHILD_ID`/`PARENT_IDS`/`CHILD_NAME`) upsert-ятся первыми и **авторитетны** для перечисленных ID;
   - затем legacy `users` добивает только те ID, которых нет в конфиге (`INSERT OR IGNORE`): `role`, `first_name`/`child_name` → `members.name`; `parent_child_links` не нужен, т.к. один тенант;
   - если ни legacy, ни `.env` — семью всё равно создаём (id=1), участников нет (новая установка ждёт 2B).
4. Перестроить `measurements` (`user_id`→`child_id`, `family_id=1` для всех строк).
5. Перестроить `settings` и `reminders_sent` под новые PK, перенеся данные в семью №1 / `CHILD_ID`.
6. `PRAGMA user_version = 2`.
7. Идемпотентность: повторный запуск ничего не ломает (проверка версии + `IF NOT EXISTS`).

Значения по умолчанию, если сидинг из `.env` невозможен: не падать, оставить пустые таблицы участников.

---

## 5. Data layer (database.py)

Все функции, работающие с замерами/настройками, становятся tenant-aware.
**Согласованное отклонение от исходного дизайна:** `family_id` идёт **последним**
параметром со значением по умолчанию `DEFAULT_FAMILY_ID (1)`, а `child_id` занимает
прежнюю позицию `user_id` — ради обратной совместимости 300+ call-sites. Исключение:
`mark_reminder_sent`/`was_reminder_sent`, где `child_id` обязателен (входит в PK).
Итоговые сигнатуры (публичный контракт):

```python
# Члены и семьи
get_member(db, telegram_id) -> dict | None          # {telegram_id, family_id, role, name, created_at}
get_family(db, family_id) -> dict | None
create_family(db, name) -> int
add_member(db, telegram_id, family_id, role, name)
list_family_children(db, family_id) -> list[dict]

# Замеры: user_id → child_id, добавлен family_id (последним, дефолт 1)
get_today_measurements(db, child_id, family_id=1)
get_all_measurements(db, child_id, include_auto=False, family_id=1)
get_recent_measurements(db, child_id, limit=2, family_id=1)
get_previous_of_tod(db, child_id, time_of_day, before_id, family_id=1)
add_or_replace_measurement(db, pef, time_of_day, child_id, added_by, force=False, source="manual", family_id=1)
# settings / reminders — по (family_id, ...) последним; напоминания — по child_id
get_setting(db, key, default="", family_id=1)
set_setting(db, key, value, family_id=1)
get_effective_target(db, fallback, family_id=1)
get_reminder_hours(db, family_id=1)
mark_reminder_sent(db, date_str, reminder_type, child_id)   # child_id обязателен
was_reminder_sent(db, date_str, reminder_type, child_id)    # child_id обязателен
```

**Совместимость:** на время 2A–2B, пока нет выбора семьи, бот и web передают `family_id = DEFAULT_FAMILY_ID (1)` по умолчанию и `child_id = CHILD_ID` из `.env`. Функции с `family_id` по умолчанию не должны молча смешивать семьи.

---

## 6. Критерии приёмки

- [ ] `init_db` на чистой БД создаёт схему v2, `user_version = 2`.
- [ ] `init_db` на копии прод-БД: 183 замера сохранены, `family_id=1`, `child_id` = прежний `user_id`; участники перенесены; `settings`/`reminders_sent` сохранены.
- [ ] Повторный `init_db` идемпотентен.
- [ ] Тест изоляции: замеры семьи A не возвращаются запросами семьи B (для measurements, settings, reminders).
- [ ] Все 236 существующих тестов проходят (после адаптации вызовов data layer).
- [ ] `pyflakes` + `compileall` чисто.
- [ ] Бэкап `*.v1.bak` создаётся перед миграцией непустой v1-БД.
- [ ] Документация (`PROJECT.md`, `wiki.md`, план) обновлена.

---

## 7. Тестирование (TDD)

1. Миграция пустой БД → v2, схема корректна.
2. Миграция БД v1 с данными (фикстура из старой схемы) → данные на месте, версия 2.
3. Миграция прод-подобной БД (legacy `users` + 183 замера) → первая семья, участники.
4. Идемпотентность: двойной `init_db`.
5. Изоляция семей по measurements/settings/reminders.
6. Регрессия: полный прогон существующих тестов.

---

## 8. Риски и решения

| Риск | Решение |
|------|---------|
| Потеря прод-данных | Обязательный `backup_db` перед миграцией; миграция в транзакции; тест на копии прод-БД |
| SQLite не меняет PK через ALTER | Паттерн новая таблица → copy → drop → rename |
| Расхождение `CHILD_ID` из `.env` и legacy `users` | **Конфиг (`.env`) авторитетен** для всех ID, которые в нём перечислены; legacy `users` лишь добивает ID, которых в конфиге нет (`INSERT OR IGNORE`). Причина: legacy `users` устаревает (в прод-БД ID `35641953` записан ребёнком, хотя live `.env` включает его в `PARENT_IDS` и он владеет 126 замерами, а роли сейчас читаются из env) |
| Поломка всех вызовов data layer | Отдельный подпроект 2A покрывает только слой данных + миграцию; хендлеры адаптируются в 2A минимально (передавая family_id=1) |
| Преждевременный мульти-ребёнок | `child_id` в схеме есть, выбор ребёнка — 2C |

---

## 9. Явно вне scope 2A

- Регистрация/инвайты, экраны `/start` (2B).
- Выбор активного ребёнка в UI (2C).
- Роль `doctor` (Фаза 4).
- PostgreSQL и Docker (Фаза 3).
- Удаление legacy-таблиц.

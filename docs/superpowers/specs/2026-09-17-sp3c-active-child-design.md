# SP3C — Активный ребёнок и tenant-aware данные

> **Дата:** 2026-09-17
> **Подпроект:** Фаза 2C
> **Статус:** дизайн на ревью
> **Зависимости:** SP3A/SP3B (схема v3, смёржены в main, 354 теста)
> **Не входит:** роль doctor (Фаза 4), PostgreSQL (Фаза 3), аналитика по всем детям сразу

---

## 1. Цель

Убрать временный gate (участники семей ≠ №1 без доступа к данным) и сделать данные
tenant-aware: каждое обращение к замерам/настройкам/напоминаниям использует
`family_id` вызывающего и **активного ребёнка**. Родитель с несколькими детьми может
переключать активного ребёнка; ребёнок всегда видит себя.

**Успех:** новая семья полноценно пользуется ботом и Mini App; данные семей и детей
изолированы; семья №1 работает как раньше; все тесты зелёные.

---

## 2. Схема v4

```sql
ALTER TABLE members ADD COLUMN active_child_id INTEGER;  -- выбор родителя
```

`SCHEMA_VERSION` → 4. Миграция идемпотентна (`ALTER TABLE ... ADD COLUMN` в try/except).
Для ребёнка `active_child_id` не используется (активный ребёнок = сам ребёнок).

---

## 3. Доступоры (`database.py`)

```python
set_active_child(db_path, telegram_id, child_id) -> bool
    # проверяет, что child_id — ребёнок той же семьи; обновляет members.active_child_id
resolve_active_child(db_path, member) -> int | None
    # child → member["telegram_id"]
    # parent → active_child_id, если валиден, иначе первый ребёнок семьи, иначе None
count_family_children(db_path, family_id) -> int
```

`resolve_active_child` уже опирается на `list_family_children`.

---

## 4. Резолвинг контекста (`bot.py`)

Единый хелпер вместо глобального `CHILD_ID`:

```python
async def _ctx(member) -> tuple[int, int | None]:
    """(family_id, active_child_id). member=None → семья №1 из .env (fallback)."""
    if not member:
        return DEFAULT_FAMILY_ID, CHILD_ID     # env fallback: текущее поведение
    family_id = member["family_id"]
    if member["role"] == "child":
        return family_id, member["telegram_id"]
    return family_id, await _db(resolve_active_child, DB_PATH, member)

def _child_name(member, child_id) -> str     # member=None → CHILD_NAME; иначе имя ребёнка
async def _family_parents(member, family_id) -> list[int]
    # member=None → env PARENT_IDS; иначе родители семьи из members
```

- `member=None` (существующие тесты, семья №1) даёт ровно текущее поведение —
  регрессия исключена тем же приёмом, что в 2B.
- Все хендлеры, работающие с данными, берут `family_id, child_id = await _ctx(member)`
  и передают их в функции `database.py` (сигнатуры уже принимают `family_id`,
  а `child_id` занимает прежнюю позицию).
- Если `child_id is None` (у семьи нет детей-участников) → подсказка «добавьте ребёнка»
  (кнопка «🧒 Дети»), данные не показываются.

**Уведомления** (`_persist_measurement`, эскалации, недельный отчёт): получатели —
`await _family_parents(member, family_id)` вместо `PARENT_IDS`; имя — `_child_name`.

---

## 5. Снятие временного gate (2B)

- `MemberMiddleware`: убрать deny-ветку для `family_id != DEFAULT_FAMILY_ID`
  (оставить только регистрацию как обычно).
- `cmd_start`/`cmd_cancel`/`catch_all`/`send_main_menu`: убрать `REG_SOON_MESSAGE`-гейты;
  `send_main_menu` снова строит меню для активного ребёнка (или подсказку «добавьте ребёнка»).
- `web/api.py` `_resolve_user`: убрать 403 для `family_id != 1`.

---

## 6. Выбор ребёнка (bot)

- Если `count_family_children(family_id) > 1` — в главном меню кнопка
  «🧒 Ребёнок: <имя>» (`callback=pick_child`).
- `cb_pick_child` — список детей (`set_child_<telegram_id>`), выбор → `set_active_child`,
  возврат в главное меню.
- При 1 ребёнке кнопки нет. У ребёнка кнопки нет (он и есть активный).
- «⚙️ Настройки» → «🧒 Дети»: существующие карточки + отметка активного.

---

## 7. Mini App

- `_resolve_user` возвращает `active_child_id` (через `resolve_active_child`) и `children`.
- `GET /api/children` → список детей семьи + активный.
- `PUT /api/active-child` (`{child_id}`) → `set_active_child` (только родитель).
- Все data-эндпоинты используют `auth["family_id"]` и `auth["active_child_id"]`
  вместо `config.CHILD_ID`/`CHILD_NAME` (замеры, история, график, статистика, сводка,
  настройки, экспорт, бэкап).
- Уведомления из Mini App (`web/notify.py`): получатели — родители семьи из `members`.
- Фронтенд: селектор ребёнка в шапке, если детей >1; при отсутствии детей — подсказка.

---

## 8. Критерии приёмки

- [ ] Новая семья создаёт семью, добавляет ребёнка, ребёнок входит, замеры пишутся и
      видны только этой семье (сквозной тест: две семьи, изоляция bot + web).
- [ ] Родитель с 2 детьми переключает активного ребёнка; данные меняются.
- [ ] Ребёнок видит только свои данные; изменить активного ребёнка не может.
- [ ] Уведомления идут родителям семьи (не env `PARENT_IDS`).
- [ ] Семья без детей: подсказка «добавьте ребёнка».
- [ ] Семья №1 и `member=None`-путь неизменны; все текущие тесты зелёные.
- [ ] Временный gate удалён; `REG_SOON_MESSAGE` больше не используется.
- [ ] `pyflakes` + `compileall` чисто; документация обновлена.

---

## 9. Тестирование (TDD)

1. Схема v4: `active_child_id` присутствует, миграция идемпотентна.
2. `set_active_child` (валидация своей семьи), `resolve_active_child` (child/parent/нет детей).
3. `_ctx`/`_child_name`/`_family_parents` (member=None fallback; child; parent).
4. Хендлеры: замер/история/сводка/статистика используют активного ребёнка; уведомления —
   родителям семьи.
5. Снятие gate: семья №2 проходит без `REG_SOON_MESSAGE`.
6. Web: `children`/`active-child`; data-эндпоинты scoped; изоляция семей.
7. Регрессия: семья №1 + полный прогон.

---

## 10. Риски и решения

| Риск | Решение |
|------|---------|
| Огромный объём (48×`CHILD_ID`) | хелпер `_ctx` + механическая замена; `member=None` fallback сохраняет тесты |
| Потеря семье №1 изоляции | fallback (`member=None`) даёт прежнее поведение; регрессионный прогон |
| Уведомления чужим | `_family_parents` из `members`; тест на две семьи |
| Активный ребёнок удалён | `resolve_active_child` падает на первого доступного |
| Mini App без детей | `active_child_id=None` → подсказка, пустые данные |

---

## 11. Вне scope 2C

- Сводная аналитика по всем детям сразу.
- Дата рождения/целевая ПСВ на ребёнка (глобальная `target_pef` семьи).
- Роль doctor, PostgreSQL.

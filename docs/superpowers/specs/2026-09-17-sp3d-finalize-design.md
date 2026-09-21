# SP3D — Завершение Фазы 2 (мульти-семейный планировщик, dry-run, добивка изоляции)

> **Дата:** 2026-09-17
> **Подпроект:** Фаза 2D
> **Статус:** дизайн на ревью
> **Зависимости:** SP3A/3B/3C (в main, 418 тестов)
> **Не входит:** PostgreSQL/Docker/REST (Фаза 3), PDF/геймификация (Фаза 4), PWA/графики (Фаза 5)

---

## 1. Цель

Закрыть известные ограничения Фазы 2 и завершить мульти-тенант:

- планировщик напоминаний и недельного отчёта обслуживает **все семьи**, а не только №1;
- есть **dry-run** миграции (предпросмотр v1→v4 без изменения прод-БД);
- убраны оставшиеся env-зависимости в отображении (автор записи) и добавлена defense-in-depth изоляция `get_measurement_by_id`;
- сквозные тесты изоляции chart/export в Mini App;
- документация отражает фактическое состояние.

**Успех:** семья №2 получает напоминания/отчёт; dry-run показывает, что миграция сделает, без записи; автор записи в истории/CSV корректен для любой семьи; тесты изоляции покрывают chart/export; все тесты зелёные.

---

## 2. Объём

| # | Пункт | Сейчас |
|---|-------|--------|
| D1 | `get_measurement_by_id(child_id)` — фильтр по активному ребёнку | фильтрует только `family_id` |
| D2 | Автор записи в истории/CSV — из `members`, не env | `_history_line` (env `is_parent`), `_user_display_name` (env `CHILD_NAME`) |
| D3 | Тесты изоляции chart/export в Mini App | не покрыто |
| D4 | Мульти-семейный планировщик (in-process per-family tick) | `_ctx(None)` → только семья №1 |
| D5 | Dry-run миграции (внешний скрипт) | отсутствует |
| D6 | Документация | требует актуализации |

Уже закрыто (зафиксировать, кода не требует): 2.8 (`report.py` чистый), 2.10 (базовая изоляция в 2A–2C).

---

## 3. Дизайн

### D4 — Мульти-семейный планировщик (in-process per-family tick)

`database.list_families()` → все семьи. `list_family_children(family_id)` → дети семьи.

`_family_reminder_context(family_id)`:
- для каждой семьи и каждого её ребёнка-участника: `child_id` = telegram_id ребёнка;
- `hours` = `get_reminder_hours(DB_PATH, family_id)`;
- напоминания/эскалация — по каждому ребёнку; уведомления — родителям семьи;
- флаги (`reminders_sent`) уже ключ по `(child_id, date)` — коллизий между семьями нет.

`scheduler_loop` раз в минуту:
```
now/today/hour/minute
for family_id in list_families():
    hours = get_reminder_hours(family_id)
    for child in list_family_children(family_id):
        _maybe_ping_child(child, hours, family_id, hour, minute, today)
        _escalate_parents(child, hours, family_id, hour, minute, today)
    weekly(family_id) if due
```
Семья №1 остаётся совместимой (её `.env`-child — участник после миграции).

Риски: объём БД-запросов на тик растёт с числом семей; для 2D приемлемо (in-process, семей десятки). Оптимизация/вынос в отдельный процесс — Фаза 3.

### D5 — Dry-run миграции

`scripts/migration_dry_run.py`:
- копирует `DB_PATH` (или аргумент) во временный файл;
- запускает `init_db` на копии;
- печатает отчёт: `user_version` до/после, таблицы, число строк (measurements/settings/members/families/invites/reminders_sent), orphan-`child_id` (не в `members`);
- удаляет временный файл; прод не трогает.
- код возврата 0; тест вызывает функции напрямую на копии тестовой БД.

### D1 — `get_measurement_by_id`

Сигнатура: `get_measurement_by_id(db_path, measurement_id, family_id=DEFAULT_FAMILY_ID, child_id=None)`.
Если `child_id` задан — дополнительно фильтрует по нему. Бот/веб передают активного ребёнка.
Тест: запись ребёнка A не находится при `child_id` ребёнка B; без `child_id` — прежнее поведение.

### D2 — Автор записи

- `_history_line(m, target, member=None)` / `_format_history_lines` — значок родитель/ребёнок по `members.role` автора (`get_member(added_by)`), fallback env при `member is None`.
- `_user_display_name` → использовать реальное имя автора из `members` (кэш в пределах рендера), fallback env.
- CSV `display_name` — из `members`.

### D3 — Тесты изоляции chart/export

- `GET /api/chart` для семьи 2 не содержит точек семьи 1 (и наоборот).
- `GET /api/export/csv` семьи 2 — только её строки; `periods` — только её месяцы.
- `GET /api/export/periods` scoped.

---

## 4. Критерии приёмки

- [ ] Семьи №1 и №2 с разными часами напоминаний получают независимые пинги/эскалации/недельные отчёты.
- [ ] `scripts/migration_dry_run.py` печатает отчёт и не изменяет указанную БД.
- [ ] `get_measurement_by_id` не отдаёт запись другого ребёнка при заданном `child_id`.
- [ ] Автор записи (значок/имя/CSV) корректен для семьи №2 (не env).
- [ ] Тесты изоляции chart/export зелёные.
- [ ] `member=None`/семья №1 — без регрессий; все тесты зелёные; pyflakes+compileall чисто.
- [ ] Документация актуализирована.

---

## 5. Тестирование (TDD)

1. `get_measurement_by_id` child-scoped.
2. Автор записи из `members` для семьи 2; env-fallback при `member=None`.
3. Изоляция chart/export (две семьи).
4. Планировщик: две семьи, разные `hours` → оба ребёнка получают пинг; weekly — каждой семье.
5. Dry-run: отчёт на копии тестовой БД, оригинал не изменён.
6. Регрессия: полный прогон.

---

## 6. Риски

| Риск | Решение |
|------|---------|
| Планировщик шумит/тормозит на многих семьях | in-process tick достаточен для 2D; вынос в отдельный процесс — Фаза 3 |
| Dry-run случайно изменит прод | всегда работа с копией во временном файле; удаление копии |
| env-fallback ломает семью №1 | fallback сохраняется только при `member is None` |
| Дубли уведомлений при нескольких детях | флаги ключ `(child_id, date)` — уже изолированы |

---

## 7. Вне scope 2D

- Вынос планировщика/бота/веба в отдельные процессы, очередь уведомлений (Фаза 3).
- Целевая ПСВ на ребёнка.
- Роль doctor, PostgreSQL, Docker.

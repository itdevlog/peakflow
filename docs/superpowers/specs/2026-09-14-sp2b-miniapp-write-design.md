# Design: SP2b — Mini App: запись (замеры, заметки)

**Дата:** 2026-09-14
**Статус:** утверждён (в чате, brainstorming)
**Объём:** write-API (add/edit/delete замеров, заметки) + UI-потоки в Mini App; Telegram-уведомления как в боте.
**Не входит (SP2c):** настройки (цель ПСВ, часы напоминаний), CSV-экспорт, бэкап БД.
**Опирается на:** SP2a (`web/api.py`, `web/auth.py`, `web/static/*`), SP1 (`bot`/`config` в `services`).

---

## 1. Контекст

SP2a дал read-only Mini App. Замеры по-прежнему можно вносить только в
Telegram-боте. SP2b добавляет запись прямо из Mini App с тем же поведением и
правами, что в боте:

- **Add** — ребёнок и родители. Время суток определяется автоматически; если
  сегодня в этот слот стоит авто-запись (auto-carry) — она заменяется реальной.
  После сохранения — предложение заметки. Родителям уходят уведомления
  «добавил замер» и «красная зона».
- **Edit/Delete** — только родители (в боте ребёнок не может править/удалять).
- **Заметки** — ребёнок и родители, ≤200 символов.

## 2. Архитектура

Расширяется существующий `web/api.py` (в процессе бота). Добавляется
`web/notify.py` для Telegram-уведомлений. Бот передаёт свой `Bot` в web-слой
через `services`.

```
web/
  api.py       # + POST/PATCH/DELETE/nnnote эндпоинты
  notify.py    # new: notify_added, notify_red_zone
  auth.py      # без изменений (require_user даёт role)
  static/app.js# + форма пошагового ввода, edit/delete
bot.py         # _web_services(): + "bot": bot
```

`_web_services` в `bot.py` меняется на:
```python
def _web_services(state: dict, shutdown_event: asyncio.Event) -> dict:
    return {"config": app_config, "bot": bot, "state": state, "shutdown_event": shutdown_event}
```

### 2.1 Переиспользование логики бота

Логика add в боте (`_save_measurement`) состоит из: определить `tod`, вызвать
`replace_auto_measurement` (иначе `add_measurement`), вычислить зону/диф, уведомить
родителей. В web это повторяется в `api.py`, но **функции `database.py`
переиспользуются напрямую** (`replace_auto_measurement`, `add_measurement`,
`edit_measurement`, `delete_measurement`, `set_note`, `get_all_measurements`,
`get_effective_target`-эквивалент через `get_setting`). Дублируется только
тонкая оркестрация (как в боте), не SQL.

## 3. Права

`require_user` возвращает `{"user", "role"}`. Добавляется зависимость
`require_parent`:
```python
def require_parent(auth: dict = Depends(require_user)) -> dict:
    if auth["role"] != "parent":
        raise HTTPException(403, "Только родители")
    return auth
```
- add / note → `require_user` (обе роли).
- edit / delete → `require_parent` (только родители).

## 4. REST API (write)

Все под префиксом `/api`. Валидация `pef`: целое `100 ≤ pef ≤ 690`, иначе `422`
(FastAPI `Query`/тело). Несуществующий id → `404`. Ошибки БД → `500` только в
непредвиденных случаях; `rowcount == 0` → `404`.

### 4.1 `POST /api/measurements`
Тело: `{"pef": int}`.
1. `tod = _auto_time_of_day(config)` — сервер по `TZ_OFFSET` (`morning` если час
   < 12). `_auto_time_of_day(config) -> str` — чистая модульная функция в
   `web/api.py`; тесты монkeypatch-ят её для детерминизма.
2. Если `has_today_measurement(tod, skip_auto=True)`: как в боте — переключить
   слот на противоположный (`evening`↔`morning`), чтобы повторный замер не
   затирал уже измеренный слот.
3. `replaced = replace_auto_measurement(DB, CHILD_ID, tod, pef, who)`.
   Если `replaced` — id существующей записи (получить `get_last_measurement`);
   иначе `mid = add_measurement(..., source="manual")`.
4. Вычислить `target`, зону (`pef_zone`), `pct` (`pct_of`), diff к
   предыдущему реальному замеру (`get_all_measurements`).
5. `notify_added(bot, who, pef, tod, target)` — родителям (кроме автора).
6. Если `pct < ZONE_YELLOW` → `notify_red_zone(bot, pef, target)`.
7. Ответ `{"id": mid, "pef": pef, "tod": tod, "zone": "green|yellow|red",
   "pct": int, "diff": int|None}`.

`zone` — строковый ключ (не эмодзи): `green`/`yellow`/`red` по порогам
`ZONE_GREEN`/`ZONE_YELLOW`.

### 4.2 `PATCH /api/measurements/{id}`
Тело: `{"pef": int}`. Только parent. `edit_measurement(DB, id, pef, CHILD_ID)`;
`False` → `404`. Ответ `{"id", "pef"}`. Уведомления при edit не шлём (паритет с
ботом: бот при edit не уведомляет).

### 4.3 `DELETE /api/measurements/{id}`
Только parent. `delete_measurement(DB, id, CHILD_ID)`; `False` → `404`. Ответ
`{"deleted": true, "id": id}`. Уведомлений нет.

### 4.4 `POST /api/measurements/{id}/note`
Тело: `{"note": str}`. Обе роли. `note = note.strip()[:200]`.
`set_note(DB, id, note, CHILD_ID)`; `False` → `404`. Ответ
`{"id", "note", "truncated": bool}` (`truncated` = исходная длина > 200).
Пустая заметка после `strip()` → сохраняем пустую строку (очистка заметки
разрешена), `truncated=False`.

## 5. Уведомления (`web/notify.py`)

```python
async def notify_added(bot, who: int, pef: int, tod: str, target: int) -> None
async def notify_red_zone(bot, pef: int, tod: str, target: int) -> None
```
- `notify_added`: имя автора (`_user_display_name`-эквивалент: CHILD_NAME для
  CHILD_ID, «Родитель» для parent, иначе «Кто-то») + текст
  `📝 <Имя> добавил для <CHILD_NAME>: <pef> л/мин <zone_emoji> (<tod label>)`.
  Шлём всем `PARENT_IDS`, кроме `who` и `CHILD_ID`.
- `notify_red_zone`: текст как в боте
  `🚨 <CHILD_NAME>: ПСВ <pef> л/мин — <zone_name>!\nНорма: <target> (<pct>%).`.
  Шлём всем `PARENT_IDS`.
- Каждый `send_message` в `try/except`, ошибки → лог, запрос не падает.
- Если `bot is None` (web-слой запущен без бота — напр. в тестах) → функции
  ничего не делают (no-op).
- Тексты — плоский текст без Markdown (упрощает web-контекст; в боте был
  Markdown). Эмодзи зоны берём из тех же порогов.

## 6. Фронтенд

### 6.1 Добавление
На экране «Сегодня» — кнопка **«+ Замер»**. Открывает форму (та же вкладка):
- Заголовок «Выбери ПСВ (л/мин)».
- Пошаговый выбор **сотни** (1–6) → **десятки** (0–9) — как в боте
  (`kb_pef_hundreds`/`kb_pef_tens`). Итог = `h*100 + t*10` (диапазон 100–690).
- Кнопка «⬅️ Назад» сбрасывает шаг.
- После выбора десятков → `POST /api/measurements`; показываем результат
  (значение, зона, %, изменение) и кнопки **«📝 Заметка» / «Пропустить»**.
- «📝 Заметка» → поле ввода (textarea, maxlength 200) → `POST /api/measurements/{id}/note`
  → «✅ Заметка сохранена» → обновить «Сегодня».
- «Пропустить» / сохранение заметки → перезагрузка «Сегодня».

### 6.2 Редактирование/удаление (только родители)
В «Истории» у записей родителя — кнопки **✏️** и **🗑️** (ребёнку не
рендерятся; роль известна из `/api/me`, которую фронт уже зовёт на старте —
`state.role`).
- ✏️ → пошаговый ввод с текущим значением как подсказкой → `PATCH` → обновить
  историю.
- 🗑️ → `confirm()` → `DELETE` → обновить историю.
- Ошибки (403/404/сеть) → баннер через существующий `showError`.

### 6.3 Состояние
`state.role` (из `/api/me`), `state.form` (шаг, сотни, режим add/edit, editId).

## 7. Обработка ошибок

- `pef` вне 100–690 → `422`; фронт показывает «Значение 100–690».
- edit/delete ребёнком → `403`.
- несуществующий id → `404`.
- уведомление не отправилось → лог, запрос успешен.
- сеть упала → баннер, повтор возможен.

## 8. Тесты

**`test_webapp_write.py`** (TestClient + тестовая БД, локальный генератор initData
как в `test_webapp_api.py`):
1. child add → 200, запись в БД, `source='manual'`; `tod` =
   детерминированный (тест монkeypatch-ит `web.api._auto_time_of_day` →
   `"morning"`).
2. parent add → 200.
3. add при существующей auto-записи в слоте → запись заменена (id тот же,
   `source='manual'`, значение новое).
4. add при уже сделанном ручном замере в слоте → создаётся запись в
   противоположном слоте.
5. `pef=99`/`pef=691` → 422.
6. child edit → 403; parent edit → 200, значение изменено.
7. child delete → 403; parent delete → 200, запись удалена.
8. edit/delete несуществующего id (parent) → 404.
9. note child/parent → 200, сохранена; >200 → `truncated=True`, обрезка.
10. note несуществующего id → 404.
11. unauth (без заголовка) → 403.
12. add возвращает `zone`/`pct`/`diff` корректно для известного набора.

**`test_webapp_notify.py`** (мок `bot.send_message` `AsyncMock`):
13. add родителем → второму родителю ушло `notify_added` (и красная зона при
    низком ПСВ).
14. add ребёнком → обоим родителям ушло.
15. ошибка `send_message` (side_effect) → add всё равно 200 (уведомление
    проглатывается).
16. `bot=None` → add 200 без исключений.

## 9. Файлы

| Файл | Изменения |
|------|-----------|
| `web/notify.py` | новый: `notify_added`, `notify_red_zone` |
| `web/api.py` | +4 write-роута, `require_parent`, `_auto_time_of_day`, `_pef_zone`/`_pct_of`, `_effective_target`-использование |
| `web/static/app.js` | +форма пошагового ввода, add/edit/delete, заметка, `state.role` |
| `web/static/style.css` | стили формы/кнопок |
| `bot.py` | `_web_services`: +`"bot": bot` |
| `test_webapp_write.py` | новый |
| `test_webapp_notify.py` | новый |
| `README.md`, `wiki.md`, `roadmap.md` | SP2b-статус |

## 10. Безопасность

- Все write-роуты под `require_user`/`require_parent`; не-семья → 403.
- Все записи принадлежат `CHILD_ID`; `edit/delete/set_note` фильтруют по
  `user_id = CHILD_ID`.
- `note` обрезается до 200; `pef` ограничен 100–690 (защита от мусора).
- Тексты уведомлений — плоские, без Markdown (нет инъекции разметки).
- CSRF не актуален: auth через initData-заголовок (не cookie), Caddy same-origin.

## 11. Открытые вопросы — нет

Решено в чате: уведомления — как в боте (нужен `bot` в web-слое); права —
паритет с ботом (edit/delete только родители); ввод — пошаговые кнопки.

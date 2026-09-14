# Design: SP2a — Mini App (только чтение)

**Дата:** 2026-09-13
**Статус:** утверждён (в чате, brainstorming)
**Объём:** auth через Telegram initData, read-only REST API, vanilla-JS фронтенд с интерактивным canvas-графиком.
**Не входит (SP2b):** добавление/редактирование/удаление замеров, заметки.
**Не входит (SP2c):** настройки (цель ПСВ, часы напоминаний), CSV-экспорт, бэкап БД.
**Опирается на:** SP1 (`web/api.py`, `web/server.py`, `WebApp_PORT/URL`, in-process uvicorn).

---

## 1. Контекст

SP1 поднял web-слой в процессе бота: `web/api.py` умеет только `GET /healthz`,
`web/server.py` запускает uvicorn, `config.py` читает `WEBAPP_*`. Mini App как
таковой отсутствует. SP2 добавляет сам Mini App; чтобы объём был обозримым,
он разбит: SP2a — чтение (эта спека), SP2b — запись, SP2c — настройки/экспорт.

Цель SP2a: семья открывает Mini App из Telegram и видит текущий статус,
историю, график и статистику — те же данные, что и в боте, в web-виде.

## 2. Архитектура

Один процесс (как SP1). Добавляются:

```
web/
  auth.py       # валидация Telegram WebApp initData
  api.py        # расширяется: read-only REST (SP2a)
  static/
    index.html  # разметка + Telegram WebApp SDK
    app.js      # vanilla JS: табы, fetch, canvas-график
    style.css   # тёмная/светлая тема Telegram
```

Бэкенд вызывает функции `database.py` напрямую, используя
`services["config"]` (для `DB_PATH`, `CHILD_ID`, `PARENT_IDS`, `BOT_TOKEN`).
Никаких новых таблиц/миграций — только чтение существующей БД.

`web/api.py:create_app(services)` монтирует статику в конце (как в SP1-плане
эталона): `app.mount("/", StaticFiles(directory=..., html=True))`.
FastAPI отдаёт `/api/*` раньше, чем catch-all-статика.

### 2.1 MenuButton Mini App

В `bot.py` при старте (хук `on_startup`), если `WEBAPP_URL` задан и
`WEBAPP_PORT > 0`, установить кнопку меню:
```python
await bot.set_chat_menu_button(
    menu_button=MenuButtonWebApp(text="💨 Дневник", web_app=WebAppInfo(url=WEBAPP_URL))
)
```
Ошибка установки логируется и не валит запуск (try/except, как в эталоне).
Если `WEBAPP_URL` пуст — кнопка не ставится, бот работает как раньше.

## 3. Авторизация и права

### 3.1 `web/auth.py` (порт эталона)
```python
def parse_init_data(init_data: str) -> dict: ...
def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict | None: ...
def get_user_from_init_data(init_data: str, bot_token: str) -> dict | None: ...
```
- HMAC: `secret = HMAC_SHA256(b"WebAppData", bot_token)`, затем
  `HMAC_SHA256(secret, data_check_string)`; сравнение — `hmac.compare_digest`.
- `data_check_string` — отсортированные `key=value` через `\n`, без `hash`.
- `auth_date`: старше `max_age` (86400 с) или из будущего → невалидно.
- `user`/`receiver` парсятся из JSON в `parse_init_data`.

### 3.2 Авторизация запроса
`create_app(services)` собирает `_resolve_user(init_data: str | None) -> dict`
(чистая функция, тестируемая без HTTP):
1. Нет заголовка / подпись невалидна → `HTTPException(403)`.
2. `user.id == config.CHILD_ID` → `{"user": ..., "role": "child"}`.
3. `user.id in config.PARENT_IDS` → `{"user": ..., "role": "parent"}`.
4. Иначе → `HTTPException(403)`.

FastAPI-зависимость `require_user(x_telegram_init_data: str | None =
Header(None)) -> dict` просто вызывает `_resolve_user`. `BOT_TOKEN` берётся из
`services["config"].BOT_TOKEN`. Если токен пуст или `None` — все запросы →
`403` (не 500).

### 3.3 Паритет прав в SP2a
Все read-эндпоинты доступны и `child`, и `parent`. Разграничение write-прав —
SP2b (в боте родитель может edit/delete, ребёнок — только добавлять).

## 4. REST API (read-only, префикс `/api`)

Все эндпоинты зависят от `require_user`. `child`-данные принадлежат
`CHILD_ID` (в БД все записи — его). Ошибки БД не должны давать 500:
пустой результат → пустые списки/нули.

| Метод | Путь | Ответ |
|-------|------|-------|
| GET | `/api/me` | `{user: {id, first_name, username}, role, child_name, target_pef}` |
| GET | `/api/status` | `{today: [...], last: {...}\|null, target_pef}` |
| GET | `/api/history?page=N&per_page=10` | `{items: [...], page, total, total_pages}` |
| GET | `/api/chart?year=YYYY&month=MM` | `{points: [{date, tod, pef, source}], target_pef, zones: {green, yellow}, month, title, can_prev, can_next, available_months}` |
| GET | `/api/stats` | результат `get_stats()` + `target_pef` |
| GET | `/api/summary` | `{today: [...], target_pef}` |
| GET | `/api/weekly?offset=0` | `{this_week: [...], prev_week: [...]}` |

Детали:
- `/api/me`: `target_pef` — из `get_setting(DB_PATH, "target_pef", TARGET_PEF)`
  (та же логика, что `get_effective_target()` в bot.py).
- `/api/status`: `today` = `get_today_measurements`; `last` =
  `get_last_measurement`. Никаких авто-исключений сверх того, что уже делают
  функции БД.
- `/api/history`: `get_measurements_paginated(DB_PATH, CHILD_ID, page, per_page)`;
  `per_page` фиксируем 10 (паритет с ботом), `page ≥ 1`.
- `/api/chart`: если `year/month` не заданы — текущий месяц
  (`datetime.now(TZ)`); точки — `get_measurements_for_month` (авто уже
  исключены фильтром БД), сортировка по `measured_at`; `available_months` —
  `get_available_months`; `can_next` = есть ли месяц в `available_months`
  позже запрошенного; `can_prev` — раньше. `title` — «<Месяц> <Год>»
  (русские названия, как `month_title` в bot.py).
  `zones` = `{green: ZONE_GREEN, yellow: ZONE_YELLOW, red: ZONE_RED}` из config.
- `/api/stats`: `get_stats(DB_PATH, CHILD_ID)` + `target_pef`.
- `/api/weekly`: `get_last_two_weeks(DB_PATH, CHILD_ID)` → `(this, prev)`.
  `offset` в SP2a принимается, но пока всегда текущая/прошлая (лимит бота);
  вне диапазона — clamp.

## 5. Фронтенд (`web/static/`)

### 5.1 `index.html`
- `<script src="https://telegram.org/js/telegram-web-app.js">`.
- Табы: **Сегодня | История | График | Статистика**.
- Контейнеры под каждый экран; `loading`; `error`.
- `<canvas id="chart">` для графика.

### 5.2 `app.js` (vanilla, без сборки и внешних либ)
- `const tg = window.Telegram.WebApp; tg.ready(); tg.expand();`
- Общий `api(path)` — `fetch` с заголовком `X-Telegram-Init-Data: tg.initData`;
  не-2xx → бросает с `detail`.
- Табы переключают экраны и лениво грузят данные.
- **Сегодня:** карточки сегодняшних замеров (значение, утро/вечер, зона,
  % от цели), последний замер, отметка `🤖 авто`.
- **История:** список строк (дата, время суток, ПСВ, зона, заметка `ℹ️`,
  `🤖 авто`), кнопки «‹ / ›» по `total_pages`.
- **График (canvas, вручную):**
  - оси: X — дата, Y — ПСВ; подписи осей.
  - линия реальных замеров (авто исключены бэком); точки утро (☀️) и вечер (🌙)
    разными цветами.
  - горизонтальная линия цели `target_pef`; фоновые полосы зон по
    `green/yellow/red` %.
  - навигация «‹ Месяц ›» по `available_months` (`can_prev`/`can_next`).
  - `click`/`touch` по canvas → ближайшая точка → тултип (дата, значение, tod).
  - ресайз по ширине экрана (`window.innerWidth`, DPR-aware canvas).
- **Статистика:** total, avg, min, max, latest, morning/evening avg+count,
  trend (↑/↓), today_count; всё в карточках.
- Оформление под тему Telegram (`tg.themeParams` через CSS-переменные).

### 5.3 `style.css`
Простая адаптивная вёрстка (mobile-first), тёмная/светлая тема через
`prefers-color-scheme` и переменные Telegram.

## 6. Обработка ошибок

- Невалидный/просроченный initData → `403`; фронтенд показывает «Откройте через
  Telegram».
- Нет данных → экран с подсказкой, не ошибка.
- Ошибка сети/`fetch` → баннер с текстом, повтор по кнопке.
- Canvas при 0 точках — «В этом месяце замеров нет», навигация остаётся.
- Установка MenuButton не удалась → лог, бот продолжает работу.

## 7. Тесты

**`test_webapp_auth.py`** (юнит, без сети):
1. Валидная подпись → возвращает dict с `user`.
2. Неверный `hash` → `None`.
3. `auth_date` старше 86400 → `None`.
4. `auth_date` из будущего → `None`.
5. Пустой `init_data` / пустой токен → `None`.
6. `parse_init_data` разбирает `user` из JSON.

**`test_webapp_api.py`** (расширение; `TestClient` + тестовая БД через
существующую фикстуру `setup_db`, temp `DB_PATH`):
7. Без заголовка initData → `403`.
8. Невалидная подпись → `403`.
9. `user.id == CHILD_ID` → `200`, role `child`.
10. `user.id` из `PARENT_IDS` → `200`, role `parent`.
11. Чужой `user.id` → `403`.
12. `/api/me` отдаёт `child_name` и `target_pef`.
13. `/api/history` — пагинация: N записей → корректные `total/total_pages`.
14. `/api/chart` исключает авто-записи и содержит `target_pef`/`zones`.
15. `/api/stats` — `total`, `avg` для известного набора.
16. `/api/status`/`/api/summary` — пустая БД → пустые списки, не 500.

Тестовая подпись строится в тесте той же формулой HMAC (свой генератор
валидного initData), чтобы не зависеть от Telegram.

## 8. Файлы

| Файл | Изменения |
|------|-----------|
| `web/auth.py` | новый (~47 строк, порт эталона) |
| `web/api.py` | расширение: `require_user`, 7 read-эндпоинтов, монтаж статики |
| `web/static/index.html` | новый |
| `web/static/app.js` | новый (~350–450 строк) |
| `web/static/style.css` | новый (~100–150 строк) |
| `bot.py` | MenuButtonWebApp в `on_startup` (guarded) |
| `test_webapp_auth.py` | новый |
| `test_webapp_api.py` | расширение |
| `README.md`, `wiki.md`, `roadmap.md` | документация Mini App (SP2a) |

## 9. Ограничения и безопасность

- Mini App отдаёт медданные: все `/api/*` под initData-HMAC; не-семья → 403.
- `WEBAPP_HOST=0.0.0.0` в SP1; для реального доступа обязателен Caddy/HTTPS
  (`./manage.sh caddy`), т.к. Telegram требует HTTPS для initData.
  Рекомендация (не в объёме): для прод-режима биндить `127.0.0.1`.
- Секреты (`BOT_TOKEN`) не логируются и не отдаются в ответах.

## 10. Открытые вопросы — нет

Решено в чате: auth — initData + паритет прав; график — интерактивный JS
Canvas; декомпозиция SP2a (чтение) → SP2b (запись) → SP2c (настройки/экспорт);
структура — REST JSON + vanilla JS в `web/static`.

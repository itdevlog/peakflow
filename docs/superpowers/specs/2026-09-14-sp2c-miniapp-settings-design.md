# Design: SP2c — Mini App: настройки, CSV, бэкап

**Дата:** 2026-09-14
**Статус:** утверждён (в чате, brainstorming)
**Объём:** настройки (цель ПСВ, часы напоминаний), CSV-экспорт по периодам, бэкап БД — всё для родителей; вынос общей CSV/хелпер-логики в `report.py`.
**Последний** из запланированных подпроектов (SP1 → SP2a → SP2b → SP2c).
**Опирается на:** SP2a/SP2b (`web/api.py`, `require_parent`, `web/static/*`), существующие `database.py` (`set_setting`, `get_reminder_hours`, `backup_db`, `get_measurements_between`, `get_available_months`).

---

## 1. Контекст

Бот даёт родителям настройки (цель ПСВ 100–800, четыре часа напоминаний
0–23), CSV-экспорт (всё время / месяцы, UTF-8 BOM, колонки с заметкой и
источником) и согласованный бэкап БД. В Mini App этого пока нет. SP2c переносит
эти функции в web с тем же поведением и правами (только родители).

Общая логика (CSV-формат, зоны, названия месяцев, отображаемое имя) сейчас
живёт в `bot.py`, который web-слой импортировать не должен (тяжёлый: aiogram +
matplotlib + Bot). Поэтому выносим чистые функции в `report.py` и
ре-экспортируем их из `bot.py` для обратной совместимости тестов.

## 2. Архитектура

```
report.py        # new: чистые хелперы + CSV (без aiogram/matplotlib)
bot.py           # импортирует из report; ре-экспорт имён (обёртки/алиасы)
web/api.py       # + settings/export/backup роуты (require_parent)
web/static/app.js# + таб «Настройки» (только родители)
web/static/{index.html,style.css}
```

`report.py` не импортирует `bot.py`, `database.py`, aiogram или matplotlib.
CSV-генератор принимает уже выбранные данные (rows/target/stats) — чистая
функция без доступа к БД.

### 2.1 Что выносится в `report.py`

| Функция/константа | Сигнатура | Источник в bot.py |
|-------------------|-----------|-------------------|
| `MONTH_NAMES` | `list[str]` | `MONTH_NAMES` |
| `month_title` | `(year, month) -> str` | `month_title` |
| `tod_emoji` | `(tod) -> str` | `tod_emoji` |
| `tod_label` | `(tod) -> str` | `tod_label` |
| `pef_zone` | `(value, target, zone_green=80, zone_yellow=60) -> tuple[str,str]` | `pef_zone` (пороги параметризуются; bot вызывает с дефолтами) |
| `pct_of` | `(value, target) -> int` | `pct_of` |
| `display_name` | `(user_id, child_id, child_name, parent_ids) -> str` | тело `_user_display_name` |
| `parse_month` | `(payload) -> tuple[int,int]|None` | `parse_csv_month` |
| `build_csv_content` | `(rows, target, child_name, stats=None, include_summary=True, display_name=None, zone_green=80, zone_yellow=60) -> str` | `build_csv_content` |

`build_csv_content` использует `pef_zone`/`pct_of`/`tod_label`/`display_name`
внутри. `display_name` передаётся колбэком-функцией `(added_by)->str` (чтобы
`report.py` не знал о config); по умолчанию — прочерк/`"Кто-то"`. Пороги зон
передаются параметрами (`zone_green`/`zone_yellow`), чтобы `bot.py` и
`web/api.py` использовали значения из `config`; дефолты 80/60 совпадают с
`config`.

### 2.2 Ре-экспорт в `bot.py`

Чтобы `test_bot.py` (`from bot import month_title`, `pef_zone`, `pct_of`,
`parse_csv_month`, `bot.build_csv_content`) продолжал работать:

```python
from report import (
    MONTH_NAMES, month_title, tod_emoji, tod_label, pct_of,
    parse_month as parse_csv_month,
    build_csv_content as _report_build_csv,
    pef_zone as _report_pef_zone,
)

def pef_zone(value, target):
    """Обёртка: пороги зон берутся из config (как раньше)."""
    return _report_pef_zone(value, target, ZONE_GREEN, ZONE_YELLOW)

def build_csv_content(rows, target, include_summary=True):
    stats = get_stats(DB_PATH, CHILD_ID) if include_summary else None
    return _report_build_csv(rows, target, CHILD_NAME, stats=stats,
                             include_summary=include_summary,
                             display_name=_user_display_name)
```

`pct_of`, `month_title`, `tod_*`, `parse_csv_month` доступны как
импортированные имена; `pef_zone`/`build_csv_content` — тонкие обёртки,
сохраняющие прежние сигнатуры (тесты `from bot import pef_zone` и
`bot.build_csv_content(rows, target, include_summary=False)` не меняются).
Все существующие вызовы в `bot.py` продолжают работать без правок.

## 3. Права

Все роуты SP2c — под `require_parent` (только родители). Child → `403`.
Настройки/CSV/бэкап в боте тоже только для родителей.

## 4. REST API

### 4.1 `GET /api/settings` (parent)
```json
{"target_pef": 260, "child_name": "Motya", "total": 42,
 "reminder_hours": {"child_morning": 8, "child_evening": 20,
                    "parent_morning": 10, "parent_evening": 22}}
```
`total = len(get_all_measurements(DB_PATH, CHILD_ID, include_auto=True))`
(как в боте). `reminder_hours = get_reminder_hours(DB_PATH)`.

### 4.2 `PUT /api/settings/target` (parent)
Тело `{"target_pef": int}`, диапазон `100 ≤ x ≤ 800` (иначе `422`).
`set_setting(DB_PATH, "target_pef", str(x))`. Ответ
`{"target_pef": x}`.

### 4.3 `PUT /api/settings/reminders` (parent)
Тело:
```json
{"child_morning": 8, "child_evening": 20, "parent_morning": 10, "parent_evening": 22}
```
Каждое значение `0 ≤ h ≤ 23` (иначе `422`). Для каждого ключа
`set_setting(DB_PATH, f"reminder_{key}", str(h))`. Ответ
`{"reminder_hours": {...}}` (перечитанные `get_reminder_hours`).

### 4.4 `GET /api/export/csv?period=all|YYYY-MM` (parent)
- `period=all` → `get_measurements_between(DB_PATH, CHILD_ID, "2000-01-01", today)`.
- `period=YYYY-MM` → валидный `(year, month)`; границы месяца считаются как в
  боте (`от 1-го до последнего дня`); невалидный period → `422`.
- Если строк нет → `404` (`📭 нет данных`).
- Тело: `build_csv_content(rows, target, CHILD_NAME, stats, display_name=...)`,
  кодируется в **UTF-8 с BOM** (`content.encode("utf-8-sig")`).
- Заголовки: `Content-Type: text/csv; charset=utf-8`,
  `Content-Disposition: attachment; filename="peakflow_<CHILD_NAME>_<period>.csv"`.
- Имя: `all` → `peakflow_<CHILD_NAME>_<YYYYMMDD_HHMM>.csv`;
  месяц → `peakflow_<CHILD_NAME>_<YYYY-MM>.csv`.

### 4.5 `GET /api/export/periods` (parent)
`{"months": ["2026-08", "2026-09"], "latest": "2026-09"}` —
`get_available_months` (может быть пусто). Фронт строит список периодов.

### 4.6 `GET /api/backup` (parent)
1. `dest = tempfile.mktemp(suffix=".db")`.
2. `backup_db(DB_PATH, dest)` (согласованный `sqlite3.backup`, WAL-safe).
3. `FileResponse(dest, media_type="application/octet-stream", filename=f"peakflow_backup_<stamp>.db", background=BackgroundTask(os.remove, dest))`.
4. `stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M")`. Ошибка бэкапа → лог и
   `500` с `{"detail": "Не удалось создать бэкап"}` (temp-файл удаляется).

## 5. Фронтенд

### 5.1 Таб «Настройки»
Добавляется пятый таб, видимый/активный только при `state.role === "parent"`
(ребёнку скрыт). Экран:
- «🎯 Целевая ПСВ: N л/мин» + кнопка «Изменить» → пошаговый ввод
  (сотни 1–8 → десятки) как форма замера, диапазон 100–800 → `PUT target`.
- «⏰ Напоминания»: 4 строки (утро/вечер ребёнку, утро/вечер родителям) с
  текущими часами; тап по строке → числовой ввод 0–23 (prompt или inline-поле)
  → `PUT reminders`.
- «📥 Экспорт CSV»: список периодов (Всё время + доступные месяцы) → скачивание.
- «💾 Скачать бэкап» → скачивание .db.

### 5.2 Скачивание (fetch + blob)
```javascript
async function download(path, fallbackName) {
  const r = await fetch(path, { headers: { "X-Telegram-Init-Data": tg.initData || "" } });
  if (!r.ok) { const b = await r.json().catch(()=>({detail:"Ошибка"}));
    throw new Error(b.detail || `HTTP ${r.status}`); }
  const cd = r.headers.get("Content-Disposition") || "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  const name = m ? m[1] : fallbackName;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}
```
Существующий `api()` не подходит (он парсит JSON) — нужна отдельная функция
(или параметр `raw`).

### 5.3 Состояние
`state.settings` (target, hours), `state.settingsStep` для пошагового ввода.

## 6. Обработка ошибок

- Child на любом SP2c-роуте → `403`; фронт показывает баннер.
- target вне 100–800 / час вне 0–23 → `422`; фронт: «Диапазон …».
- Нет данных для CSV → `404`; фронт: «Нет записей».
- Ошибка бэкапа → `500`; фронт: «Не удалось создать бэкап».
- Скачивание: ошибка сети/HTTP → баннер `showError`.

## 7. Тесты

**`test_report.py`** (чистые функции):
1. `month_title` (русские названия).
2. `pef_zone` для зелёной/жёлтой/красной при target 260.
3. `pct_of` (208→80, 130→50).
4. `parse_month`: `"2026-08"→(2026,8)`, мусор→None.
5. `build_csv_content`: заголовок содержит «Заметка» и «Источник»; авто→«авто»,
   ручной→«ручной»; заметка с запятой заменяется на `;`; `include_summary=False`
   не содержит блока «# Статистика».

**`test_webapp_settings.py`** (TestClient + тестовая БД):
6. child `GET /api/settings` → 403; parent → 200, поля target/child_name/total/hours.
7. parent `PUT target` 300 → 200, `get_setting('target_pef')=='300'`.
8. `PUT target` 99 / 801 → 422.
9. parent `PUT reminders` с валидными часами → 200, `get_reminder_hours` обновились.
10. `PUT reminders` час 24 / −1 → 422.
11. child `PUT target` / `PUT reminders` → 403.

**`test_webapp_export.py`**:
12. child `GET /api/export/csv` → 403.
13. parent CSV `all` → 200, `Content-Type` text/csv, тело начинается с BOM
    (`\ufeff`), содержит «Дата,Время,Период,ПСВ», строку замера и «# Статистика».
14. CSV месяц `YYYY-MM` содержит только записи месяца; нет данных → 404.
15. невалидный period → 422.
16. `GET /api/export/periods` → 200, `months` отсортированы.
17. parent `GET /api/backup` → 200, `application/octet-stream`, тело —
    валидный SQLite (заголовок `SQLite format 3\0`) и содержит замеры; child → 403.

**Регрессия:** существующие тесты `test_bot.py` (`month_title`, `pef_zone`,
`pct_of`, `parse_csv_month`, `build_csv_content`) остаются зелёными без правок.

## 8. Файлы

| Файл | Изменения |
|------|-----------|
| `report.py` | новый: `MONTH_NAMES`, `month_title`, `tod_emoji`, `tod_label`, `pef_zone`, `pct_of`, `display_name`, `parse_month`, `build_csv_content` |
| `bot.py` | импорт из `report` + ре-экспорт (`build_csv_content` обёртка); убрать дубли |
| `web/api.py` | +6 роутов (settings/export/backup), `require_parent`, `BackgroundTask`, `FileResponse`, `tempfile` |
| `web/static/index.html` | 5-й таб «Настройки» + контейнер экрана |
| `web/static/app.js` | загрузка/редактирование настроек, CSV-периоды, скачивание |
| `web/static/style.css` | стили экрана настроек |
| `test_report.py` | новый |
| `test_webapp_settings.py` | новый |
| `test_webapp_export.py` | новый |
| `README.md`, `wiki.md`, `roadmap.md` | SP2c-статус, финал |

## 9. Безопасность

- Все роуты под `require_parent`; child → 403. Настройки меняют медзначимую
  цель и напоминания — только родители.
- CSV/бэкап содержат медданные; отдаются только авторизованной семье, за
  Caddy/HTTPS.
- Бэкап-файл: temp в системном temp, удаляется после отдачи (`BackgroundTask`).
- `target` 100–800, часы 0–23 — валидация входа.
- CSV-инъекция: значения из БД (числа, даты, `note` с заменой `,`→`;`), не
  пользовательский ввод в формулах; риск минимален (как в боте).

## 10. Открытые вопросы — нет

Решено в чате: скачивание — fetch+blob; права — паритет с ботом (всё
родителям); CSV-логика — общий `report.py` с ре-экспортом в `bot.py`.

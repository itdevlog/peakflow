# 🫁 Пикфлоуметр — Полная техническая документация

> Telegram-бот для мониторинга пиковой скорости выдоха (ПСВ / PEF)  
> Мульти-семейный (тенантный) сервис: родители + один или несколько детей,
> у каждого родителя — свой активный ребёнок

---

## 📋 Содержание

1. [Описание](#описание)
2. [Архитектура проекта](#архитектура-проекта)
3. [База данных](#база-данных)
4. [Конфигурация](#конфигурация)
5. [Модуль `bot.py`](#модуль-botpy)
6. [Модуль `database.py`](#модуль-databasepy)
7. [Модуль `config.py`](#модуль-configpy)
8. [Сценарии использования](#сценарии-использования)
9. [Планировщик](#планировщик)
10. [Запуск и тестирование](#запуск-и-тестирование)

---

## Описание

Бот позволяет ребёнку (или родителям) быстро записывать показания пикфлоуметра — максимальную скорость выдоха (ПСВ, л/мин). Родители получают сводки, графики и автоматические уведомления при пропуске замеров или выходе значений за норму.

### Ключевые особенности

| Функция | Описание |
|---------|----------|
| Авто-регистрация | Все 3 пользователя известны по ID из `.env` |
| Авто-утро/вечер | Время суток определяется автоматически (`< 12:00` → 🌅) |
| Статус-блок | В главном меню — сегодня, последний замер, тренд |
| Редактирование | Кнопка «✏️ Исправить последний» |
| Сводка «Сегодня» | Все замеры дня + статистика (родители) |
| Недельный отчёт | Сравнение с прошлой неделей, тренды утро/вечер |
| Выбор ребёнка | Родитель переключает активного ребёнка в боте и Mini App |
| Напоминания | 10:00 / 22:00 — если замер пропущен |
| Красная зона | Мгновенное уведомление обоим родителям при `< 60%` |
| Уведомление родителю | При каждом замере — второму родителю |

---

## Архитектура проекта

```
peakflow/
├── bot.py              # Логика бота (хендлеры, FSM, планировщик)
├── database.py         # SQLite CRUD + миграции (PRAGMA user_version)
├── config.py           # Настройки, ID семьи, пороги
├── report.py           # Чистые хелперы и CSV (общие для бота и Mini App)
├── web/                # FastAPI Mini App: api.py, server.py, auth.py, notify.py, static/
├── test/               # Pytest тесты (412)
├── manage.sh           # Установка и эксплуатация (systemd, бэкапы, Caddy)
├── requirements.txt    # Python зависимости
├── requirements-dev.txt# + pytest, pyflakes
├── .env                # Переменные окружения
├── .env.example        # Шаблон .env
├── README.md           # Краткое описание
├── PROJECT.md          # Полная документация
├── wiki.md             # Документация логики по коду
├── roadmap.md          # Аудит и план развития
└── peakflow.db         # SQLite база (создаётся автоматически)
```

### Технологический стек

| Компонент | Версия | Назначение |
|-----------|--------|------------|
| Python | 3.11+ | Язык |
| aiogram | 3.31 | Telegram Bot Framework |
| FastAPI | 0.141 | Mini App API (в процессе бота) |
| SQLite | 3 | Хранилище данных (WAL mode) |
| matplotlib | 3.11 | Генерация графиков |
| pytest | 9.x | Тестирование |
| python-dotenv | 1.x | Загрузка `.env` |

---

## База данных

Схема — **v4 (мульти-тенант + регистрация + активный ребёнок)**: `SCHEMA_VERSION = 4`,
`DEFAULT_FAMILY_ID = 1`. Данные изолированы по семье (`families`/`members`), замеры
принадлежат семье и ребёнку. При старте `init_db()` на непустой БД старой версии
сначала делает резервную копию `<db>.v1.bak` (`backup_db`), затем мигрирует схему до
v4. Миграция идемпотентна (`ALTER TABLE members ADD COLUMN active_child_id` в
try/except), выполняется в транзакции, при ошибке бэкапа прерывается.

Роли участников в рантайме берутся из `members` (в боте — через `MemberMiddleware`,
в Mini App — в `_resolve_user`). Переменные `.env` (`CHILD_ID`/`PARENT_IDS`) используются
как fallback и для сидинга семьи №1 при миграции. Временный gate 2B, ограничивавший
обслуживание семьёй №1, **снят**: все зарегистрированные семьи получают данные.

Активный ребёнок родителя хранится в `members.active_child_id` (NULL или недоступный
ребёнок → первый ребёнок семьи; у ребёнка активный = он сам). Родитель переключает
ребёнка в боте (кнопка «🧒 Ребёнок» при >1 ребёнке) и в Mini App (`PUT /api/active-child`,
селектор в шапке); все data-запросы идут в разрезе `(family_id, active_child_id)`.

### Таблица `families` (v2)

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | ID семьи (тенанта); семья №1 — `DEFAULT_FAMILY_ID` |
| `name` | TEXT | NOT NULL | Название семьи |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Дата создания |

### Таблица `members` (v2, `active_child_id` — v4)

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `telegram_id` | INTEGER | PRIMARY KEY | Telegram ID участника |
| `family_id` | INTEGER | NOT NULL REFERENCES families(id) | Семья участника |
| `role` | TEXT | NOT NULL CHECK IN ('parent', 'child') | Роль участника |
| `name` | TEXT | NOT NULL DEFAULT '' | Отображаемое имя |
| `active_child_id` | INTEGER | — | Выбранный активный ребёнок родителя (NULL → первый ребёнок семьи; v4) |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Дата добавления |

**Индекс:** `idx_members_family` по `family_id`.

### Таблица `measurements`

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Уникальный ID записи |
| `family_id` | INTEGER | NOT NULL DEFAULT 1 | Семья-владелец (v2) |
| `child_id` | INTEGER | NOT NULL | ID ребёнка (в v1 — `user_id`, обычно `CHILD_ID`) |
| `pef_value` | INTEGER | NOT NULL | Значение ПСВ (л/мин) |
| `time_of_day` | TEXT | CHECK IN ('morning', 'evening', 'unknown') | Время суток |
| `measured_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Дата и время записи |
| `added_by` | INTEGER | — | Telegram ID того, кто добавил |
| `note` | TEXT | — | Заметка к замеру |
| `source` | TEXT | DEFAULT 'manual' | `'manual'` или `'auto'` (автозаполнение пропуска) |

**Индексы:** Автоматический по `id`; `idx_meas_family_child_time` по
`(family_id, child_id, measured_at)`.

### Таблица `reminders_sent` (v2)

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `child_id` | INTEGER | NOT NULL DEFAULT 0, PRIMARY KEY (child_id, date) | ID ребёнка |
| `date` | TEXT | NOT NULL, PRIMARY KEY (child_id, date) | Дата в формате `YYYY-MM-DD` |
| `morning_reminder` | INTEGER | DEFAULT 0 | Отправлено напоминание об утреннем замере |
| `evening_reminder` | INTEGER | DEFAULT 0 | Отправлено напоминание о вечернем замере |
| `weekly_report` | INTEGER | DEFAULT 0 | Отправлен недельный отчёт |
| `child_morning_reminder` / `child_evening_reminder` | INTEGER | DEFAULT 0 | Отправлено напоминание ребёнку |
| `auto_morning` / `auto_evening` | INTEGER | DEFAULT 0 | Выполнено автозаполнение пропуска |

### Таблица `settings` (v2)

Ключ-значение в разрезе семьи: `PRIMARY KEY (family_id, key)`. При миграции
данные v1 переносятся в семью №1.

### Таблица `invites` (v3)

Приглашения в семью. Токен — `secrets.token_urlsafe(8)`, многоразовый и бессрочный.

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `token` | TEXT | PRIMARY KEY | Код приглашения |
| `family_id` | INTEGER | NOT NULL REFERENCES families(id) | Семья |
| `role` | TEXT | NOT NULL CHECK IN ('parent', 'child') | Роль приглашаемого |
| `name` | TEXT | NOT NULL DEFAULT '' | Имя (для карточки ребёнка) |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Дата создания |

**Индекс:** `idx_invites_family` по `family_id`.

Родительское приглашение — одно на семью (`role='parent'`); карточка ребёнка —
строка `role='child'` с именем. Отзыв: `regenerate_family_invite` (родительский)
или `delete_invite` (карточка).

### SQL-запросы

См. функции в [`database.py`](#модуль-databasepy). Правило tenant-aware доступа:
`family_id` — **последний** параметр со значением по умолчанию `DEFAULT_FAMILY_ID`
(= 1), `child_id` занимает прежнюю позицию `user_id`. Исключение:
`mark_reminder_sent`/`was_reminder_sent` — `child_id` обязателен (см. ниже).

---

## Конфигурация

### Файл `.env`

```env
BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxx
CHILD_ID=123456789
PARENT_IDS=987654321,111222333
CHILD_NAME=Маша
TARGET_PEF=260
DB_PATH=peakflow.db
```

### Переменные окружения

| Переменная | Тип | Обязательная | По умолчанию | Описание |
|-----------|-----|:---:|:---:|----------|
| `BOT_TOKEN` | string | ✅ | — | Токен от [@BotFather](https://t.me/BotFather) |
| `CHILD_ID` | int | ✅ | — | Telegram ID ребёнка |
| `PARENT_IDS` | string | ✅ | — | Telegram ID родителей через запятую |
| `CHILD_NAME` | string | ❌ | `Ребёнок` | Имя ребёнка для отображения |
| `TARGET_PEF` | int | ❌ | `260` | Целевая ПСВ от врача (л/мин) |
| `DB_PATH` | string | ❌ | `peakflow.db` | Путь к SQLite файлу |
| `TZ_OFFSET` | int | ❌ | `5` | Смещение часового пояса от UTC в часах |
| `WEBAPP_HOST` | string | ❌ | `127.0.0.1` | Адрес прослушивания веб-сервера Mini App (только через reverse proxy) |
| `WEBAPP_PORT` | int | ❌ | `8080` | Порт веб-сервера; `0` — веб-сервер выключен |
| `WEBAPP_URL` | string | ❌ | — | Публичный HTTPS-URL Mini App; пусто — кнопка Mini App не добавляется |

### Константы в `config.py`

| Константа | Значение | Описание |
|-----------|:---:|----------|
| `ZONE_GREEN` | `80` | Порог зелёной зоны (% от нормы) |
| `ZONE_YELLOW` | `60` | Порог жёлтой зоны (% от нормы) |
| `ZONE_RED` | `50` | Справочный порог «опасно» (фактически красная зона `< 60%`) |
| `REMINDER_MORNING_HOUR` | `8` | Час утреннего напоминания ребёнку |
| `REMINDER_MORNING_DEADLINE` | `10` | Дедлайн утреннего замера |
| `REMINDER_EVENING_HOUR` | `20` | Час вечернего напоминания |
| `REMINDER_EVENING_DEADLINE` | `22` | Дедлайн вечернего замера |
| `WEEKLY_REPORT_DAY` | `6` | День недели отчёта (0=Пн, 6=Вс) |
| `WEEKLY_REPORT_HOUR` | `21` | Час отчёта |

---

## Модуль `bot.py`

### FSM-состояния

```python
class Measurement(StatesGroup):
    editing_target_pef = State()      # Родитель вводит новую целевую ПСВ
    editing_reminder_hour = State()   # Родитель вводит час напоминания
    waiting_note = State()            # Ожидание текста заметки
    pef_input_hundreds = State()      # Выбор сотен ПСВ
    pef_input_tens = State()          # Выбор десятков ПСВ
```

Выход из любого состояния — команда `/cancel`.

### Хелперы

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `auto_time_of_day()` | — | `str` | `"morning"` если текущий час < 12, иначе `"evening"` |
| `tod_emoji(tod)` | `tod: str` | `str` | `"☀️"` или `"🌙"` |
| `tod_label(tod)` | `tod: str` | `str` | `"Утро"` или `"Вечер"` |
| `pef_zone(value, target)` | `value: int, target: int` | `tuple[str, str]` | `(эмодзи, название_зоны)` |
| `pct_of(value, target)` | `value: int, target: int` | `int` | Процент от нормы |
| `answer_callback(cb)` | `callback: CallbackQuery` | — | ACK + удаление сообщения callback |
| `respond(cb, text, kb)` | `callback, text, kb, parse_mode` | `Message` | answer_callback + answer |
| `_user_display_name(uid, member=None, child_name=None, members_map=None)` | `...` | `str` | Имя автора записи: при `members_map` — из `members` (иначе «Кто-то»), контекст семьи никогда не читает `.env`; fallback на `.env` только при `member is None` |
| `_history_line(m, target, member=None, author_roles=None)` | `...` | `str` | Строка истории: значок автора (👨‍👧/👶) по `author_roles` из `members`; `.env` только при `member is None` |
| `_ctx(member) → (family_id, child_id)` | `member: dict \| None` | `tuple` | Тенант-контекст: семья и активный ребёнок (`member=None` → семья №1 из `.env`) |
| `_child_name(member, child_id, child_name=None)` | `...` | `str` | Имя активного ребёнка (`member=None` → env `CHILD_NAME`) |
| `_family_parents(member, family_id)` | `...` | `list[int]` | Telegram ID родителей семьи (fallback — env `PARENT_IDS`) |
| `_no_child_reply(target, member)` | `target, member` | — | Подсказка «добавьте ребёнка», когда в семье нет детей |

### Клавиатуры

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `kb_main(is_parent, show_child_button=False)` | `is_parent_user: bool, show_child_button: bool` | `InlineKeyboardMarkup` | Главное меню (разное для родителя/ребёнка); кнопка выбора ребёнка при >1 ребёнке |
| `kb_back()` | — | `InlineKeyboardMarkup` | Кнопка «⬅️ Назад» |
| `kb_pagination(page, total)` | `page: int, total_pages: int` | `InlineKeyboardMarkup` | Кнопки ⏮️ 1/2 ⏭️ |

### Статус-блок

```python
async def build_status_block() -> str:
```

Формирует текст для главного меню:
```
👋 Маша | Целевая: 260 л/мин

Сегодня: 🌅 240 🟢 | 🌆 —
Последний: 240 🟢 (+10)
```

### Обработчики команд

| Команда | Функция | Описание |
|---------|---------|----------|
| `/start` | `cmd_start(message, state)` | Проверка ID → приветствие → главное меню |

### Обработчики callback

| Callback | Функция | Описание |
|----------|---------|----------|
| `add` | `cb_add(cb, state)` | Начало замера (авто-утро/вечер) |
| `add_force_morning` | `cb_add_force(cb, state)` | Повторный утренний замер |
| `add_force_evening` | `cb_add_force(cb, state)` | Повторный вечерний замер |
| `history` | `cb_history(cb, state)` | История (страница 1) |
| `hist_page_N` | `cb_history_page(cb, state)` | Страница N истории |
| `chart` | `cb_chart(cb)` | График за 30 дней |
| `summary` | `cb_summary(cb)` | Сводка за сегодня (только родители) |
| `weekly` | `cb_weekly(cb)` | Недельный отчёт (только родители) |
| `stats` | `cb_stats(cb)` | Общая статистика (только ребёнок) |
| `edit_last` | `cb_edit_last(cb, state)` | Начать редактирование последнего |
| `back` | `cb_back(cb, state)` | Возврат в главное меню |
| `noop` | `cb_noop(cb)` | Пустой callback (для пагинации) |

### Обработчики текста

| Состояние | Функция | Описание |
|-----------|---------|----------|
| `Measurement.waiting_for_pef` (цифра) | `input_pef(msg, state)` | Сохранение замера |
| `Measurement.waiting_for_pef` (не цифра) | `input_pef_invalid(msg)` | Подсказка «введите число» |
| `Measurement.editing_last` (цифра) | `input_edit(msg, state)` | Исправление последнего замера |
| `F.text` (catch-all) | `catch_all(msg, state)` | Возврат в главное меню |

### Логика сохранения замера

```
input_pef()
  ├─ Проверка диапазона 50–800
  ├─ Определение time_of_day (forced или auto)
  ├─ add_measurement()
  ├─ Вычисление зоны и % от нормы
  ├─ Вычисление изменения к предыдущему
  ├─ Ответ пользователю
  ├─ Уведомление второму родителю
  ├─ Если красная зона → уведомление обоим родителям
  └─ Главное меню
```

### Планировщик

`_tick_targets()` собирает пары `(family_id, child)` по **всем** семьям
(`list_families` → `list_family_children`; для семьи №1 без строк в `members` —
fallback на `.env`-ребёнка). `scheduler_loop()` тикает раз в минуту и для каждого
ребёнка каждой семьи:

```python
async def scheduler_loop():
    # Цикл каждую минуту, по каждой семье и каждому её ребёнку:
    #   08:00/20:00 — пинг ребёнку (часы из settings семьи)
    #   10:00/22:00 — эскалация родителям семьи, если замера нет
    #   Вс 21:00 — недельный отчёт НА КАЖДОГО ребёнка, родителям его семьи
    #              (дедуп по (child_id, "weekly"))
```

> Каждая семья использует собственные `reminder_*` из `settings`; уведомления не
> выходят за пределы семьи. Семья с двумя детьми получает два независимых
> недельных отчёта (по одному на ребёнка). Семья №1 остаётся совместимой через
> `.env`-ребёнка.

### Главная функция

```python
def main():
    # 1. Проверка BOT_TOKEN, CHILD_ID, PARENT_IDS
    # 2. Singleton lock (flock)
    # 3. init_db()
    # 4. dp.startup.register(on_startup)
    # 5. dp.run_polling(bot)
```

---

## Модуль `database.py`

### Основные функции

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `init_db(db_path)` | `db_path: str` | — | Создание таблиц, миграция v1→v2, бэкап `<db>.v1.bak` |
| `add_measurement(db, pef, tod, child_id, by, source='manual', family_id=1)` | `str, int, str, int, int, str, int` | `int` | Добавление замера, возвращает ID |
| `add_or_replace_measurement(db, pef, tod, child_id, by, force=False, source='manual', family_id=1)` | `...` | `tuple` | Атомарно добавить/заменить авто-запись, `(id, status)` |
| `edit_measurement(db, mid, val, child_id, family_id=1)` | `str, int, int, int, int` | `bool` | Редактирование по ID |
| `delete_measurement(db, mid, child_id, family_id=1)` | `str, int, int, int` | `bool` | Удаление по ID |
| `get_measurement_by_id(db, mid, family_id=1, child_id=None)` | `str, int, int, int \| None` | `Optional[dict]` | Замер по `id` в семье; при заданном `child_id` — дополнительно scoped по ребёнку |
| `get_last_measurement(db, child_id, family_id=1)` | `str, int, int` | `Optional[dict]` | Последний замер |
| `get_all_measurements(db, child_id, include_auto=False, family_id=1)` | `str, int, bool, int` | `list[dict]` | Все замеры (DESC по id) |
| `get_today_measurements(db, child_id, family_id=1)` | `str, int, int` | `list[dict]` | Замеры за сегодня |
| `has_today_measurement(db, child_id, tod, skip_auto=False, family_id=1)` | `str, int, str, bool, int` | `bool` | Есть ли замер today+tod |
| `get_measurements_paginated(db, child_id, p, pp, family_id=1)` | `str, int, int, int, int` | `tuple` | `(замеры, всего, страниц)` |
| `get_measurements_for_chart(db, child_id, days=30, family_id=1)` | `str, int, int, int` | `list[dict]` | Данные для графика |
| `get_stats(db, child_id, family_id=1)` | `str, int, int` | `dict` | Полная статистика |
| `get_last_two_weeks(db, child_id, family_id=1)` | `str, int, int` | `tuple` | `(эта_неделя, прошлая_неделя)` |
| `mark_reminder_sent(db, date, type, child_id)` | `str, str, str, int` | — | Отметить отправку напоминания (child_id обязателен) |
| `was_reminder_sent(db, date, type, child_id)` | `str, str, str, int` | `bool` | Было ли напоминание сегодня (child_id обязателен) |
| `create_family(db, name)` / `get_family(db, fid)` | `str, str` / `str, int` | `int` / `Optional[dict]` | Создать/прочитать семью |
| `add_member(db, telegram_id, family_id, role, name)` / `get_member(db, telegram_id)` | `...` | — / `Optional[dict]` | Upsert/чтение участника |
| `list_family_children(db, family_id)` | `str, int` | `list[dict]` | Дети семьи |
| `list_family_parents(db, family_id)` | `str, int` | `list[dict]` | Родители семьи (получатели уведомлений) |
| `count_family_children(db, family_id)` | `str, int` | `int` | Число детей семьи (кнопка выбора при >1) |
| `set_active_child(db, telegram_id, child_id)` | `str, int, int` | `bool` | Выбрать активного ребёнка родителя (валидирует роль и свою семью) |
| `resolve_active_child(db, member)` | `str, dict` | `int \| None` | Активный ребёнок: ребёнок → сам; родитель → `active_child_id`/первый; нет детей → `None` |
| `backup_family_db(db, dest, family_id)` | `str, str, int` | — | Family-scoped бэкап: вся схема, но только строки одной семьи |
| `get_setting(db, key, default='', family_id=1)` / `set_setting(db, key, value, family_id=1)` | `...` | `str` / — | Настройки в разрезе семьи |
| `get_effective_target(db, fallback, family_id=1)` | `str, int, int` | `int` | Целевая ПСВ (settings → fallback → 300) |
| `get_reminder_hours(db, family_id=1)` | `str, int` | `dict` | Часы напоминаний |

### `get_stats()` — структура результата

```python
{
    "total": int,           # Всего замеров
    "avg": float,           # Среднее
    "min": int,             # Минимум
    "max": int,             # Максимум
    "latest": int,          # Последний замер
    "morning_avg": float,   # Среднее утренних (или None)
    "morning_count": int,   # Кол-во утренних
    "evening_avg": float,   # Среднее вечерних (или None)
    "evening_count": int,   # Кол-во вечерних
    "today_count": int,     # Замеров сегодня
    "trend": float,         # last_3_avg - prev_3_avg (или None)
}
```

---

## Модуль `config.py`

### Функции

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `is_parent(uid)` | `int` | `bool` | ID в списке родителей |
| `is_child(uid)` | `int` | `bool` | ID == CHILD_ID |

Целевая ПСВ читается единой функцией `database.get_effective_target(db_path, fallback)`
(`settings.target_pef` → `TARGET_PEF` из `.env` → 300); используется и ботом, и Mini App.

---

## Сценарии использования

### Ребёнок: добавление замера

```
[💨 Измерение]
   ↓ (авто: 🌅 Утро)
[Введите ПСВ: 245]
   ↓
✅ 🌅 Утро: 245 л/мин 🟢
Зона: Зелёная (94% от нормы)
📈 Изменение: +5 л/мин
   ↓
[Главное меню]
```

### Родитель: просмотр сводки

```
[📊 Сводка]
   ↓
📊 Маша — сегодня, 13 апреля

🌅 Утро: 240 🟢, 245 🟢
🌆 Вечер: 260 🟢

📈 Всего: 142 | Среднее: 248
🌅 Утро avg: 243 | 🌆 Вечер avg: 255
```

### Родитель: недельный отчёт

```
[📈 Неделя]
   ↓
📋 Маша — неделя 2026-04-07 — 2026-04-13

Замеров: 12 (🌅 7 / 🌆 5)
Среднее: 248
🏆 Лучший: 270 (🌆 2026-04-12)
⚠️ Худший: 225 (🌅 2026-04-09)

📈 Эта vs прошлая:
🌅 Утро: 240 → 246 (+6) 🟢
🌆 Вечер: 255 → 251 (-4) 🟡
```

### Автоматические уведомления

| Событие | Условие | Получатели |
|---------|---------|-----------|
| Утренний пропуск | 10:00, нет 🌅 замера | Оба родителя |
| Вечерний пропуск | 22:00, нет 🌆 замера | Оба родителя |
| Недельный отчёт | Вс 21:00 | Оба родителя |
| Красная зона | Мгновенно при замере < 60% | Оба родителя |
| Новый замер | Мгновенно | Другой родитель |

### Регистрация семьи (v3) и выбор ребёнка (v4)

Неизвестный пользователь на `/start` видит экран выбора:

- **🏠 Создать семью** → имя семьи → пользователь становится родителем, получает
  родительский invite-код.
- **🔑 Войти по коду** → ввод токена → участник (роль из приглашения).
- **Deep-link** `/start <token>` → мгновенный вход по коду.

Уже зарегистрированный пользователь не может быть перемещён в другую семью по
коду — на обоих путях (ввод и deep-link) он получает «вы уже в семье» и главное меню.

Родитель в «⚙️ Настройки» → «👨‍👩‍👧 Участники» видит invite-код и может его
перегенерировать; «🧒 Дети» — карточки детей (создать/удалить), каждая с токеном
для входа ребёнка. Активный ребёнок помечается «✅»; при >1 ребёнке в главном меню
появляется кнопка «🧒 Ребёнок» (`pick_child` → `set_child_<id>`) для переключения.

**Инфраструктура ролей:** `MemberMiddleware` один раз на update читает строку
`members` в `asyncio.to_thread` и кладёт `member` в data хендлеров. Хелпер
`_role(member, uid)` возвращает роль из БД, а при `member is None` — fallback на
`.env` (семья №1). Тенант-контекст даёт `_ctx(member)` → `(family_id, active_child_id)`,
имя активного ребёнка — `_child_name`, получатели уведомлений — `_family_parents`.

---

## Запуск и тестирование

### Установка

Ручная установка:

```bash
cd /root/bot/picklo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Установка на сервер одной командой через `manage.sh`:

```bash
curl -fsSL https://raw.githubusercontent.com/itdevlog/peakflow/main/manage.sh | bash -s -- install
```

Скрипт клонирует репозиторий (по умолчанию в `/opt/peakflow`), создаёт
venv `.venv`, ставит зависимости, интерактивно настраивает `.env` и предлагает
systemd-сервис `peakflow-bot-<instance>` (`Restart=on-failure`). Управление:
`install`, `update`, `start`, `stop`, `restart`, `status`, `logs`, `backup`,
`restore`, `doctor`, `caddy`, `uninstall`, `help` (флаг `--no-color`,
`--instance ИМЯ`/`RASPISANIE_INSTANCE` — имя инстанса для нескольких ботов).

### Веб-версия (Mini App)

`web/api.py` (`create_app`) и `web/server.py` (`run_webapp`) поднимают FastAPI
в том же процессе и event loop, что и aiogram. Слой включается при
`WEBAPP_PORT > 0`; health-check — `GET /healthz` (статус и `manage.sh doctor`
проверяют `http://localhost:$WEBAPP_PORT/healthz`). Переменные `.env`:
`WEBAPP_HOST` (по умолчанию `127.0.0.1`), `WEBAPP_PORT`, `WEBAPP_URL`. HTTPS для
Mini App даёт общий Caddy через `./manage.sh caddy` (Let's Encrypt): каждый бот
пишет фрагмент `/etc/caddy/conf.d/<instance>.caddy`, базовый `/etc/caddy/Caddyfile`
импортирует `conf.d/*.caddy` (шаблон-справка — `deploy/Caddyfile.site`).

Mini App tenant-aware: `_resolve_user` берёт роль, `family_id`, `active_child_id`
и список детей из `members` (gate 2B снят — семьи ≠ №1 обслуживаются). `/api/me`
отдаёт `children` и `active_child_id`; `GET /api/children` — список детей семьи;
`PUT /api/active-child` (`{child_id}`, только родитель) меняет активного ребёнка.
Все data-эндпоинты (`/api/status`, `/api/history`, `/api/chart`, `/api/stats`,
`/api/settings`, `/api/export/*`, `/api/backup`) scoped по
`(family_id, active_child_id)`; бэкап — `backup_family_db` (данные одной семьи).
В шапке Mini App при >1 ребёнке показывается селектор (`<select>`), при 0 детей —
подсказка «Добавьте ребёнка в боте»; уведомления уходят родителям семьи.

### Настройка

```bash
cp .env.example .env
# Вставьте реальные значения
```

### Запуск

```bash
python bot.py
```

### Тесты

```bash
python -m pytest test/ -v
# 444 passed
```

Тесты запускаются без `.env`: `test/conftest.py` подставляет тестовый `DB_PATH`
и dummy-токен. В CI (GitHub Actions) дополнительно прогоняются `pyflakes` и
`compileall`.

### Dry-run миграции

`scripts/migration_dry_run.py [DB_PATH]` — предпросмотр миграции без записи в
источник. Делает **файловый** снимок БД (main + `-wal`) во временный файл,
запускает `init_db` на копии и печатает JSON-отчёт: `user_version` до/после,
таблицы и число строк, семьи/участники, orphan-`child_id`. Источник никогда не
открывается SQLite-соединением: read-only соединение трогает `-shm`, а
read-write при закрытии делает checkpoint, переписывает main-файл и удаляет
`-wal`/`-shm`. Поэтому `dry_run` не изменяет ни main, ни sidecar'ы источника;
временная копия и её sidecar'ы удаляются.

---

## ⚠️ Важное предупреждение

> Данный бот предназначен **исключительно для мониторинга и отслеживания** данных.  
> Он **не является медицинским устройством** и **не заменяет консультацию врача**.  
> Все решения по лечению должны приниматься совместно с квалифицированным специалистом.

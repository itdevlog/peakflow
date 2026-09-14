# 🫁 Пикфлоуметр — Полная техническая документация

> Telegram-бот для мониторинга пиковой скорости выдоха (ПСВ / PEF)  
> Предназначен для семьи: **1 ребёнок + 2 родителя**

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
| Напоминания | 10:00 / 22:00 — если замер пропущен |
| Красная зона | Мгновенное уведомление обоим родителям при `< 60%` |
| Уведомление родителю | При каждом замере — второму родителю |

---

## Архитектура проекта

```
picklo/
├── bot.py              # Основная логика бота (~790 строк)
├── database.py         # SQLite CRUD (~275 строк)
├── config.py           # Настройки, ID семьи, пороги (~70 строк)
├── test_bot.py         # Pytest тесты (19 тестов)
├── requirements.txt    # Python зависимости
├── .env                # Переменные окружения
├── .env.example        # Шаблон .env
├── README.md           # Краткое описание
├── PROJECT.md          # Полная документация
└── peakflow.db         # SQLite база (создаётся автоматически)
```

### Технологический стек

| Компонент | Версия | Назначение |
|-----------|--------|------------|
| Python | 3.11+ | Язык |
| aiogram | 3.x | Telegram Bot Framework |
| SQLite | 3 | Хранилище данных (WAL mode) |
| matplotlib | 3.10 | Генерация графиков |
| pytest | 9.x | Тестирование |
| python-dotenv | 1.x | Загрузка `.env` |

---

## База данных

### Таблица `measurements`

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Уникальный ID записи |
| `user_id` | INTEGER | NOT NULL | ID ребёнка (CHILD_ID) |
| `pef_value` | INTEGER | NOT NULL | Значение ПСВ (л/мин) |
| `time_of_day` | TEXT | CHECK IN ('morning', 'evening') | Время суток |
| `measured_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Дата и время записи |
| `added_by` | INTEGER | — | Telegram ID того, кто добавил |

**Индексы:** Автоматический по `id`. Рекомендация: `CREATE INDEX idx_measurements_user_time ON measurements(user_id, measured_at DESC)`.

### Таблица `reminders_sent`

| Поле | Тип | Ограничения | Описание |
|------|-----|-------------|----------|
| `date` | TEXT | PRIMARY KEY | Дата в формате `YYYY-MM-DD` |
| `morning_reminder` | INTEGER | DEFAULT 0 | Отправлено напоминание об утреннем замере |
| `evening_reminder` | INTEGER | DEFAULT 0 | Отправлено напоминание о вечернем замере |
| `weekly_report` | INTEGER | DEFAULT 0 | Отправлен недельный отчёт |

### SQL-запросы

См. функции в [`database.py`](#модуль-databasepy).

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
| `WEBAPP_HOST` | string | ❌ | `0.0.0.0` | Адрес прослушивания веб-сервера Mini App |
| `WEBAPP_PORT` | int | ❌ | `8080` | Порт веб-сервера; `0` — веб-сервер выключен |
| `WEBAPP_URL` | string | ❌ | — | Публичный HTTPS-URL Mini App; пусто — кнопка Mini App не добавляется |

### Константы в `config.py`

| Константа | Значение | Описание |
|-----------|:---:|----------|
| `ZONE_GREEN` | `80` | Порог зелёной зоны (% от нормы) |
| `ZONE_YELLOW` | `60` | Порог жёлтой зоны (% от нормы) |
| `ZONE_RED` | `50` | Порог красной зоны (% от нормы) |
| `PEF_NORM_BY_AGE` | dict | Справочник норм по возрасту (4–18 лет) |
| `REMINDER_MORNING_HOUR` | `8` | Час утреннего напоминания |
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
    waiting_for_pef = State()   # Ожидание ввода числа ПСВ
    editing_last = State()      # Ожидание нового значения для последнего замера
```

### Хелперы

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `auto_time_of_day()` | — | `str` | `"morning"` если текущий час < 12, иначе `"evening"` |
| `tod_emoji(tod)` | `tod: str` | `str` | `"🌅"` или `"🌆"` |
| `tod_label(tod)` | `tod: str` | `str` | `"Утро"` или `"Вечер"` |
| `pef_zone(value, target)` | `value: int, target: int` | `tuple[str, str]` | `(эмодзи, название_зоны)` |
| `pct_of(value, target)` | `value: int, target: int` | `int` | Процент от нормы |
| `answer_callback(cb)` | `callback: CallbackQuery` | — | ACK + удаление сообщения callback |
| `respond(cb, text, kb)` | `callback, text, kb, parse_mode` | `Message` | answer_callback + answer |
| `_user_display_name(uid)` | `user_id: int` | `str` | Имя ребёнка или «Родитель» |

### Клавиатуры

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|-----------|----------|
| `kb_main(is_parent)` | `is_parent_user: bool` | `InlineKeyboardMarkup` | Главное меню (разное для родителя/ребёнка) |
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

```python
async def on_startup():
    asyncio.create_task(scheduler_loop())

async def scheduler_loop():
    # Цикл каждую минуту:
    #   10:00 — если нет утреннего замера → напоминание родителям
    #   22:00 — если нет вечернего замера → напоминание родителям
    #   Вс 21:00 — недельный отчёт родителям
```

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
| `init_db(db_path)` | `db_path: str` | — | Создание таблиц |
| `add_measurement(db, pef, tod, uid, by)` | `str, int, str, int, int` | `int` | Добавление замера, возвращает ID |
| `edit_measurement(db, mid, val, uid)` | `str, int, int, int` | `bool` | Редактирование по ID |
| `delete_measurement(db, mid, uid)` | `str, int, int` | `bool` | Удаление по ID |
| `get_last_measurement(db, uid)` | `str, int` | `Optional[dict]` | Последний замер |
| `get_all_measurements(db, uid)` | `str, int` | `list[dict]` | Все замеры (DESC по id) |
| `get_today_measurements(db, uid)` | `str, int` | `list[dict]` | Замеры за сегодня |
| `has_today_measurement(db, uid, tod)` | `str, int, str` | `bool` | Есть ли замер today+tod |
| `get_measurements_paginated(db, uid, p, pp)` | `str, int, int, int` | `tuple` | `(замеры, всего, страниц)` |
| `get_measurements_for_chart(db, uid, days)` | `str, int, int` | `list[dict]` | Данные для графика |
| `get_stats(db, uid)` | `str, int` | `dict` | Полная статистика |
| `get_last_two_weeks(db, uid)` | `str, int` | `tuple` | `(эта_неделя, прошлая_неделя)` |
| `mark_reminder_sent(db, date, type)` | `str, str, str` | — | Отметить отправку напоминания |
| `was_reminder_sent(db, date, type)` | `str, str, str` | `bool` | Было ли напоминание сегодня |

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
| `get_effective_target()` | — | `int` | TARGET_PEF или fallback 300 |

### Справочник `PEF_NORM_BY_AGE`

| Возраст | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Норма | 140 | 170 | 200 | 230 | 260 | 290 | 320 | 350 | 380 | 410 | 440 | 470 | 500 | 530 | 560 |

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
systemd-сервис `peakflow-bot` (`Restart=on-failure`). Управление:
`install`, `update`, `start`, `stop`, `restart`, `status`, `logs`, `backup`,
`restore`, `doctor`, `caddy`, `uninstall`, `help` (флаг `--no-color`).

### Веб-версия (Mini App)

`web/api.py` (`create_app`) и `web/server.py` (`run_webapp`) поднимают FastAPI
в том же процессе и event loop, что и aiogram. Слой включается при
`WEBAPP_PORT > 0`; health-check — `GET /healthz` (статус и `manage.sh doctor`
проверяют `http://localhost:$WEBAPP_PORT/healthz`). Переменные `.env`:
`WEBAPP_HOST`, `WEBAPP_PORT`, `WEBAPP_URL`. HTTPS для Mini App даёт
`deploy/Caddyfile` через `./manage.sh caddy` (Let's Encrypt).

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
python -m pytest test_bot.py -v
# 19 passed
```

---

## ⚠️ Важное предупреждение

> Данный бот предназначен **исключительно для мониторинга и отслеживания** данных.  
> Он **не является медицинским устройством** и **не заменяет консультацию врача**.  
> Все решения по лечению должны приниматься совместно с квалифицированным специалистом.

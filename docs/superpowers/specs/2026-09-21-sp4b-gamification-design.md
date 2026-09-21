# SP4B — Геймификация: streak и достижения

> **Дата:** 2026-09-21
> **Подпроект:** Фаза 4.2 (пункт `4.2`)
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–2 в main, SP4A (схема v4, 481 тест, CI зелёный)
> **Не входит:** визуальные картинки-бейджи, лидерборды/соцсравнение, награды родителям

---

## 1. Цель

Мотивировать ребёнка ежедневно измерять ПСВ: показывать **серию дней подряд** (streak)
и **достижения** («7/30/100 дней подряд», «100/500/1000 замеров») в боте и Mini App;
один раз уведомлять семью о разблокировке.

**Успех:** streak корректно считается с учётом пропусков и таймзоны; достижения
разблокируются один раз без повторных уведомлений; данные изолированы по
`(family_id, child_id)`; существующие данные не спамят при деплое.

---

## 2. Определения

- **Активный день** — календарный день (в TZ пользователя, `TZ_OFFSET`), в который есть
  ≥1 замер. Авто-замеры **засчитываются**.
- **Текущая серия (`current_streak`)** — число подряд идущих активных дней, заканчивающихся
  сегодня. Если сегодня замеров ещё нет, но вчера был — серия считается до вчера (grace, не
  сбрасывается в течение дня). Если последний активный день раньше вчера — серия 0.
- **Рекордная серия (`longest_streak`)** — максимальная длина серии за всю историю.
- **Достижение** засчитывается, если:
  - streak-тип: `longest_streak >= threshold` (не теряется после сброса текущей серии);
  - total-тип: `total_measurements >= threshold` (все источники, включая авто).

---

## 3. Схема v5

`SCHEMA_VERSION` 4 → 5. Идемпотентно.

```sql
CREATE TABLE IF NOT EXISTS achievements (
    child_id    INTEGER NOT NULL,
    code        TEXT    NOT NULL,
    unlocked_at TEXT    NOT NULL,
    PRIMARY KEY (child_id, code)
);
```

`child_id` — telegram id ребёнка (глобально уникален), как в `reminders_sent(child_id, date)`.

---

## 4. Модуль `gamification.py` (новый, чистый)

stdlib only; **не импортирует** `bot.py`/`database.py`/`aiogram`/`matplotlib`.

```python
ACHIEVEMENTS = [
    {"code": "streak_7",    "emoji": "🔥", "title": "7 дней подряд",   "kind": "streak", "threshold": 7},
    {"code": "streak_30",   "emoji": "🔥", "title": "30 дней подряд",  "kind": "streak", "threshold": 30},
    {"code": "streak_100",  "emoji": "🏆", "title": "100 дней подряд", "kind": "streak", "threshold": 100},
    {"code": "total_100",   "emoji": "💯", "title": "100 замеров",     "kind": "total",  "threshold": 100},
    {"code": "total_500",   "emoji": "⭐", "title": "500 замеров",     "kind": "total",  "threshold": 500},
    {"code": "total_1000",  "emoji": "👑", "title": "1000 замеров",    "kind": "total",  "threshold": 1000},
]

def current_streak(dates: list[str], today: date) -> int
def longest_streak(dates: list[str]) -> int
def evaluate(streak_longest: int, total: int) -> set[str]
def achievement_status(code: str, streak_longest: int, total: int) -> tuple[bool, int, int]
    # (unlocked, current, threshold) для отображения прогресса
```

- `dates` — ISO-строки `YYYY-MM-DD` (могут быть не отсортированы, с дублями); функции
  нормализуют во множество `date`.
- `current_streak`: сначала отбрасываются даты `> today`; `latest = max(остатка)`; если
  `latest == today` — старт сегодня, если `latest == today - 1` — старт вчера, иначе 0; далее
  счёт назад по множеству до первого пропуска.
- `longest_streak`: сортировка уникальных дат и поиск максимального прогона.
- `evaluate`: коды, для которых порог достигнут (streak — по `streak_longest`).

---

## 5. Данные (`database.py`)

```python
get_measurement_dates(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> list[str]
    # DISTINCT substr(measured_at,1,10), ORDER BY date ASC; все источники
count_measurements(db_path, child_id, family_id=DEFAULT_FAMILY_ID) -> int
    # все источники, включая auto
get_achievements(db_path, child_id) -> dict[str, str]        # {code: unlocked_at}
unlock_achievements(db_path, child_id, codes, when) -> set[str]
    # INSERT OR IGNORE; возвращает ТОЛЬКО реально вставленные коды
```

- Миграция `_create_achievements_v5(conn)` вызывается из `init_db`.
- В `init_db` запоминается `old_version` до апгрейда; **бэкфилл** запускается только при
  `old_version < 5`: для каждого ребёнка считается `evaluate(longest_streak(dates), total)` и
  заслуженные коды вставляются с `unlocked_at = сегодня` **без уведомлений** (чтобы после
  деплоя не спамить). Бэкфилл идемпотентен (`INSERT OR IGNORE`). `unlocked_at` — локальная
  дата-метка для отображения, точность TZ не критична.

---

## 6. Бот

- **Статус-блок** (`build_status_block`): добавляет строку `🔥 Серия: N дн.` (если `N >= 1`)
  и, при наличии, краткую строку последнего бейджа.
- **Экран «🏅 Достижения»** (`callback_data="achievements"`):
  - доступен ребёнку (свои) и родителю (активный ребёнок); `_ctx(member)`, `child_id is None`
    → `_no_child_reply`;
  - для каждого достижения: разблокировано → `✅ {emoji} {title}`; нет →
    `⬜ {emoji} {title} — {current}/{threshold}`;
  - кнопка «⬅️ Назад».
- **Кнопки**: «🏅 Достижения» в меню ребёнка (`kb_main` ветка child) и в меню родителя; на
  экране достижений — назад.
- **Разблокировка и уведомление**: при сохранении замера (`_persist_measurement`, после
  успешной записи) — `_evaluate_and_notify`:
  1. `dates = get_measurement_dates`, `total = count_measurements`;
  2. `earned = gamification.evaluate(longest_streak(dates), total)`;
  3. `new = unlock_achievements(child_id, earned, today)` — уведомляем **только новые**;
  4. получатели: `({child_id} ∪ родители семьи) \ {who}`; одно сообщение со списком новых
     достижений: `🎉 Новое достижение: {emoji} {title}`;
  5. данные — под `(family_id, child_id)`; `member=None` fallback сохраняется.
- Уведомление идёт после «измерение сохранено»; повторный замер не уведомляет (код уже в БД).

---

## 7. Web / Mini App

- `GET /api/gamification` (`require_user` — доступен и ребёнку, и родителю) →
  ```json
  {"streak_current": 5, "streak_longest": 12, "total": 130,
   "achievements": [{"code": "streak_7", "emoji": "🔥", "title": "7 дней подряд",
                     "unlocked": true, "unlocked_at": "2026-09-01"}]}
  ```
  - Доступ: `require_user` (ребёнок видит свои, родитель — активного ребёнка);
    `active_child_id is None` → `404`.
  - `unlocked` — по `gamification.evaluate`; `unlocked_at` — из `get_achievements`
    (может быть `None`, если заслужено авто-замерами и ещё не персистировано).
  - Изоляция: `get_measurement_dates`/`count_measurements` с `auth["family_id"]`.
- Mini App: экран «Статистика» — строка «🔥 Серия: N дн.» и сетка бейджей (✅/⬜).

---

## 8. Обработка ошибок и краевые случаи

| Ситуация | Поведение |
|----------|-----------|
| Нет замеров | `streak_current=0`, `longest=0`, все бейджи ⬜ (`current=0`) |
| Разрыв в датах | серия считается только по подряд идущим дням |
| День только с авто-замером | засчитывается как активный |
| Разблокировка авто-замером | достижение всплывёт/персистируется при следующем сохранении; без спама |
| Нет активного ребёнка (веб) | `404` |
| Нет ребёнка (бот) | `_no_child_reply` |
| Несколько детей в семье | всё scoped по активному `child_id` |

---

## 9. Тестирование (TDD)

1. `test/test_gamification.py` (новый):
   - `current_streak`: пусто; сегодня; заканчивается вчера (grace); разрыв → 0/частичная;
     неотсортированные даты; дубли; даты в будущем отбрасываются и не ломают серию.
   - `longest_streak`: пусто; одна дата; два прогона → максимум; все подряд.
   - `evaluate`: ниже/на пороге/выше для streak и total.
   - `achievement_status`: unlocked/current/threshold для streak и total.
2. `test/test_bot.py` (db + хендлеры):
   - схема v5: таблица `achievements` есть, `SCHEMA_VERSION == 5`, миграция идемпотентна;
   - `get_measurement_dates` (distinct, sorted, все источники);
   - `unlock_achievements` идемпотентен и возвращает только новые;
   - бэкфилл: при апгрейде со старой версии вставляет заслуженные коды без уведомлений;
   - `_persist_measurement` пересекает порог → код в БД + уведомление родителям один раз;
     повторный замер не уведомляет;
   - `cb_achievements` показывает бейджи; parent-only/child wiring.
3. `test/test_webapp_api.py`:
   - `/api/gamification` → 200, поля присутствуют; `active_child_id is None` → 404;
   - изоляция двух семей (семья B видит только своего ребёнка).
4. Регрессия: `pytest test/`; `pyflakes` + `compileall` (добавить `gamification.py` в CI-список
   и в проверочную команду); обновить счётчики тестов в `PROJECT.md`, `wiki.md`, todo-plan.

---

## 10. Критерии приёмки

- [ ] Streak и рекорд считаются верно (сегодня/вчера/разрыв/пусто), с учётом TZ.
- [ ] 6 достижений разблокируются по правилам; отображаются в боте и Mini App.
- [ ] Уведомление о новом достижении приходит **один раз**; повторные замеры не спамят.
- [ ] После деплоя старые данные не вызывают уведомлений (бэкфилл без пуша).
- [ ] Изоляция `(family_id, child_id)` подтверждена тестами двух семей.
- [ ] Схема v5, миграция идемпотентна; `member=None` fallback сохранён.
- [ ] `pyflakes` + `compileall` + все тесты зелёные; документация/счётчики обновлены.

---

## 11. Риски и решения

| Риск | Решение |
|------|---------|
| Спам при первом деплое | бэкфилл только при `old_version < 5`, без уведомлений |
| Повторные уведомления | `PRIMARY KEY(child_id, code)` + `INSERT OR IGNORE`, уведомляем только новые |
| Сброс streak пугает | streak-достижения по `longest_streak` |
| Таймзона/полночь | границы дня по `TZ_OFFSET`; grace на сегодня |
| Авто-замеры «накручивают» | осознанно: авто = забота о ребёнке; задокументировано |
| Циклический импорт | `gamification` чистый; `database` импортирует его для бэкфилла |

---

## 12. Вне scope SP4B

- Картинки/ассеты бейджей (только emoji и текст).
- Лидерборды и сравнение между детьми/семьями.
- Награды родителям, шеринг в соцсети.
- Отдельные пуши из планировщика (достижения от авто-замеров всплывут при следующем замере).
- Геймификация в PDF-отчёте врачу.

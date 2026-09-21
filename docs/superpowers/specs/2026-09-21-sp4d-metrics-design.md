# SP4D — Метрики и structured-логи

> **Дата:** 2026-09-21
> **Подпроект:** Фаза 4.4 (пункт `4.4`)
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–2, SP4A/SP4B в main (схема v5, 521 тест, CI зелёный)
> **Не входит:** JSON-логи, Sentry, ротация файлов, Grafana-дашборды, алертинг, гистограммы/перцентили, multi-process метрики

---

## 1. Цель

Дать наблюдаемость для self-hosted развёртывания без новых зависимостей: единый
in-process реестр метрик, расширенный `/healthz` (uptime + ключевые счётчики),
опциональный `GET /metrics` в формате Prometheus и аккуратные текстовые логи с
устойчивыми полями по запросам, ошибкам и планировщику.

**Успех:** владелец видит, жив ли бот, когда был последний тик планировщика, сколько
замеров/семей/детей и какова частота запросов/ошибок; метрики не отдаются наружу по
умолчанию; логи остаются читаемыми в systemd; новых зависимостей нет.

---

## 2. Решения (утверждены владельцем 2026-09-21)

- Формат логов — **текстовый, как сейчас** (structured по стабильным полям `key=value`).
- Покрытие — **HTTP-запросы + ошибки + планировщик**.
- Метрики — **расширенный `/healthz`** + **опциональный `/metrics`** (Prometheus-text,
  своими руками, env-gated).
- Вывод — **только stdout/stderr** (systemd/journald).

---

## 3. Модуль `metrics.py` (новый, stdlib, чистый)

Не импортирует `bot.py`/`database.py`/`aiogram`. Собственный `threading.Lock` —
вызовы приходят из веб-потока и из `asyncio.to_thread`.

```python
START_TIME: float          # time.time() при импорте
def uptime_seconds() -> float

def inc(name: str, value: int = 1, **labels) -> None
def set_gauge(name: str, value: float, **labels) -> None
def get_counter(name: str, **labels) -> int
def snapshot() -> list[dict]          # [{"name","type","labels","value"}, ...]
def render_prometheus() -> str        # text/plain; version=0.0.4
def reset() -> None                   # только для тестов
```

- Метки — плоский `dict[str, str]`; ключ реестра — `(name, tuple(sorted(labels)))`.
- `render_prometheus`: `# TYPE <name> counter|gauge`, строки `name{label="v"} value`;
  имена/метки валидируются (только `[a-zA-Z_:][a-zA-Z0-9_:]*`, метки `[a-zA-Z_][a-zA-Z0-9_]*`),
  некорректные — `ValueError` (защита от инъекции формата).
- `reset()` очищает реестр и **не** трогает `START_TIME`.

---

## 4. Точки сбора

### Web (`web/api.py`)
- Middleware `@app.middleware("http")`: после обработки запроса
  `inc("http_requests_total", method=METHOD, status=str(status_code))` и
  `set_gauge("http_last_duration_ms", ms)`; строка лога
  `logger.info("http method=%s path=%s status=%s duration_ms=%.1f", ...)`.
  5xx дополнительно `logger.warning(...)`.
- Гейдж по БД на момент скрейпа: `set_gauge("families_count", ...)`,
  `children_count`, `measurements_count` — из нового
  `database.get_system_counts(db_path)`.

### Бот (`bot.py`)
- `_persist_measurement` → `inc("measurements_saved_total")`.
- `_evaluate_and_notify` → `inc("achievement_notifications_total")` при отправке.
- `scheduler_loop` (каждый тик) → `inc("scheduler_ticks_total")`,
  `set_gauge("scheduler_last_tick_timestamp", time.time())`;
  отправленные напоминания → `inc("reminders_sent_total")`.

Метрики процесса — в памяти; при рестарте сбрасываются (это ожидаемо).

---

## 5. `/healthz` (расширение, обратно совместимо)

Успех:
```json
{"status": "ok", "uptime_seconds": 123.4, "last_scheduler_tick": 1758441000.0,
 "families": 2, "children": 3, "measurements": 42}
```
- `last_scheduler_tick` — `null`, пока не было тика.
- При `state["bot_ok"] is False` — прежний `503 {"status": "bot down"}`.
- Внешние healthcheck'и (systemd/`manage.sh`/Docker) проверяют только 200/503 —
  контракт кода ответа не меняется. Существующий тест на точное равенство словаря
  обновляется на проверку подмножества.

---

## 6. `GET /metrics` (Prometheus text)

- Включается env `METRICS_ENABLED` (default `"0"`); выключено → `404`.
- Опциональный `METRICS_TOKEN`: если задан, требуется заголовок
  `Authorization: Bearer <token>` (иначе `401`).
- Ответ: `PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")`
  со счётчиками запросов, планировщика и гейджами БД/процесса.
- Веб слушает `127.0.0.1` (`WEBAPP_HOST`), но сгенерированный Caddy-vhost
  reverse-proxy-ит **все** пути, включая `/metrics`, поэтому при публичном
  `WEBAPP_URL` токен обязателен. Персональных данных в метриках нет (только числа).

Новые ключи `config.py`: `METRICS_ENABLED = os.getenv("METRICS_ENABLED", "0") == "1"`,
`METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")`.

---

## 7. Данные

`database.get_system_counts(db_path) -> dict`:
```python
{"families": <int>, "children": <int>, "measurements": <int>}
```
- `families` — `COUNT(*) FROM families`;
- `children` — `COUNT(*) FROM members WHERE role='child'`;
- `measurements` — `COUNT(*) FROM measurements`.
Только агрегаты, без персональных данных.

---

## 8. Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| Метрика вне `reset()` не определена | `get_counter` → 0 (без исключения) |
| Некорректное имя/метка | `ValueError` на записи (баг программиста, не вход пользователя) |
| `/metrics` выключен | `404` |
| `/metrics` с токеном без/с неверным `Authorization` | `401` |
| Нет тика планировщика | `last_scheduler_tick = null` |
| Ошибка подсчёта БД в healthz | поля `null`, статус `ok` не падает (healthz не должен падать) |

---

## 9. Тестирование (TDD)

1. `test/test_metrics.py` (новый):
   - `inc`/`set_gauge`/`get_counter` с метками; разные метки — разные ряды;
   - `reset` очищает, `START_TIME`/`uptime_seconds` живы;
   - `render_prometheus`: `# TYPE`, корректные строки, экранирование значения метки;
   - невалидное имя/метка → `ValueError`.
2. `test_webapp_api.py`:
   - `/healthz` содержит `status`, `uptime_seconds`, `families`, `children`,
     `measurements`; `bot_ok=False` → `503`;
   - `/metrics` выключен → `404`; включён → `200`, `text/plain`, содержит
     `http_requests_total`;
   - с `METRICS_TOKEN` без заголовка → `401`, с верным Bearer → `200`;
   - после запроса middleware инкрементит `http_requests_total`.
3. `test_bot.py`:
   - `_persist_measurement` инкрементит `measurements_saved_total`;
   - тик планировщика ставит `scheduler_ticks_total` и `scheduler_last_tick_timestamp`
     (юнит на соответствующую функцию).
   - `get_system_counts` возвращает корректные агрегаты.
4. Регрессия: `pytest test/`; `pyflakes` + `compileall` (добавить `metrics.py` в CI-список
   и в проверочную команду); обновить счётчики в `PROJECT.md`, `wiki.md`, todo-plan.

---

## 10. Критерии приёмки

- [ ] `metrics.py` отдаёт счётчики/гейджи и Prometheus-text без новых зависимостей.
- [ ] `/healthz` отдаёт uptime + last tick + семьи/дети/замеры; `503` при bot down.
- [ ] `/metrics` по умолчанию `404`; при включении — валидный Prometheus-text; токен соблюдается.
- [ ] Запросы, ошибки и планировщик отражаются в метриках и логах с устойчивыми полями.
- [ ] Логи остаются текстовыми; вывод — stdout.
- [ ] Персональные данные в метрики не попадают.
- [ ] `pyflakes` + `compileall` + все тесты зелёные; документация/счётчики обновлены.

---

## 11. Риски и решения

| Риск | Решение |
|------|---------|
| Метрики утекают наружу | off by default, рекомендованный токен при публичном `WEBAPP_URL` |
| Гонки при записи метрик | `threading.Lock` в реестре |
| `/metrics` замедляет запросы | middleware считает после ответа; реестр в памяти |
| healthz падает из-за БД | ошибки подсчёта → `null`, статус не рушится |
| Инъекция в Prometheus-формат | валидация имён/меток |
| Сброс метрик при рестарте | ожидаемо; uptime показывает свежесть |

---

## 12. Вне scope SP4D

- JSON-формат логов, Sentry, файловая ротация.
- Grafana-дашборды, алертинг, Prometheus-сервер в репозитории.
- Гистограммы/перцентили задержек (только последняя длительность + счётчики).
- Метрики по нескольким процессам/инстансам.

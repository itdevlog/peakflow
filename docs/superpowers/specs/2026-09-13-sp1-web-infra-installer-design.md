# Design: SP1 — инфраструктура web-слоя и установщик

**Дата:** 2026-09-13
**Статус:** утверждён (в чате, brainstorming)
**Объём:** in-process FastAPI/uvicorn рядом с aiogram, `/healthz`, конфиг `WEBAPP_*`, полный `manage.sh`, systemd, `deploy/Caddyfile`.
**Не входит (подпроект SP2):** auth initData, REST API замеров/графиков/настроек, фронтенд Mini App.
**Источник:** эталон `github.com/itdevlog/raspisanie` (manage.sh + web/server.py + deploy/Caddyfile).

---

## 1. Контекст

Бот пикфлоуметрии — aiogram 3.x + SQLite, запуск ручной (`python bot.py`).
Есть реальная прод-БД с медицинскими данными (183 замера), но нет:
системы установки/обновления, автозапуска, бэкапов и health-check.
Roadmap №23 уже формулирует эту потребность.

Эталонный бот `raspisanie` решает то же и имеет проверенный `manage.sh` (907 строк)
и Mini App на FastAPI в одном процессе с ботом. SP1 переносит инфраструктуру,
чтобы SP2 (полноценный Mini App) встал поверх без переделок.

## 2. Архитектура

### 2.1 Web в процессе бота (вариант A)

Один процесс, один event loop, один systemd-юнит, одна БД:

```
web/
  __init__.py    # пусто
  server.py      # run_webapp(services), wait_forever(), _defer_shutdown_signals()
  api.py         # create_app(services): GET /healthz (статика добавится в SP2)
```

- `web/server.py` — копия эталонной логики: uvicorn.Server поверх `create_app`,
  с `_defer_shutdown_signals()` (гасит повторную доставку SIGINT/SIGTERM, чтобы
  `finally`-очистка успела выполниться).
- `web/api.py` — `create_app(services)` возвращает `FastAPI`; в SP1 единственный
  эндпоинт `GET /healthz → {"status": "ok"}`. Фронтенд/статика — SP2.
- `services` — dict с `config` (и позже `bot_data`); передаётся в `create_app`.

### 2.2 Точка входа bot.py

Сейчас `main()`:
```python
dp.startup.register(on_startup)
dp.run_polling(bot)
```

Станет:
```python
def main():
    # ... существующие проверки BOT_TOKEN/CHILD_ID/PARENT_IDS ...
    lock_fd = acquire_lock()
    try:
        init_db(DB_PATH)
        asyncio.run(run_async())
    finally:
        release_lock(lock_fd)


async def run_async():
    dp.startup.register(on_startup)
    polling = asyncio.create_task(dp.start_polling(bot))
    try:
        if WEBAPP_PORT:
            await run_webapp(services_from_config())
        else:
            await wait_forever()      # поведение как сейчас: только polling
    finally:
        polling.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await polling
        await bot.session.close()
```

Поведение при `WEBAPP_PORT=0` — идентично текущему (polling до сигнала).
При `WEBAPP_PORT>0` uvicorn владеет обработкой сигналов и держит процесс;
polling идёт параллельной задачей.

### 2.3 Обработка сигналов и завершение

- uvicorn перехватывает SIGINT/SIGTERM (`should_exit`), затем `server.serve()`
  возвращает управление → `finally` отменяет polling и закрывает сессию бота.
- `_defer_shutdown_signals()` из `web/server.py` предотвращает преждевременную
  смерть процесса от повторно доставленного сигнала (детали — в комментарии
  функции; логика переносится из эталона дословно).
- `release_lock` в `finally` снимает `.bot.lock`.

## 3. Конфигурация

### 3.1 `.env.example` (добавляются)

```
# Веб-версия (Telegram Mini App), работает в процессе бота
WEBAPP_HOST=0.0.0.0
WEBAPP_PORT=8080          # 0 = веб-сервер выключен (бот работает как раньше)
WEBAPP_URL=               # публичный HTTPS-URL; пусто = кнопка Mini App не ставится
```

Существующие ключи (`BOT_TOKEN`, `CHILD_ID`, `PARENT_IDS`, `CHILD_NAME`,
`TARGET_PEF`, `DB_PATH`, `TZ_OFFSET`) — без изменений.

### 3.2 `config.py`

Добавляются:
```python
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080") or "0")   # мусор → 0, не падать
WEBAPP_URL  = normalize_webapp_url(os.getenv("WEBAPP_URL", ""))
```

`normalize_webapp_url(url)` — как в эталоне: убирает хвостовой `/`,
пустая строка остаётся пустой, невалидная схема → пусто. Точная реализация:
- strip whitespace + `rstrip("/")`;
- `"" → ""`;
- если нет `http://`/`https://` — вернуть `""` (Telegram принимает только http/https);
- иначе как есть.

## 4. `manage.sh`

Порт эталона (`raspisanie/manage.sh`) с адаптацией под этот бот.

### 4.1 Константы

| Переменная | Значение |
|-----------|----------|
| `REPO_URL` | `https://github.com/itdevlog/peakflow.git` |
| `INSTALL_DIR_DEFAULT` | `/opt/peakflow` |
| `SERVICE_NAME` | `peakflow-bot` |
| `VENV_DIR` | `${SCRIPT_DIR}/.venv` |
| `ENV_FILE` / `ENV_EXAMPLE` | `${SCRIPT_DIR}/.env` / `.env.example` |
| `DB_FILE` | `peakflow.db` (из `DB_PATH` в `.env`, дефолт) |
| `BACKUP_DIR` | `${SCRIPT_DIR}/backups` |
| `PID_FILE` | `${SCRIPT_DIR}/bot.pid` |
| `HEALTH_TIMEOUT` | `30` |

### 4.2 Команды

Полный набор: `install`, `update`, `start`, `stop`, `restart`, `status`,
`logs`, `backup`, `restore`, `doctor`, `caddy`, `uninstall`, `help`.
Флаг `--no-color`; `curl | bash` bootstrap (клонирует в `INSTALL_DIR_DEFAULT`).

- **install** — проверка git/curl, поиск Python 3.11+, venv, `pip install -r
  requirements.txt`, создание `data/ logs/ backups/`, интерактивная настройка
  `.env` (токен, `CHILD_ID`, `PARENT_IDS`, `TZ_OFFSET`, `WEBAPP_PORT`),
  установка systemd-юнита (с подтверждением). Без tty — копия `.env.example`.
- **update** — проверка чистоты git, `fetch`/`pull --ff-only`, обновление
  зависимостей, бэкап, restart, health-check, авто-откат при сбое.
- **start / stop / restart** — через systemd, иначе nohup + `bot.pid`.
- **status / logs** — `systemctl status` / `journalctl`, иначе `bot.pid` /
  `logs/bot.log`.
- **backup** — `peakflow.db` (+ `-wal`, `-shm`) и `.env` в
  `backups/bot-backup-<stamp>.tar.gz`; хранит последние 10.
- **restore** — последний бэкап, с подтверждением.
- **doctor** — venv, зависимости, `.env`, `BOT_TOKEN`/`CHILD_ID`/`PARENT_IDS`,
  сервис/процесс, `/healthz` (если `WEBAPP_PORT != 0`).
- **caddy** — ставит Caddy, пишет `/etc/caddy/Caddyfile` из `deploy/Caddyfile`,
  `WEBAPP_DOMAIN`/`WEBAPP_PORT` через systemd drop-in, Let's Encrypt.
- **uninstall** — stop + удаление сервиса (с подтверждениями).

### 4.3 Health-check

Как в эталоне:
- `WEBAPP_PORT=0` → проверка живости процесса (systemd active / `bot.pid` /
  внешний `python.*bot.py`).
- `WEBAPP_PORT>0` → `curl -sf http://localhost:${port}/healthz` с ожиданием
  до `HEALTH_TIMEOUT`.

### 4.4 Бэкап БД

Приоритет — консистентность:
1. Если доступен `sqlite3` CLI и БД существует → `sqlite3 "$DB" ".backup
   '<tmp>'"` (корректно при WAL).
2. Иначе — остановка бота, `cp` БД (+ `-wal`/`-shm`), повторный запуск.
   Файл кладётся в tar вместе с `.env`.

## 5. systemd-юнит

```ini
[Unit]
Description=Telegram Peakflow Bot
After=network.target

[Service]
Type=simple
User=<user>
WorkingDirectory=<SCRIPT_DIR>
ExecStart=<VENV_DIR>/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`install_service()` включает автозапуск; если токен не задан — enable без
`--now` и предупреждение.

## 6. `deploy/Caddyfile`

Копия эталона:
```
{$WEBAPP_DOMAIN:localhost} {
	reverse_proxy 127.0.0.1:{$WEBAPP_PORT:8080}
}
```
`manage.sh caddy` подставляет домен из `WEBAPP_URL` и порт из `WEBAPP_PORT`.

## 7. Зависимости

`requirements.txt` дополняется:
```
fastapi==0.141.1
uvicorn==0.52.4
```
Пины переносятся из эталона (`raspisanie`) — там они проверены на Python 3.11;
dev-требования не меняются.

## 8. Тесты

- `tests/test_webapp_api.py` — `create_app({...})` через FastAPI TestClient:
  `GET /healthz` → 200, `{"status": "ok"}`.
- `tests/test_config_webapp.py` — `WEBAPP_PORT` (число, `"0"`, мусор → 0),
  `normalize_webapp_url` (пусто, без схемы, со слешем, с `http://`).
- `test_bot.py` не трогаем (импортирует `bot.py` целиком).

## 9. Документация

Обновить `README.md`, `PROJECT.md`, `wiki.md`, `roadmap.md`:
- раздел установки через `manage.sh` / `curl | bash`;
- переменные `WEBAPP_*`;
- roadmap №23 → выполнено (SP1), отметить прогресс по №21/24 при необходимости.

## 10. Файлы

| Файл | Изменения |
|------|-----------|
| `web/__init__.py` | новый, пустой |
| `web/server.py` | новый (~65 строк, порт эталона) |
| `web/api.py` | новый: `create_app` + `/healthz` (~25 строк; расширится в SP2) |
| `bot.py` | `main()` → `asyncio.run(run_async())`; `run_async` рядом с polling (~+40) |
| `config.py` | `WEBAPP_*` + `normalize_webapp_url` (~+20) |
| `manage.sh` | новый (~600–900 строк, порт эталона с адаптацией) |
| `deploy/Caddyfile` | новый (12 строк) |
| `.env.example` | `WEBAPP_*` (~+6) |
| `requirements.txt` | `fastapi`, `uvicorn` |
| `tests/test_webapp_api.py` | новый |
| `tests/test_config_webapp.py` | новый |
| `README.md`, `PROJECT.md`, `wiki.md`, `roadmap.md` | документация установки |

## 11. Открытые вопросы — нет

Решено в чате: scope — полный порт `manage.sh`; web — in-process FastAPI/uvicorn;
health-check — HTTP `/healthz`; декомпозиция SP1→SP2, спека на SP1.

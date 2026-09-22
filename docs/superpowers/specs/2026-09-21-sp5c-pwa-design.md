# SP5C — PWA: установка и офлайн app-shell

> **Дата:** 2026-09-21
> **Подпроект:** Фаза 5.3
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–4, SP5A/SP5B в main (561 тест, CI зелёный)
> **Не входит:** офлайн-кэш данных API, push/background-sync, PNG-иконки, iOS-специфика

---

## 1. Цель

Сделать Mini App устанавливаемым (Add to Home Screen) и устойчивым к отсутствию сети:
манифест + service worker, кэширующий **app-shell** (статика). Данные замеров по сети
не кэшируются (приватность медданных). Работает как progressive enhancement —
регистрация выполняется там, где есть поддержка Service Worker; в окружениях без
него (часть WebView) она просто не выполняется, ничего не ломая (install не
покажется, но приложение работает).

**Успех:** при открытии в браузере по `WEBAPP_URL` (HTTPS через Caddy) приложение
устанавливается; при офлайне открывается оболочка с понятным состоянием; API всегда
идёт в сеть; обновление статики подхватывается без ручной чистки кэша.

---

## 2. Файлы

- `web/static/manifest.json` (новый) — PWA-манифест.
- `web/static/icon.svg` (новый) — SVG-иконка `any maskable`.
- `web/static/sw.js` (новый) — service worker.
- `web/static/index.html` — линк манифеста/иконки/theme-color + регистрация SW.
- `test/test_webapp_api.py` — статические проверки (файлы/поля/токены).

Статика отдаётся `StaticFiles` из `web/static` в корень (`/`), поэтому пути —
`/manifest.json`, `/icon.svg`, `/sw.js`; scope SW = `/`.

---

## 3. `manifest.json`

```json
{
  "name": "Пикфлоуметр",
  "short_name": "Пикфлоуметр",
  "description": "Дневник пикфлоуметрии для семьи",
  "start_url": "./",
  "scope": "./",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#2ea6ff",
  "icons": [
    {"src": "./icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
  ]
}
```

(`theme_color` совпадает с акцентом клиента `--accent`.)

---

## 4. `icon.svg`

Простая масштабируемая иконка: скруглённый квадрат-фон акцентного цвета и
стилизованная «кривая дыхания»/цифра. Без внешних зависимостей и шрифтов; только
базовые фигуры (`rect`, `path`). `viewBox="0 0 512 512"`.

---

## 5. `index.html`

В `<head>`:

```html
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#2ea6ff">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
```

Перед закрытием `<body>` (после `app.js`) — регистрация SW:

```html
<script>
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
</script>
```

Регистрация обёрнута в `catch` — в окружениях без SW (часть WebView) просто ничего не
происходит.

---

## 6. `sw.js`

- Константа версии: `const CACHE = "peakflow-v1";` и список `SHELL = ["/", "/index.html", "/app.js", "/style.css", "/manifest.json", "/icon.svg"]`.
- **install**: `caches.open(CACHE)` → `addAll(SHELL)`; `self.skipWaiting()`.
- **activate**: удалить все кэши, кроме `CACHE`; `self.clients.claim()`.
- **fetch** (`GET`, same-origin только):
  - путь начинается с `/api/`, `/healthz`, `/metrics` → **не перехватывать** (network-only, `return` без `respondWith`), чтобы данные и метрики всегда шли в сеть.
  - навигация (`request.mode === "navigate"`) → network-first: сеть → при ошибке `caches.match("/index.html")`.
  - прочее (статика) → **network-first**: сеть (запись в кэш только при `response.ok`, через `event.waitUntil`) → при ошибке fallback на кэш. Онлайн всегда отдаёт свежую версию (нет «залипания» статики), офлайн — закэшированный app-shell.
  - cross-origin (Telegram SDK, etc.) → не перехватывать.
- Никаких `importScripts`/workbox — только нативный Cache API.

---

## 7. Обработка ошибок и краевые случаи

| Ситуация | Поведение |
|----------|-----------|
| Нет HTTPS | SW не регистрируется; приложение работает как раньше |
| Окружение без поддержки SW (часть WebView) | guard `"serviceWorker" in navigator` ложно → регистрация не выполняется; UI не ломается |
| Офлайн, аппшелл в кэше | навигация отдаёт `index.html`; API-запросы падают штатной ошибкой клиента |
| Офлайн, аппшелл не кэширован | обычная ошибка браузера (как без PWA) |
| Обновление статики | network-first: онлайн всегда свежая версия; плюс `skipWaiting`+`clients.claim` и чистка старых кэшей |
| API/метрики | никогда не кэшируются |
| `addAll(SHELL)` частично падает | `install` отклоняется; старый SW/кэш остаётся — деградация без поломки |

---

## 8. Тестирование

Статические проверки (JS/SW не запускаются в pytest):

1. `manifest.json` существует, парсится `json.load`, содержит `name`, `short_name`,
   `start_url`, `display=="standalone"`, непустой `icons` с `type=="image/svg+xml"`.
2. `icon.svg` существует и начинается с `<svg`.
3. `index.html` содержит `rel="manifest"`, `theme-color`, `icon.svg` и регистрацию
   `navigator.serviceWorker.register("/sw.js")`.
4. `sw.js` содержит: `peakflow-v` (версия), `/api/` (байпас), `skipWaiting`,
   `clients.claim`, `addAll`, и обработку `"navigate"`.
5. `sw.js` — network-first для навигации и статики (`networkFirst`,
   fallback на `/index.html`); `response.ok` + `event.waitUntil` для записи в кэш;
   `/healthz` и `/metrics` в байпасе.
6. Регрессия: `pytest test/`; `pyflakes`/`compileall`; счётчики в `PROJECT.md`/`wiki.md`/todo-plan.

---

## 9. Критерии приёмки

- [ ] Манифест валиден, иконка- SVG, `display=standalone`.
- [ ] `index.html` линкует манифест/иконку и регистрирует SW с guard'ом.
- [ ] SW кэширует app-shell, версионирует кэш, активируется и чистит старьё.
- [ ] `/api/`, `/healthz`, `/metrics` никогда не кэшируются.
- [ ] Офлайн-навигация отдаёт оболочку; онлайн статика всегда свежая; данные — с понятной ошибкой, без «залипшего» кэша.
- [ ] В окружениях без поддержки SW ничего не ломается.
- [ ] `pyflakes` + `compileall` + все тесты зелёные; документация/счётчики обновлены.

---

## 10. Риски и решения

| Риск | Решение |
|------|---------|
| Кэш «замораживает» старую версию | network-first для статики (онлайн свежо) + версионированный `CACHE` + `skipWaiting`/`clients.claim` + чистка |
| Медданные попадают в кэш | API/SW-байпас, кэшируем только статику |
| Окружение без поддержки SW (часть WebView) | progressive enhancement, guard + `catch` |
| SVG-иконка не везде для install | принять как компромисс; PNG — отдельная задача |
| `StaticFiles` отдаёт `.json`/`.js` с верным типом | `manifest.json` как JSON, `.js` как JS — ок |

---

## 11. Вне scope SP5C

- Офлайн-хранение данных замеров (IndexedDB) и синхронизация при возврате в сеть.
- Push-уведомления, Background Sync, Periodic Sync.
- PNG-растр (`192/512`) и iOS splash-экраны.
- Изменение `deploy/Caddyfile`/`manage.sh` (HTTPS уже настроен).

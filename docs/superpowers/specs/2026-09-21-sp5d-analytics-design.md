# SP5D — Аналитика: зоны, heatmap недели, тренд

> **Дата:** 2026-09-21
> **Подпроект:** Фаза 5.4
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–4, SP5A–SP5C в main (565 тестов, CI зелёный)
> **Не входит:** календарная heatmap месяца, таблица прогноза на 7 дней, корреляция заметок (SP5E), сравнение детей

---

## 1. Цель

Дать семье простую аналитику ПСВ на отдельной вкладке: распределение замеров по зонам
(pie), средний ПСВ по дням недели (heatmap) и линейный тренд по последним двум неделям
(мини-график с линией регрессии). Данные — по активному ребёнку семьи.

**Успех:** вкладка «Аналитика» показывает три блока; данные изолированы по
`(family_id, child_id)`; пустые данные не дают ошибок; расчёты покрыты unit-тестами.

---

## 2. Модуль `analytics.py` (новый, чистый)

stdlib only; не импортирует `bot.py`/`database.py`/`aiogram`/`matplotlib`; может
импортировать чистые хелперы из `report.py`.

```python
def zone_distribution(rows, target, zone_green=80, zone_yellow=60) -> dict
    # {"green": n, "yellow": n, "red": n}; зона через report.pef_zone

def weekday_averages(rows, target, zone_green=80, zone_yellow=60) -> list
    # 7 элементов (Пн=0..Вс=6): {"avg": float, "count": int, "zone": "green"} | None

def linear_fit(values) -> tuple[float, float]
    # (slope, intercept) МНК по x=0..n-1; n==0 -> (0.0, 0.0); n==1 -> (0.0, values[0])
```

- `weekday_averages`: дата из `measured_at[:10]` → `date.fromisoformat(...).weekday()`.
- `linear_fit`: обычный метод наименьших квадратов; знаменатель 0 невозможен при `n>=2`.

---

## 3. API `GET /api/analytics`

`require_user`; активный ребёнок/семья из `auth`; `active_child_id is None` → `404`.

Ответ:

```json
{
  "zones": {"green": 12, "yellow": 4, "red": 1},
  "weekday": [{"avg": 250.0, "count": 3, "zone": "green"}, null, null, null, null, null, null],
  "trend": {
    "n": 10, "slope": 1.1, "intercept": 240.0, "per_week": 7.7,
    "daily": [{"date": "2026-09-08", "avg": 248.0}, {"date": "2026-09-09", "avg": 250.0}]
  }
}
```

- `zones` и `weekday` — по всем замерам: `get_measurements_between(child, "2000-01-01", today, family_id)`.
- `trend` — по последним **14 дням** (`today-13..today`): `report.daily_average_series`
  по списку дат; пустые дни (None) пропускаются; `linear_fit` средних; `slope` — на день,
  `intercept` — свободный член фита, `per_week = slope * 7`; `daily` — только дни с данными.
- Данные — строго по активному ребёнку/семье; новых зависимостей нет.

---

## 4. Mini App — вкладка «Аналитика»

- `index.html`: кнопка вкладки `<button class="tab" data-screen="analytics">Аналитика</button>`
  и `<section id="screen-analytics" class="screen" hidden></section>`.
- `app.js`: `switchTo` получает ветку `analytics` → `loadAnalytics()`.
- `loadAnalytics()` тянет `/api/analytics` и рисует:
  - **pie** зон (`drawPie(canvas, {green,yellow,red})`) с легендой и числами; пусто → «Нет данных».
  - **heatmap** недели: 7 ячеек `Пн..Вс`, фон по зоне, подпись среднего; `null` → «—».
  - **тренд**: мини-canvas `drawTrend(daily, slope, intercept)` — линия дневных средних и
    пунктирная линия регрессии; подпись `↗/↘ {per_week:+.1f} л/мин/нед` и `n` дней.
- Пустые данные: каждая карточка показывает «Нет данных», без исключений.

---

## 5. Обработка ошибок и краевые случаи

| Ситуация | Поведение |
|----------|-----------|
| Нет замеров | все счётчики 0, `weekday` — все `null`, `trend.daily` пуст, `slope=0` |
| Один замер / один день | `linear_fit` не падает (`slope=0`) |
| Дни без замеров в 14-дневном окне | пропускаются в `daily` и регрессии |
| `target_pef == 0` | зоны по дефолтным порогам 80/60 (pef_zone сам обрабатывает `target=0` как 100%) |
| Нет активного ребёнка (веб) | `404` |
| Ошибка запроса в UI | `showError`, вкладка не ломается |

---

## 6. Тестирование (TDD)

1. `test/test_analytics.py` (новый):
   - `zone_distribution`: границы зон (green/yellow/red), пусто.
   - `weekday_averages`: группировка по дням недели, `None` для дней без данных, среднее и count.
   - `linear_fit`: возрастание, убывание, константа, пусто, одна точка.
2. `test/test_webapp_api.py`:
   - `/api/analytics`: 200 и набор полей; `zones`/`weekday`/`trend` согласованы с засеянными данными;
   - нет активного ребёнка → 404; изоляция двух семей (общий `child_id`, данные чужой семьи не попадают);
   - статические проверки `index.html`/`app.js` (`screen-analytics`, `loadAnalytics`, `drawPie`, `drawHeatmap`, `drawTrend`, `/api/analytics`).
3. Регрессия: `pytest test/`; `pyflakes`/`compileall` (добавить `analytics.py` в CI-список и в проверочную команду); счётчики в `PROJECT.md`/`wiki.md`/todo-plan.

---

## 7. Критерии приёмки

- [ ] `analytics.py` считает зоны, дни недели и линейный тренд; покрыто unit-тестами.
- [ ] `/api/analytics` отдаёт `zones`/`weekday`/`trend`; 404 без ребёнка; изоляция семьи.
- [ ] Вкладка «Аналитика» рисует pie, heatmap недели и тренд с линией регрессии.
- [ ] Пустые данные показывают «Нет данных» без ошибок.
- [ ] `pyflakes` + `compileall` + все тесты зелёные; документация/счётчики обновлены.

---

## 8. Риски и решения

| Риск | Решение |
|------|---------|
| Полная история на каждый запрос | один `get_measurements_between` в `to_thread`; семьи малы |
| Выбросы искажают тренд | МНК по дневным средним за 14 дней; подпись per_week |
| Canvas-pie/heatmap без библиотек | простые фигуры (`arc`/`fillRect`), как в SP5A |
| Пустые данные | каждая карточка «Нет данных»; `linear_fit` безопасен |
| Смешение auto/ручных | осознанно: считаем все замеры (как streak/зоны) |

---

## 9. Вне scope SP5D

- Календарная heatmap месяца; таблица/экспорт прогноза.
- Корреляция заметок с падениями ПСВ (SP5E).
- Сравнение детей/семей; экспорт аналитики в PDF/CSV.

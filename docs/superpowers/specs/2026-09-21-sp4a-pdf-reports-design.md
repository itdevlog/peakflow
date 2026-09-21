# SP4A — PDF-отчёты врачу

> **Дата:** 2026-09-21
> **Подпроект:** Фаза 4.1 (пункт `4.1`, старт)
> **Статус:** дизайн на ревью
> **Зависимости:** Фазы 0–2 в main (схема v4, мульти-тенант, 444 теста)
> **Не входит:** доступ врача (4.3), геймификация (4.2), метрики (4.4), кастомные диапазоны, авто-отправка

---

## 1. Цель

Дать родителю возможность получить **PDF-отчёт для врача** по активному ребёнку за
неделю/месяц/квартал — из бота и из Mini App. Отчёт самодостаточен для печати на A4
(в т.ч. ч/б): шапка, график ПСВ, статистика за период и таблица замеров.

**Успех:** родитель получает валидный PDF за любой из трёх периодов из бота и Mini App;
данные строго изолированы по `(family_id, child_id)`; новых зависимостей нет; все тесты
зелёные.

---

## 2. Периоды

| Период | Границы (`period_bounds`) | Подпись (`period_label`) |
|--------|---------------------------|--------------------------|
| `week` | Пн–Вс текущей ISO-недели (`today - today.weekday()` … +6 дней) | `16–22.09.2026` (или `30.09–06.10.2026` при переходе месяца) |
| `month` | 1-е … последнее число текущего месяца | `Сентябрь 2026` |
| `quarter` | 1-е число первого месяца квартала … последнее число третьего | `III квартал 2026` |

Невалидный период → `ValueError`. Все границы считаются от «сегодня» в таймзоне
пользователя (`now_tz()`/`TZ_OFFSET`), а не от UTC.

---

## 3. Модуль `report_pdf.py` (новый, чистый)

По образцу `report.py`: **не импортирует** `bot.py`, `database.py`, `aiogram`.
Зависимости: стандартная библиотека + `matplotlib` (уже в `requirements.txt`).
`matplotlib` импортируется **лениво** внутри рендер-функций (backend `Agg`), чтобы не
тянуть его в веб-процесс на старте. Разрешено импортировать чистые хелперы из `report.py`
(`month_title`, `pef_zone`).

### Публичный API

```python
PERIODS = ("week", "month", "quarter")

period_bounds(period: str, today: date) -> tuple[str, str]   # ISO date_from, date_to (включительно)
period_label(period: str, today: date) -> str
compute_stats(rows: list[dict], target: int,
              zone_green: int = 80, zone_yellow: int = 60) -> dict
build_pdf(rows: list[dict], *, target: int, child_name: str,
          period_label: str, zone_green: int = 80, zone_yellow: int = 60) -> bytes
render_png(rows: list[dict], target: int, title: str,
           zone_green: int = 80, zone_yellow: int = 60) -> bytes
draw_chart(ax, rows, target, zone_green, zone_yellow, text_labels: bool = False) -> None
```

- `compute_stats` возвращает `{"total", "avg", "min", "max", "morning_avg",
  "evening_avg", "zones": {"green", "yellow", "red"}}`; зоны — через `report.pef_zone`.
  Считается из переданных `rows` (период), `database.get_stats` (all-time) не трогаем.
- `build_pdf` — `PdfPages(BytesIO)`, A4 portrait; синхронная, чистая.

### Потокобезопасность

matplotlib не потокобезопасен, а бот и веб рендерят в `asyncio.to_thread` параллельно.
Модульный `threading.Lock` (`_RENDER_LOCK`) сериализует **весь** рендер:
`render_png` и `build_pdf` берут его один раз; `draw_chart` — низкоуровневая функция без
захвата лока (её зовут только изнутри уже залоченного кода), реентерабельного deadlock нет.

### Рефактор графика

Отрисовка графика переезжает из `bot._render_chart_png` в `report_pdf.draw_chart` с
флагом `text_labels`:
- бот (`render_png`) → `text_labels=False`, метки emoji сохраняются (поведение PNG
  неизменно, существующие тесты проходят);
- PDF → `text_labels=True`, метки «Лучший/Худший» обычным текстом (emoji-глифы в PDF
  ненадёжны со шрифтами matplotlib).

`bot._render_chart_png` становится тонкой обёрткой над `report_pdf.render_png`;
`bot._render_chart_png_async` сохраняется.

---

## 4. Вёрстка PDF

A4 portrait, поля ~15 мм.

- **Страница 1:** шапка (имя ребёнка, целевая ПСВ, период, дата формирования), график ПСВ
  с линией нормы и порогами зон, блок статистики (всего, среднее, мин/макс, средние
  утро/вечер, распределение по зонам).
- **Страницы 2+:** таблица замеров (Дата, Время, Период, ПСВ, % нормы, Зона, Заметка),
  ~35 строк на страницу, заголовок повторяется на каждой странице.
- **A4 ч/б-совместимость:** зона дублируется словом (Зелёная/Жёлтая/Красная), а не только
  цветом; график использует различимые линии/маркеры, а не только цвет.
- Пустые `rows`: `build_pdf` не падает и возвращает валидный PDF с пометкой «нет данных».
  Обработчики всё равно отсекают пустой период раньше (см. ниже) для UX.

---

## 5. Данные и изоляция

- Ряды — только `get_measurements_between(db_path, child_id, date_from, date_to,
  family_id=family_id)`.
- `family_id`/`child_id`:
  - бот — `family_id, child_id = await _ctx(member)`;
  - веб — `auth["family_id"]`, `auth["active_child_id"]` под `require_parent`.
- `child_id is None` → существующая подсказка `_no_child_reply` (бот); в вебе — 403/404 по
  текущему поведению.
- Никаких выгрузок целой БД (`backup_family_db`-паттерн не затрагивается).

---

## 6. Бот

- `kb_settings`: новая кнопка «📄 Отчёт врачу» (`callback_data="report"`).
- Новый экран `kb_report_periods()` — кнопки `report_week` / `report_month` /
  `report_quarter` + «⬅️ Настройки».
- `cb_report` — показывает экран периодов.
- **Каждый** handler `cb_report`/`cb_report_<period>` сам проверяет родителя
  (`_is_parent_member(member, callback.from_user.id)`), как `cb_export*` после Фазы 0, —
  прямой callback-запрос не должен обходить проверку из `cb_settings`.
- `cb_report_<period>`:
  1. проверка родителя; `family_id, child_id = await _ctx(member)`;
     `child_id is None` → `_no_child_reply`.
  2. `date_from, date_to = period_bounds(period, today)`.
  3. `rows = await _db(get_measurements_between, DB_PATH, child_id, date_from, date_to, family_id)`.
  4. Пусто → `callback.answer("Нет записей за период.", show_alert=True)`.
  5. `target = await _db(get_effective_target, family_id=family_id)`.
  6. PDF в `asyncio.to_thread` (хелпер `_build_report_pdf_async`), чтобы не блокировать loop.
  7. `answer_document(BufferedInputFile(pdf, filename=f"peakflow_report_{child}_{period}.pdf"))`.
- Файлы: `bot.py` (+ `report_pdf.py`).

---

## 7. Web / Mini App

- `GET /api/report/pdf?period=week|month|quarter` под `require_parent`:
  - период невалиден → `422`;
  - `active_child_id` отсутствует → `404` «Нет активного ребёнка»;
  - нет записей за период → `404` «Нет записей за период»;
  - иначе `Response(content=pdf, media_type="application/pdf",
    headers={"Content-Disposition": _content_disposition(filename)})`,
    имя `peakflow_report_{child}_{period}_{stamp}.pdf` — тот же конвейер, что у
    `/api/export/csv`.
  - высокоуровневый хелпер `_effective_target`/`get_measurements_between` — как в CSV.
- `web/static/app.js`, экран Настройки: карточка «📄 Отчёт врачу» с тремя кнопками
  (Неделя/Месяц/Квартал), скачивание через существующий `download("/api/report/pdf?period=…")`.
- Файлы: `web/api.py`, `web/static/app.js`.

---

## 8. Обработка ошибок

| Ситуация | Бот | Web |
|----------|-----|-----|
| Период невалиден | недостижимо (кнопки фиксированы) | `422` |
| Нет активного ребёнка | `_no_child_reply` | `404` «Нет активного ребёнка» |
| Нет записей за период | alert «Нет записей за период.» | `404` «Нет записей за период» |
| Ошибка рендера PDF | лог + alert «Не удалось создать отчёт» | `500` «Не удалось создать отчёт» |
| Пользователь — не родитель | `_is_parent_member` → alert | `require_parent` → `403` |

---

## 9. Тестирование (TDD)

1. `test/test_report_pdf.py` (новый):
   - `period_bounds`: Пн/Вс недели, конец месяца, кварталы I–IV, смена года (декабрь→январь,
     неделя через границу года), невалидный период → `ValueError`.
   - `period_label`: все три периода, переход месяца внутри недели.
   - `compute_stats`: avg/min/max, morning/evening, распределение по зонам, пустой список.
   - `build_pdf`: начинается с `%PDF`, много строк → >1 страницы, пустой `rows` → валидный PDF.
2. `test/test_bot.py`:
   - `cb_report` показывает экран периодов; не-родитель получает отказ.
   - `cb_report_month` шлёт документ с `.pdf` (DB и `_build_report_pdf_async` замоканы).
   - пустой период → alert, документ не отправляется.
   - `_render_chart_png`/`_render_chart_png_async` не сломаны после выноса `draw_chart`.
3. `test/test_webapp_api.py`:
   - `/api/report/pdf` → `200`, `application/pdf`, magic `%PDF`;
   - невалидный период → `422`; ребёнок (не родитель) → `403`; пусто → `404`;
   - изоляция двух семей: семья №2 получает свои данные, данные семьи №1 не утекают.
4. Регрессия: полный прогон `python -m pytest test/ -q`, `pyflakes`, `compileall`.
5. После реализации обновить счётчики тестов в `PROJECT.md`, `wiki.md`,
   `docs/superpowers/specs/2026-09-17-todo-plan.md`.

---

## 10. Критерии приёмки

- [ ] PDF за неделю/месяц/квартал генерируется и скачивается из бота и Mini App.
- [ ] PDF содержит шапку, график, статистику за период и таблицу замеров.
- [ ] Отчёт читаем на A4 в ч/б (зона продублирована текстом).
- [ ] Изоляция по `(family_id, child_id)` подтверждена тестами двух семей.
- [ ] Пустой период даёт понятный отказ (alert/404), PDF не генерируется.
- [ ] Доступ только у родителей (бот и веб).
- [ ] Новых зависимостей нет; `_render_chart_png` не сломан.
- [ ] `pyflakes` + `compileall` + все тесты зелёные; документация/счётчики обновлены.

---

## 11. Риски и решения

| Риск | Решение |
|------|---------|
| matplotlib не потокобезопасен (бот+веб параллельно) | `_RENDER_LOCK` вокруг всего рендера |
| emoji-глифы ломают PDF | `text_labels=True` для PDF; emoji остаются только в PNG бота |
| Вынос `draw_chart` ломает PNG-график бота | тонкая обёртка + существующие тесты как регрессия |
| Импорт matplotlib утяжеляет веб-процесс | ленивый импорт внутри рендер-функций |
| Большой период → гигантский PDF/память | таблица постранично, ~35 строк/стр.; PDF в памяти (период ≤ квартал) |

---

## 12. Вне scope SP4A

- Доступ врача (read-only по ссылке), комментарии врача (4.3).
- Геймификация (4.2), метрики/логи (4.4).
- Произвольные диапазоны дат, авто-отправка отчёта по расписанию, e-mail/облако.
- Изменение `database.get_stats` и CSV-экспорта.

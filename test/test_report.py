"""Тесты общих хелперов и CSV (report.py)."""
from report import (
    build_csv_content,
    display_name,
    month_title,
    parse_month,
    pct_of,
    pef_zone,
    tod_emoji,
    tod_label,
)


def test_tod_emoji():
    assert tod_emoji("morning") == "☀️"
    assert tod_emoji("evening") == "🌙"


def test_month_title():
    assert month_title(2026, 8) == "Август 2026"
    assert month_title(2026, 9) == "Сентябрь 2026"


def test_pef_zone_thresholds():
    emoji, name = pef_zone(220, 260)
    assert name == "Зелёная"
    assert pef_zone(180, 260)[1] == "Жёлтая"
    assert pef_zone(100, 260)[1] == "Красная"


def test_pef_zone_custom_thresholds():
    # 200/260 = 76.9% → with green=70 it is green, default (80) it is yellow
    assert pef_zone(200, 260, 80, 60)[1] == "Жёлтая"
    assert pef_zone(200, 260, 70, 60)[1] == "Зелёная"


def test_pct_of():
    assert pct_of(208, 260) == 80
    assert pct_of(130, 260) == 50


def test_tod_label():
    assert tod_label("morning") == "Утро"
    assert tod_label("evening") == "Вечер"


def test_display_name():
    assert display_name(111, 111, "Motya", [222, 333]) == "Motya"
    assert display_name(222, 111, "Motya", [222, 333]) == "Родитель"
    assert display_name(999, 111, "Motya", [222, 333]) == "Кто-то"


def test_parse_month():
    assert parse_month("2026-08") == (2026, 8)
    assert parse_month("csv_2026-08") == (2026, 8)
    assert parse_month("csv_x") is None
    assert parse_month("2026-13") is None


def test_build_csv_content_columns_and_marks():
    rows = [
        {"measured_at": "2026-08-05 08:00:00", "time_of_day": "morning",
         "pef_value": 240, "added_by": 222, "source": "manual", "note": "болел, сильно"},
        {"measured_at": "2026-08-06 20:00:00", "time_of_day": "evening",
         "pef_value": 250, "added_by": 222, "source": "auto", "note": None},
    ]
    content = build_csv_content(
        rows, target=260, child_name="Motya",
        display_name=lambda uid: "Родитель", include_summary=False,
    )
    assert content.startswith("Дата,Время,Период,ПСВ (л/мин),% от нормы,Зона,Добавил,Заметка,Источник")
    assert "болел; сильно" in content   # comma replaced by ';'
    assert "авто" in content
    assert "ручной" in content
    assert "# Статистика" not in content


def test_build_csv_content_summary():
    rows = [{"measured_at": "2026-08-05 08:00:00", "time_of_day": "morning",
             "pef_value": 240, "added_by": 222, "source": "manual", "note": None}]
    stats = {"total": 1, "avg": 240.0, "min": 240, "max": 240}
    content = build_csv_content(rows, target=260, child_name="Motya",
                                stats=stats, include_summary=True,
                                display_name=lambda uid: "Родитель")
    assert content.startswith("\n# Статистика")
    assert "Всего замеров: 1" in content

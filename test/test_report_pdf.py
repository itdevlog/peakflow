"""Тесты PDF-отчётов (SP4A)."""
from datetime import date

import pytest


class TestPeriodBounds:
    def test_week_monday_to_sunday(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 9, 21)) == ("2026-09-21", "2026-09-27")

    def test_week_midweek_same_bounds(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 9, 23)) == ("2026-09-21", "2026-09-27")

    def test_week_crosses_year(self):
        from report_pdf import period_bounds
        assert period_bounds("week", date(2026, 12, 31)) == ("2026-12-28", "2027-01-03")

    def test_month(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2026, 9, 21)) == ("2026-09-01", "2026-09-30")

    def test_month_february_leap(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2024, 2, 10)) == ("2024-02-01", "2024-02-29")

    def test_month_december(self):
        from report_pdf import period_bounds
        assert period_bounds("month", date(2026, 12, 15)) == ("2026-12-01", "2026-12-31")

    @pytest.mark.parametrize("month,expected", [
        (1, ("2026-01-01", "2026-03-31")),
        (4, ("2026-04-01", "2026-06-30")),
        (9, ("2026-07-01", "2026-09-30")),
        (10, ("2026-10-01", "2026-12-31")),
    ])
    def test_quarter(self, month, expected):
        from report_pdf import period_bounds
        assert period_bounds("quarter", date(2026, month, 15)) == expected

    def test_invalid_period(self):
        from report_pdf import period_bounds
        with pytest.raises(ValueError):
            period_bounds("year", date(2026, 9, 21))


class TestPeriodLabel:
    def test_week_same_month(self):
        from report_pdf import period_label
        assert period_label("week", date(2026, 9, 21)) == "21–27.09.2026"

    def test_week_crosses_month(self):
        from report_pdf import period_label
        assert period_label("week", date(2026, 9, 30)) == "28.09–04.10.2026"

    def test_month(self):
        from report_pdf import period_label
        assert period_label("month", date(2026, 9, 21)) == "Сентябрь 2026"

    def test_quarter(self):
        from report_pdf import period_label
        assert period_label("quarter", date(2026, 9, 21)) == "III квартал 2026"


class TestComputeStats:
    def _rows(self):
        return [
            {"pef_value": 260, "time_of_day": "morning"},
            {"pef_value": 240, "time_of_day": "evening"},
            {"pef_value": 130, "time_of_day": "morning"},
        ]

    def test_basic(self):
        from report_pdf import compute_stats
        s = compute_stats(self._rows(), target=260)
        assert s["total"] == 3
        assert s["min"] == 130 and s["max"] == 260
        assert s["avg"] == pytest.approx((260 + 240 + 130) / 3)
        assert s["morning_avg"] == pytest.approx(195)
        assert s["evening_avg"] == pytest.approx(240)
        # target 260, zone_green 80 -> >=208, zone_yellow 60 -> >=156
        assert s["zones"] == {"green": 2, "yellow": 0, "red": 1}

    def test_empty(self):
        from report_pdf import compute_stats
        s = compute_stats([], target=260)
        assert s["total"] == 0
        assert s["avg"] == 0
        assert s["morning_avg"] is None
        assert s["evening_avg"] is None
        assert s["zones"] == {"green": 0, "yellow": 0, "red": 0}


class TestDrawChart:
    def _rows(self):
        return [
            {"pef_value": 240, "time_of_day": "morning", "measured_at": "2026-09-01 08:00:00"},
            {"pef_value": 260, "time_of_day": "evening", "measured_at": "2026-09-02 20:00:00"},
        ]

    def test_emoji_labels_by_default(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from report_pdf import draw_chart
        fig, ax = plt.subplots()
        try:
            draw_chart(ax, self._rows(), 260, 80, 60, title="T")
            texts = [t.get_text() for t in ax.texts]
        finally:
            plt.close(fig)
        assert any("🏆" in t for t in texts)

    def test_text_labels_for_pdf(self):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from report_pdf import draw_chart
        fig, ax = plt.subplots()
        try:
            draw_chart(ax, self._rows(), 260, 80, 60, text_labels=True)
            texts = [t.get_text() for t in ax.texts]
        finally:
            plt.close(fig)
        assert texts and all("🏆" not in t and "⚠" not in t for t in texts)
        assert any("Лучший" in t for t in texts)

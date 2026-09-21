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

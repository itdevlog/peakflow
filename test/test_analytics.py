"""Тесты аналитики (SP5D)."""
import pytest


class TestZoneDistribution:
    def test_counts(self):
        from analytics import zone_distribution
        rows = [{"pef_value": 260}, {"pef_value": 240}, {"pef_value": 130}]
        assert zone_distribution(rows, 260) == {"green": 2, "yellow": 0, "red": 1}

    def test_empty(self):
        from analytics import zone_distribution
        assert zone_distribution([], 260) == {"green": 0, "yellow": 0, "red": 0}


class TestWeekdayAverages:
    def test_grouping(self):
        from analytics import weekday_averages
        # 2026-09-21 — понедельник, 2026-09-22 — вторник
        rows = [
            {"measured_at": "2026-09-21 08:00:00", "pef_value": 240},
            {"measured_at": "2026-09-21 20:00:00", "pef_value": 260},
            {"measured_at": "2026-09-22 08:00:00", "pef_value": 200},
        ]
        out = weekday_averages(rows, 260)
        assert len(out) == 7
        assert out[0] == {"avg": 250.0, "count": 2, "zone": "green"}
        assert out[1]["avg"] == 200.0
        assert out[1]["zone"] == "yellow"
        assert all(x is None for x in out[2:])

    def test_empty(self):
        from analytics import weekday_averages
        assert weekday_averages([], 260) == [None] * 7


class TestLinearFit:
    def test_increasing(self):
        from analytics import linear_fit
        slope, intercept = linear_fit([0, 1, 2, 3])
        assert slope == pytest.approx(1.0)
        assert intercept == pytest.approx(0.0)

    def test_decreasing(self):
        from analytics import linear_fit
        slope, _ = linear_fit([10, 8, 6, 4])
        assert slope == pytest.approx(-2.0)

    def test_constant(self):
        from analytics import linear_fit
        slope, intercept = linear_fit([250, 250, 250])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(250.0)

    def test_empty(self):
        from analytics import linear_fit
        assert linear_fit([]) == (0.0, 0.0)

    def test_single(self):
        from analytics import linear_fit
        assert linear_fit([250]) == (0.0, 250.0)

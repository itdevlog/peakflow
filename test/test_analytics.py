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

    def test_skips_invalid_dates(self):
        from analytics import weekday_averages
        rows = [
            {"measured_at": "garbage", "pef_value": 999},
            {"measured_at": "2026-09-21 08:00:00", "pef_value": 250},
        ]
        out = weekday_averages(rows, 260)
        assert out[0] == {"avg": 250.0, "count": 1, "zone": "green"}


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


class TestMatchNoteGroups:
    def test_empty(self):
        from analytics import match_note_groups
        assert match_note_groups("") == []
        assert match_note_groups(None) == []

    def test_case_and_roots(self):
        from analytics import match_note_groups
        assert match_note_groups("Болел сильно") == ["sick"]
        assert match_note_groups("под утро БОЛЕЛА голова") == ["sick"]
        assert match_note_groups("был на тренировке") == ["sport"]
        assert match_note_groups("принял вентолин") == ["meds"]
        assert match_note_groups("аллергия на пыль") == ["allergy"]

    def test_multiple_groups(self):
        from analytics import match_note_groups
        got = match_note_groups("болел, не спал")
        assert "sick" in got and "night" in got


class TestNoteCorrelation:
    def test_avg_count_delta(self):
        from analytics import note_correlation
        rows = [
            {"pef_value": 250, "note": ""},
            {"pef_value": 250, "note": ""},
            {"pef_value": 200, "note": "болел"},
        ]
        out = note_correlation(rows)
        assert out["baseline"] == {"avg": 250.0, "count": 2}
        sick = next(g for g in out["groups"] if g["key"] == "sick")
        assert sick["avg"] == 200.0 and sick["count"] == 1 and sick["delta"] == -50.0
        sport = next(g for g in out["groups"] if g["key"] == "sport")
        assert sport["count"] == 0 and sport["avg"] is None and sport["delta"] is None

    def test_no_baseline(self):
        from analytics import note_correlation
        out = note_correlation([{"pef_value": 200, "note": "болел"}])
        assert out["baseline"]["count"] == 0
        sick = next(g for g in out["groups"] if g["key"] == "sick")
        assert sick["delta"] is None

    def test_empty(self):
        from analytics import note_correlation
        out = note_correlation([])
        assert out["baseline"] == {"avg": None, "count": 0}
        assert all(g["count"] == 0 for g in out["groups"])

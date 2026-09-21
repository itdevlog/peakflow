"""Тесты геймификации (SP4B)."""
from datetime import date, timedelta

import pytest

TODAY = date(2026, 9, 21)


def _d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


class TestCurrentStreak:
    def test_empty(self):
        from gamification import current_streak
        assert current_streak([], TODAY) == 0

    def test_today_only(self):
        from gamification import current_streak
        assert current_streak([_d(0)], TODAY) == 1

    def test_consecutive_ending_today(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(-1), _d(-2)], TODAY) == 3

    def test_grace_ending_yesterday(self):
        from gamification import current_streak
        assert current_streak([_d(-1), _d(-2)], TODAY) == 2

    def test_gap_resets(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(-2)], TODAY) == 1

    def test_stale_is_zero(self):
        from gamification import current_streak
        assert current_streak([_d(-2), _d(-3)], TODAY) == 0

    def test_unsorted_and_duplicates(self):
        from gamification import current_streak
        assert current_streak([_d(-1), _d(0), _d(0), _d(-2)], TODAY) == 3

    def test_future_dates_ignored(self):
        from gamification import current_streak
        assert current_streak([_d(0), _d(1)], TODAY) == 1
        assert current_streak([_d(1)], TODAY) == 0


class TestLongestStreak:
    def test_empty(self):
        from gamification import longest_streak
        assert longest_streak([]) == 0

    def test_single(self):
        from gamification import longest_streak
        assert longest_streak([_d(0)]) == 1

    def test_max_of_runs(self):
        from gamification import longest_streak
        assert longest_streak([_d(-6), _d(-5), _d(-3), _d(-2), _d(-1)]) == 3

    def test_all_consecutive(self):
        from gamification import longest_streak
        assert longest_streak([_d(-2), _d(-1), _d(0)]) == 3


class TestEvaluate:
    def test_below_threshold(self):
        from gamification import evaluate
        assert evaluate(6, 99) == set()

    def test_on_threshold_streak(self):
        from gamification import evaluate
        assert "streak_7" in evaluate(7, 0)

    def test_total_milestones(self):
        from gamification import evaluate
        assert {"total_100", "total_500"} <= evaluate(0, 500)
        assert "total_1000" not in evaluate(0, 500)

    def test_streak_uses_longest(self):
        from gamification import evaluate
        assert {"streak_7", "streak_30"} <= evaluate(30, 0)
        assert "streak_100" not in evaluate(30, 0)


class TestAchievementStatus:
    def test_streak_progress(self):
        from gamification import achievement_status
        assert achievement_status("streak_7", 5, 0) == (False, 5, 7)
        assert achievement_status("streak_7", 7, 0) == (True, 7, 7)

    def test_total_progress(self):
        from gamification import achievement_status
        assert achievement_status("total_100", 0, 40) == (False, 40, 100)

    def test_unknown_code_raises(self):
        from gamification import achievement_status
        with pytest.raises(KeyError):
            achievement_status("nope", 0, 0)

"""Тесты in-process метрик (SP4D)."""
import time

import pytest


@pytest.fixture(autouse=True)
def _clean():
    import metrics
    metrics.reset()
    yield
    metrics.reset()


class TestRegistry:
    def test_counter_default_zero(self):
        from metrics import get_counter
        assert get_counter("unknown_metric") == 0

    def test_inc_and_get(self):
        from metrics import inc, get_counter
        inc("hits")
        inc("hits", 2)
        assert get_counter("hits") == 3

    def test_labels_separate_series(self):
        from metrics import inc, get_counter
        inc("http_requests_total", method="GET", status="200")
        inc("http_requests_total", method="POST", status="200")
        assert get_counter("http_requests_total", method="GET", status="200") == 1
        assert get_counter("http_requests_total", method="POST", status="200") == 1
        assert get_counter("http_requests_total", method="GET", status="500") == 0

    def test_gauge_set_and_get(self):
        from metrics import set_gauge, get_gauge
        set_gauge("families_count", 3)
        set_gauge("families_count", 4)
        assert get_gauge("families_count") == 4
        assert get_gauge("missing") is None

    def test_snapshot(self):
        from metrics import inc, set_gauge, snapshot
        inc("a_total")
        set_gauge("b_gauge", 1.5)
        rows = {r["name"]: r for r in snapshot()}
        assert rows["a_total"]["type"] == "counter" and rows["a_total"]["value"] == 1
        assert rows["b_gauge"]["type"] == "gauge" and rows["b_gauge"]["value"] == 1.5

    def test_reset_keeps_uptime(self):
        from metrics import inc, get_counter, reset, uptime_seconds, START_TIME
        inc("x")
        reset()
        assert get_counter("x") == 0
        assert uptime_seconds() >= 0
        assert START_TIME <= time.time()


class TestRender:
    def test_render_format(self):
        from metrics import inc, set_gauge, render_prometheus
        inc("http_requests_total", method="GET", status="200")
        inc("http_requests_total", method="GET", status="200")
        set_gauge("families_count", 2)
        text = render_prometheus()
        assert "# TYPE http_requests_total counter" in text
        assert 'http_requests_total{method="GET",status="200"} 2' in text
        assert "# TYPE families_count gauge" in text
        assert "families_count 2" in text
        # TYPE emitted once per metric name
        assert text.count("# TYPE http_requests_total counter") == 1

    def test_label_value_escaped(self):
        from metrics import inc, render_prometheus
        inc("x_total", note='a"b')
        assert 'note="a\\"b"' in render_prometheus()

    def test_invalid_name(self):
        from metrics import inc
        with pytest.raises(ValueError):
            inc("bad-name")

    def test_invalid_label(self):
        from metrics import inc
        with pytest.raises(ValueError):
            inc("ok_total", **{"bad-label": "v"})

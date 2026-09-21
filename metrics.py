"""In-process метрики (SP4D).

Чистый потокобезопасный модуль: только stdlib. Не импортирует
bot.py/database.py/aiogram. Метрики живут в памяти процесса и сбрасываются
при рестарте.
"""
import re
import threading
import time

START_TIME = time.time()

_LOCK = threading.Lock()
_COUNTERS = {}
_GAUGES = {}

_NAME_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_TYPES = {}


def _check(name, labels):
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid metric name: {name!r}")
    for key in labels:
        if not isinstance(key, str) or not _LABEL_RE.fullmatch(key):
            raise ValueError(f"invalid label name: {key!r}")


def _register(name, kind):
    existing = _TYPES.get(name)
    if existing is not None and existing != kind:
        raise ValueError(f"metric {name!r} already registered as {existing}")
    _TYPES[name] = kind


def _key(name, labels):
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name, value=1, **labels):
    _check(name, labels)
    key = _key(name, labels)
    with _LOCK:
        _register(name, "counter")
        _COUNTERS[key] = _COUNTERS.get(key, 0) + value


def set_gauge(name, value, **labels):
    _check(name, labels)
    with _LOCK:
        _register(name, "gauge")
        _GAUGES[_key(name, labels)] = value


def get_counter(name, **labels):
    with _LOCK:
        return _COUNTERS.get(_key(name, labels), 0)


def get_gauge(name, **labels):
    with _LOCK:
        return _GAUGES.get(_key(name, labels))


def uptime_seconds():
    return time.time() - START_TIME


def snapshot():
    with _LOCK:
        counters = dict(_COUNTERS)
        gauges = dict(_GAUGES)
    rows = [{"name": n, "type": "counter", "labels": dict(l), "value": v}
            for (n, l), v in counters.items()]
    rows += [{"name": n, "type": "gauge", "labels": dict(l), "value": v}
             for (n, l), v in gauges.items()]
    return rows


def _fmt_labels(labels):
    if not labels:
        return ""
    parts = []
    for key in sorted(labels):
        value = (str(labels[key]).replace("\\", "\\\\")
                 .replace('"', '\\"').replace("\n", "\\n"))
        parts.append(f'{key}="{value}"')
    return "{" + ",".join(parts) + "}"


def _groups(store):
    grouped = {}
    for (name, labels), value in sorted(store.items()):
        grouped.setdefault(name, []).append((labels, value))
    return grouped


def render_prometheus():
    with _LOCK:
        counters = dict(_COUNTERS)
        gauges = dict(_GAUGES)
    lines = []
    for kind, store in (("counter", counters), ("gauge", gauges)):
        for name, series in _groups(store).items():
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in series:
                lines.append(f"{name}{_fmt_labels(dict(labels))} {value}")
    return ("\n".join(lines) + "\n") if lines else ""


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _GAUGES.clear()
        _TYPES.clear()

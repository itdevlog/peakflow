"""Pytest session setup: point config at the test database before collection.

`config.py` snapshots DB_PATH and BOT_TOKEN at import time. Test modules
import `config`/`bot` during collection, before `test_bot.py`'s autouse
fixture runs, so these env vars must be set here:

- `DB_PATH` keeps the whole-suite run isolated from the production DB.
- `BOT_TOKEN` must be present and of valid *format* because `bot.py` builds
  `Bot(token=...)` at import time (aiogram validates the shape). A dummy
  value is enough — no network calls are made in tests.
"""
import os

import pytest

os.environ.setdefault("DB_PATH", "test_peakflow.db")
os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF_test_token")

_TEST_DBS = ("test_peakflow.db", "test_cyr.db")
_DB_EXTS = ("", "-wal", "-shm", "-journal", ".v1.bak")


def _remove_test_dbs():
    for base in _TEST_DBS:
        for ext in _DB_EXTS:
            path = base + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@pytest.fixture(autouse=True)
def _clean_test_dbs():
    """Remove leftover test DBs before/after each test.

    A stale `test_peakflow.db*` from a previous (or parallel) run otherwise
    leaks state across tests and produces order-dependent failures.
    """
    _remove_test_dbs()
    yield
    _remove_test_dbs()

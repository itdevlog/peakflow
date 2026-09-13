"""Pytest session setup: point config at the test database before collection.

`config.py` snapshots DB_PATH at import time. Test modules import `config`/
`bot` during collection, before `test_bot.py`'s autouse fixture runs, so the
env var must be set here to keep the whole-suite run isolated from the
production `peakflow.db`.
"""
import os

os.environ.setdefault("DB_PATH", "test_peakflow.db")

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

os.environ.setdefault("DB_PATH", "test_peakflow.db")
os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF_test_token")

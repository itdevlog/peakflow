"""Тесты Telegram-уведомлений из Mini App (SP2b)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from web.notify import _display_name, notify_added, notify_red_zone

CHILD_ID = 111
PARENT_IDS = [222, 333]


def _config():
    return SimpleNamespace(
        CHILD_ID=CHILD_ID, PARENT_IDS=PARENT_IDS, CHILD_NAME="Motya",
        ZONE_GREEN=80, ZONE_YELLOW=60, ZONE_RED=50,
    )


def test_display_name():
    cfg = _config()
    assert _display_name(cfg, CHILD_ID) == "Motya"
    assert _display_name(cfg, PARENT_IDS[0]) == "Родитель"
    assert _display_name(cfg, 999) == "Кто-то"


def test_notify_added_sends_to_other_parents_only():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_added(bot, cfg, PARENT_IDS[0], 250, "morning", 260,
                             recipients=PARENT_IDS))
    assert bot.send_message.await_count == 1
    sent_to = bot.send_message.await_args.args[0]
    assert sent_to == PARENT_IDS[1]
    assert "250" in bot.send_message.await_args.args[1]


def test_notify_added_from_child_sends_to_all_parents():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "evening", 260,
                             recipients=PARENT_IDS))
    assert bot.send_message.await_count == 2


def test_notify_red_zone_sends_to_all_parents():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260, recipients=PARENT_IDS))
    assert bot.send_message.await_count == 2
    assert "120" in bot.send_message.await_args.args[1]


def test_notify_errors_are_swallowed():
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "morning", 260,
                             recipients=PARENT_IDS))  # must not raise
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260,
                                recipients=PARENT_IDS))  # must not raise


def test_notify_noop_when_bot_none():
    cfg = _config()
    asyncio.run(notify_added(None, cfg, CHILD_ID, 250, "morning", 260,
                             recipients=PARENT_IDS))
    asyncio.run(notify_red_zone(None, cfg, 120, "morning", 260, recipients=PARENT_IDS))


def test_notify_none_recipients_is_silent_not_env():
    """F3: missing recipients must never fall back to family #1's PARENT_IDS."""
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "morning", 260, recipients=None))
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260, recipients=None))
    assert bot.send_message.await_count == 0


def test_red_zone_family_two_never_targets_env_parents():
    """F3: a family-2 action only notifies family-2 parents, never env PARENT_IDS."""
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260, recipients=[700]))
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.args[0] == 700
    assert all(call.args[0] not in PARENT_IDS for call in bot.send_message.await_args_list)


def test_send_times_out_on_slow_telegram(monkeypatch):
    """A hung Telegram call must not hold the request open indefinitely."""
    import time
    import web.notify as notify

    monkeypatch.setattr(notify, "_SEND_TIMEOUT", 0.05, raising=False)

    async def slow_send(pid, text):
        await asyncio.sleep(5)

    cfg = _config()
    bot = SimpleNamespace(send_message=slow_send)

    started = time.monotonic()
    asyncio.run(notify_added(bot, cfg, CHILD_ID, 250, "morning", 260,
                             recipients=PARENT_IDS))
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"notify must time out quickly, took {elapsed:.1f}s"


def test_red_zone_can_exclude_author():
    """B3: the parent who entered a bad value must not alarm themselves."""
    cfg = _config()
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(notify_red_zone(bot, cfg, 120, "morning", 260,
                                recipients=PARENT_IDS, who=PARENT_IDS[0]))
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.args[0] == PARENT_IDS[1]

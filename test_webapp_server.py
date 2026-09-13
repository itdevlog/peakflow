"""Тесты запуска веб-сервера (SP1)."""
import asyncio

import web.server as server


def test_wait_forever_is_coroutine():
    assert asyncio.iscoroutinefunction(server.wait_forever)


def test_run_webapp_is_coroutine():
    assert asyncio.iscoroutinefunction(server.run_webapp)


def test_defer_shutdown_signals_context_manager():
    with server._defer_shutdown_signals():
        pass

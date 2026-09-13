# web/server.py
"""Запуск FastAPI в event loop бота (один процесс)."""
import asyncio
import contextlib
import logging
import signal
import threading

import uvicorn

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _defer_shutdown_signals():
    """Не даёт uvicorn-у повторно доставленным сигналом убить процесс до очистки."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {
        sig: signal.signal(sig, lambda *_: None)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


async def run_webapp(services: dict) -> None:
    """Обслуживает Mini App (uvicorn) до SIGINT/SIGTERM (uvicorn ставит should_exit)."""
    from web.api import create_app

    config = services["config"]
    server = uvicorn.Server(uvicorn.Config(
        create_app(services),
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        log_level="warning",
        access_log=False,
    ))
    logger.info("Веб-сервер Mini App: http://%s:%s", config.WEBAPP_HOST, config.WEBAPP_PORT)
    shutdown_event = services.get("shutdown_event")
    if shutdown_event is None:
        with _defer_shutdown_signals():
            await server.serve()
        return

    async def _watch_shutdown():
        await shutdown_event.wait()
        server.should_exit = True

    watcher = asyncio.create_task(_watch_shutdown())
    try:
        with _defer_shutdown_signals():
            await server.serve()
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


async def wait_forever() -> None:
    """Режим без веб-сервера: ждём сигнала завершения."""
    await asyncio.Event().wait()

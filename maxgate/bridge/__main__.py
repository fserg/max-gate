import asyncio
import fcntl
import logging
import signal
from logging.handlers import RotatingFileHandler

from aiohttp import web

from maxgate.bridge.api import InternalApi
from maxgate.bridge.supervisor import Supervisor
from maxgate.config import Settings
from maxgate.crypto import Crypto
from maxgate.db import create_storage


async def run(settings):
    if not settings.internal_token or not settings.internal_token.get_secret_value():
        raise ValueError("MAXGATE_INTERNAL_TOKEN required")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("maxgate")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(
            settings.data_dir / "bridge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        ),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    # Сторонние loggers могут печатать URL с токеном и payload. Только журнал Gate.
    logging.getLogger().setLevel(logging.CRITICAL)
    for name in ("aiogram", "aiohttp", "pymax"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    engine, sessions = create_storage(settings.database_url)
    supervisor = Supervisor(sessions, Crypto(settings.secret_key), settings)
    api = InternalApi(supervisor, settings.internal_token.get_secret_value())
    server = web.AppRunner(api.app, access_log=None)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        with (settings.data_dir / "bridge.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            await server.setup()
            await web.TCPSite(server, settings.api_host, settings.api_port).start()
            logger.info("InternalApi listening on %s:%s", settings.api_host, settings.api_port)
            await supervisor.start()
            await stopped.wait()
            logger.info("Shutdown requested")
            await server.cleanup()
            await supervisor.close()
            logger.info("Gate stopped cleanly")
    finally:
        await server.cleanup()
        await supervisor.close()
        await engine.dispose()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


def main():
    try:
        asyncio.run(run(Settings()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Никогда не печатаем исключения с env, URL, HTTP headers или payload.
        raise SystemExit(f"Gate failed ({type(exc).__name__})") from None


if __name__ == "__main__":
    main()

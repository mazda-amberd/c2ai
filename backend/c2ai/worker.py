"""Run the background job worker on its own: ``python -m c2ai.worker``.

Use this on dedicated worker machines and set ``C2AI_RUN_WORKER=false`` on
API replicas; by default the API process also runs a worker.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from c2ai.clients.http import close_http_clients
from c2ai.config import get_settings
from c2ai.core.logging import disable_sqlalchemy_logs, setup_logging
from c2ai.jobs import Worker, get_job_store

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker = Worker(get_job_store(), poll_seconds=settings.worker_poll_seconds)
    try:
        await worker.run(stop)
    finally:
        await close_http_clients()


if __name__ == "__main__":
    setup_logging()
    disable_sqlalchemy_logs()
    asyncio.run(main())

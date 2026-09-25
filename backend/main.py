"""Run the C2AI API with Uvicorn: ``python main.py``.

Host and port come from ``APP_HOST`` / ``APP_PORT`` (default 0.0.0.0:8007).
"""

import logging

from c2ai.core.logging import disable_sqlalchemy_logs, setup_logging

setup_logging()
disable_sqlalchemy_logs()

import uvicorn  # noqa: E402

from c2ai.app import app  # noqa: E402
from c2ai.config import get_settings  # noqa: E402

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    settings = get_settings()
    host, port = settings.app_host, settings.app_port
    logger.info("Starting C2AI Athena service on %s:%s", host, port)
    uvicorn.run(
        app=app,
        host=host,
        port=port,
        access_log=False,
        # The client address used for login throttling comes from
        # X-Forwarded-For only when the request arrives through these proxies.
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )

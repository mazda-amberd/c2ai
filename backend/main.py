"""Run the C2AI API with Uvicorn: ``python main.py``.

Host and port come from ``APP_HOST`` / ``APP_PORT`` (default 0.0.0.0:8007).
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from c2ai.core.logging import disable_sqlalchemy_logs, setup_logging  # noqa: E402

setup_logging()
disable_sqlalchemy_logs()

import uvicorn  # noqa: E402

from c2ai.app import app  # noqa: E402

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8007"))
    logger.info("Starting C2AI Athena service on %s:%s", host, port)
    uvicorn.run(app=app, host=host, port=port, access_log=False)

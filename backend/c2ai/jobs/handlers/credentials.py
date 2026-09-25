"""``credentials.reencrypt``: keep every stored credential under the primary key."""

from __future__ import annotations

import logging
from datetime import timedelta

from c2ai.config import get_settings
from c2ai.db.session import AsyncSessionLocal
from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule
from c2ai.security.rotate import reencrypt_all

logger = logging.getLogger(__name__)

KIND = "credentials.reencrypt"


def _configured() -> bool:
    settings = get_settings()
    return bool(settings.encryption_keys.strip() or settings.credential_encryption_key.strip())


@job_handler(KIND, lease=timedelta(minutes=10))
async def reencrypt(_ctx: JobContext) -> dict:
    result = await reencrypt_all(AsyncSessionLocal)
    if result["rewritten"]:
        logger.info("Re-encrypted %s credential(s) under key %s", result["rewritten"],
                    result["primary_key"])
    return result


register_schedule(Schedule(kind=KIND, every=lambda: timedelta(hours=24), enabled=_configured))

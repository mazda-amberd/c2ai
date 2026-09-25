"""``auth.password_reset``: the work behind "Forgot password?" on the sign-in page."""

from __future__ import annotations

import logging
from datetime import timedelta

from c2ai.jobs.worker import JobContext, job_handler
from c2ai.services.password_reset import request_password_reset

logger = logging.getLogger(__name__)

KIND = "auth.password_reset"
# The payload is an address somebody typed; keep it only briefly.
RETENTION = timedelta(hours=1)


@job_handler(KIND, lease=timedelta(minutes=1))
async def reset_password(ctx: JobContext) -> dict:
    address = str(ctx.payload.get("address") or "")
    async with ctx.session_factory() as db:
        outcome = await request_password_reset(db, address)
    # What happened is for the operator, never for whoever filled in the form.
    logger.info("Password reset requested for %r: %s", address[:120], outcome)
    return {"outcome": outcome}

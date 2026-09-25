"""``jobs.purge``: delete finished jobs, expired revocations and old login failures."""

from __future__ import annotations

from datetime import timedelta

from c2ai.config import get_settings
from c2ai.crud import session as session_store
from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule
from c2ai.services.password_reset import RESET_COOLDOWN

KIND = "jobs.purge"


@job_handler(KIND)
async def purge(ctx: JobContext) -> dict:
    # login_failures also holds the forgot-password cooldowns.
    window = max(
        timedelta(seconds=get_settings().login_failure_window_seconds), RESET_COOLDOWN
    )
    async with ctx.session_factory() as db:
        sessions = await session_store.purge_expired(db, failure_window=window)
        await db.commit()
    return {"jobs": await ctx.store.purge_expired(), "session_rows": sessions}


register_schedule(Schedule(kind=KIND, every=lambda: timedelta(minutes=10)))

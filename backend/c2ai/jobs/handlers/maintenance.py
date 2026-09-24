"""``jobs.purge``: delete finished jobs past their retention."""

from __future__ import annotations

from datetime import timedelta

from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule

KIND = "jobs.purge"


@job_handler(KIND)
async def purge(ctx: JobContext) -> dict:
    return {"deleted": await ctx.store.purge_expired()}


register_schedule(Schedule(kind=KIND, every=lambda: timedelta(minutes=10)))

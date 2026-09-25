"""Deployment jobs.

``deployments.dispatch`` sends a staged operation to its pipeline. The API
request that staged it normally does this itself; the job is the durable
fallback when that request could not (the outbox).

``deployments.track`` runs every few seconds and is the only code that polls
GitHub for operation progress. For each open operation it stores the latest
run snapshot, settles the operation when GitHub finishes it, restores
operations whose dispatch never completed, and abandons dispatches that
never produced a run. The status endpoints only read what it stored, so the
GitHub API load no longer grows with the number of open browsers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from c2ai.constants.registered_application import DeploymentStep
from c2ai.deployments import operations, repository as instances, tracking
from c2ai.deployments.lifecycle import Outcome
from c2ai.deployments.service import DISPATCH_JOB, DispatchFailed, dispatch_operation
from c2ai.jobs.worker import JobContext, JobFailed, Schedule, job_handler, register_schedule

logger = logging.getLogger(__name__)

TRACK_JOB = "deployments.track"
TRACK_EVERY = timedelta(seconds=10)
# A staged operation whose dispatch never finished (its request died).
STALE_DISPATCH_AFTER = timedelta(minutes=5)
# A dispatched operation that never produced a run or any progress.
ABANDON_AFTER = timedelta(minutes=30)


@job_handler(DISPATCH_JOB, lease=timedelta(minutes=2))
async def dispatch(ctx: JobContext) -> dict:
    async with ctx.session_factory() as db:
        try:
            await dispatch_operation(db, ctx.payload)
        except DispatchFailed as failure:
            raise JobFailed(failure.error.code, str(failure.error.detail)) from failure
    return {"dispatched": True}


async def track_open_operations(session_factory) -> dict:
    now = datetime.now(UTC)
    summary = {"checked": 0, "settled": 0, "restored": 0, "abandoned": 0}
    async with session_factory() as db:
        runs = [(run.id, run.deployment_instance_id) for run in await operations.open_operations(db)]
    for run_id, instance_id in runs:
        summary["checked"] += 1
        async with session_factory() as db:
            outcome = await _track_one(db, run_id, instance_id, now)
        if outcome:
            summary[outcome] += 1
    if any(summary[key] for key in ("settled", "restored", "abandoned")):
        logger.info("Tracked deployments: %s", summary)
    return summary


async def _track_one(db, run_id: str, instance_id, now: datetime) -> str | None:
    # 1. Read (no lock) what the lookup needs.
    run = await operations.get_operation(db, run_id)
    instance = await instances.get_registered_application_deployment(db, instance_id)
    if run is None or instance is None or run.ended_at is not None:
        return None
    age = now - run.dispatched_at
    if run.dispatch_state == "pending":
        if age <= STALE_DISPATCH_AFTER:
            return None  # its request (or the dispatch job) is still working on it
        instance = await instances.get_registered_application_deployment(
            db, instance_id, for_update=True
        )
        run = await operations.get_operation(db, run_id)
        if run is None or run.dispatch_state != "pending" or run.ended_at is not None:
            await db.commit()
            return None
        await instances.fail_dispatch(
            db, instance, "The dispatch did not complete; the operation was undone."
        )
        await db.commit()
        return "restored"
    plan = await tracking.plan_fetch(db, instance)
    await db.commit()  # 2. No transaction while GitHub answers.
    progress, _error = await tracking.fetch(plan) if plan is not None else (None, None)

    # 3. Apply under the lock.
    instance = await instances.get_registered_application_deployment(
        db, instance_id, for_update=True
    )
    run = await operations.get_operation(db, run_id)
    if run is None or instance is None or run.ended_at is not None:
        await db.commit()
        return None
    if progress is not None:
        await tracking.apply_progress(db, run, instance, progress)
        await db.commit()
        return "settled" if run.ended_at is not None else None
    has_run = bool((instance.dispatch_reference or {}).get("run_id"))
    # A pipeline that reports progress by callback is alive even when no
    # GitHub run could be matched to it.
    progressed = instance.current_step != DeploymentStep.VALIDATING_CONFIGURATION.value
    if not has_run and not progressed and age > ABANDON_AFTER:
        await instances.settle_operation(
            db,
            instance,
            Outcome.ABANDONED,
            failure_reason="No pipeline run was found for this operation.",
            message="Operation closed: its pipeline never started.",
            created_by="athena",
        )
        await db.commit()
        return "abandoned"
    await db.commit()
    return None


@job_handler(TRACK_JOB, lease=timedelta(minutes=5))
async def track(ctx: JobContext) -> dict:
    return await track_open_operations(ctx.session_factory)


register_schedule(Schedule(kind=TRACK_JOB, every=lambda: TRACK_EVERY))

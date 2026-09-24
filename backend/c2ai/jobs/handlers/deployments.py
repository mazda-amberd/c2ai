"""``deployments.reconcile``: settle operations nobody is watching.

Every open operation is checked against GitHub Actions once a minute, so an
operation finishes (and stops blocking its subdomain) even when no browser is
polling and the pipeline sends no callback. An operation whose pipeline never
produced a run or any progress within ``ABANDON_AFTER`` is closed as abandoned and its
instance marked failed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from c2ai.constants.registered_application import DeploymentStep
from c2ai.db.session import AsyncSessionLocal
from c2ai.deployments import repository as instances
from c2ai.deployments.lifecycle import Outcome
from c2ai.deployments.tracking import (
    get_registered_deployment_workflow_progress,
)
from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule
from c2ai.models.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)

KIND = "deployments.reconcile"
ABANDON_AFTER = timedelta(minutes=30)
_BATCH = 50


async def reconcile_open_operations(session_factory=AsyncSessionLocal) -> dict:
    now = datetime.now(UTC)
    settled = abandoned = checked = 0
    async with session_factory() as db:
        result = await db.execute(
            select(PipelineRun.id, PipelineRun.deployment_instance_id, PipelineRun.dispatched_at)
            .where(PipelineRun.ended_at.is_(None), PipelineRun.deployment_instance_id.is_not(None))
            .order_by(PipelineRun.dispatched_at)
            .limit(_BATCH)
        )
        open_rows = result.all()
    for _run_id, instance_id, dispatched_at in open_rows:
        checked += 1
        async with session_factory() as db:
            instance = await instances.get_registered_application_deployment(
                db, instance_id, for_update=True
            )
            if instance is None:
                continue
            progress, _error = await get_registered_deployment_workflow_progress(db, instance)
            if progress is not None and progress.get("status") == "completed":
                settled += 1
                continue
            has_run = bool((instance.dispatch_reference or {}).get("run_id"))
            # A pipeline that reports progress by callback is alive even when
            # no GitHub run could be matched to it.
            progressed = instance.current_step != DeploymentStep.VALIDATING_CONFIGURATION.value
            stale = dispatched_at is not None and now - dispatched_at > ABANDON_AFTER
            if not has_run and not progressed and stale:
                await instances.settle_operation(
                    db,
                    instance,
                    Outcome.ABANDONED,
                    failure_reason="No pipeline run was found for this operation.",
                    message="Operation closed: its pipeline never started.",
                    created_by="athena",
                )
                await db.commit()
                abandoned += 1
    if settled or abandoned:
        logger.info("Reconciled deployments: settled=%s abandoned=%s", settled, abandoned)
    return {"checked": checked, "settled": settled, "abandoned": abandoned}


@job_handler(KIND, lease=timedelta(minutes=5))
async def reconcile(_ctx: JobContext) -> dict:
    return await reconcile_open_operations()


register_schedule(Schedule(kind=KIND, every=lambda: timedelta(minutes=1)))

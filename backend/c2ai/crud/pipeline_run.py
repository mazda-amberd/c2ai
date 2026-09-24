# pylint: disable=import-error
"""CRUD operations for the PipelineRun model."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.deployment import Deployment
from c2ai.models.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)

# Unmatched runs older than this are treated as orphaned (dispatch likely silently failed)
# and excluded from the active-guard query.
ORPHAN_TIMEOUT = timedelta(minutes=10)


async def create_pipeline_run(
    db: AsyncSession,
    *,
    id: str,
    subdomain: str,
    operation: str,
    event_type: str,
    triggered_by: str,
    tier: int | None = None,
    branch: str | None = None,
    deployment_metadata: dict | None = None,
) -> PipelineRun:
    """Insert a pipeline run; for new deployments also record who the instance is for.

    ``deployment_metadata`` (customer_name, env_instance, domain) is written to
    ``deployments`` in the same transaction; the metrics views read it to show
    client and instance names for each namespace.
    """
    run = PipelineRun(
        id=id,
        subdomain=subdomain,
        operation=operation,
        event_type=event_type,
        triggered_by=triggered_by,
        tier=tier,
        branch=branch,
    )
    db.add(run)
    if deployment_metadata is not None:
        db.add(
            Deployment(
                subdomain=subdomain,
                customer_name=deployment_metadata["customer_name"],
                env_instance=deployment_metadata["env_instance"],
                domain=deployment_metadata["domain"],
                tier=tier,
                branch=branch,
                status="dispatched",
            )
        )
    await db.commit()
    await db.refresh(run)
    logger.info("Created pipeline_run id=%s subdomain=%s op=%s", id, subdomain, operation)
    return run


async def get_active_run_for_subdomain(
    db: AsyncSession,
    subdomain: str,
) -> PipelineRun | None:
    """
    Return the active (ended_at IS NULL) pipeline run for this subdomain, if any.
    Orphaned unmatched dispatches older than ORPHAN_TIMEOUT are excluded so they
    don't permanently block the subdomain.
    """
    orphan_cutoff = datetime.now(tz=UTC) - ORPHAN_TIMEOUT
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.subdomain == subdomain)
        .where(PipelineRun.ended_at.is_(None))
        .where(
            or_(
                PipelineRun.run_id.is_not(None),
                PipelineRun.dispatched_at >= orphan_cutoff,
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_run_for_subdomain(
    db: AsyncSession,
    subdomain: str,
) -> PipelineRun | None:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.subdomain == subdomain)
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_all_active_runs(db: AsyncSession) -> list[PipelineRun]:
    orphan_cutoff = datetime.now(tz=UTC) - ORPHAN_TIMEOUT
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.ended_at.is_(None))
        .where(
            or_(
                PipelineRun.run_id.is_not(None),
                PipelineRun.dispatched_at >= orphan_cutoff,
            )
        )
        .order_by(PipelineRun.dispatched_at.desc())
    )
    return list(result.scalars().all())


async def get_pipeline_run_by_id(
    db: AsyncSession,
    pipeline_run_id: str,
) -> PipelineRun | None:
    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == pipeline_run_id)
    )
    return result.scalar_one_or_none()


async def get_runs_for_subdomain(
    db: AsyncSession,
    subdomain: str,
    limit: int = 20,
) -> list[PipelineRun]:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.subdomain == subdomain)
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def set_run_id(
    db: AsyncSession,
    pipeline_run_id: str,
    run_id: int,
) -> None:
    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == pipeline_run_id)
    )
    run = result.scalar_one_or_none()
    if run:
        run.run_id = run_id
        db.add(run)
        await db.commit()
        logger.info("Resolved run_id=%s for pipeline_run=%s", run_id, pipeline_run_id)


async def mark_run_ended(
    db: AsyncSession,
    pipeline_run_id: str,
) -> None:
    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == pipeline_run_id)
    )
    run = result.scalar_one_or_none()
    if run and run.ended_at is None:
        run.ended_at = datetime.now(tz=UTC)
        db.add(run)
        await db.commit()
        logger.info("Marked pipeline_run=%s as ended", pipeline_run_id)

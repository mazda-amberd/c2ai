"""The operation log (``pipeline_runs``): one row per lifecycle operation.

Rows are opened in the same transaction that moves the instance into its
in-progress state, so a dispatch that fails rolls both back. The unique index
``uq_pipeline_runs_active_subdomain`` turns a second concurrent operation on
the same subdomain into a ConflictError instead of two pipelines racing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.core.exceptions import ConflictError
from c2ai.deployments.lifecycle import LOG_OPERATION, Operation, Outcome
from c2ai.models.pipeline_run import PipelineRun

ACTIVE_SUBDOMAIN_INDEX = "uq_pipeline_runs_active_subdomain"


def log_subdomain(instance) -> str:
    """The host label an instance's operations are serialised on."""

    return instance.subdomain or instance.instance_name


def open_operation(
    db: AsyncSession,
    instance,
    operation: Operation,
    *,
    triggered_by: str,
    tier: int | None = None,
    version: str | None = None,
    event_type: str | None = None,
) -> PipelineRun:
    """Add an unfinished log row for ``operation`` (flushed by the caller)."""

    run = PipelineRun(
        id=str(uuid.uuid4()),
        deployment_instance_id=instance.id,
        subdomain=log_subdomain(instance),
        operation=LOG_OPERATION[operation],
        event_type=event_type or f"registered-application-{operation.value}",
        triggered_by=triggered_by,
        tier=tier if tier is not None else instance.tier,
        branch=version,
        dispatched_at=datetime.now(UTC),
    )
    db.add(run)
    return run


def is_active_operation_conflict(error: IntegrityError) -> bool:
    return ACTIVE_SUBDOMAIN_INDEX in str(getattr(error, "orig", error))


def operation_conflict(subdomain: str) -> ConflictError:
    return ConflictError(
        f"Another operation is already in progress for '{subdomain}'. "
        "Wait for it to complete before starting a new one.",
        code="DeploymentOperationInProgress",
    )


def record_dispatch(run: PipelineRun | None, reference: dict[str, Any]) -> None:
    """Copy what the pipeline returned (workflow, run id) onto the log row."""

    if run is None:
        return
    workflow = reference.get("workflow_id") or reference.get("event_type")
    if workflow:
        # ".github/workflows/ada-deploy.yaml" → "ada-deploy.yaml", as GitHub names it.
        run.event_type = PurePosixPath(str(workflow)).name
    if reference.get("run_id") is not None:
        run.run_id = int(reference["run_id"])


def close_operation(run: PipelineRun | None, outcome: Outcome, *, at: datetime | None = None):
    if run is None or run.ended_at is not None:
        return
    run.ended_at = at or datetime.now(UTC)
    run.conclusion = outcome.value


async def active_operation(db: AsyncSession, instance_id) -> PipelineRun | None:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.deployment_instance_id == instance_id, PipelineRun.ended_at.is_(None))
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_operation(db: AsyncSession, operation_id: str) -> PipelineRun | None:
    result = await db.execute(select(PipelineRun).where(PipelineRun.id == operation_id))
    return result.scalar_one_or_none()


async def latest_operation_for_subdomain(db: AsyncSession, subdomain: str) -> PipelineRun | None:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.subdomain == subdomain)
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def operations_for_subdomain(
    db: AsyncSession, subdomain: str, *, limit: int = 20
) -> list[PipelineRun]:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.subdomain == subdomain)
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def visible_operations(
    db: AsyncSession, *, failed_within: timedelta, limit: int = 100
) -> list[PipelineRun]:
    """Unfinished operations, plus unsuccessful ones that ended recently."""

    cutoff = datetime.now(UTC) - failed_within
    result = await db.execute(
        select(PipelineRun)
        .where(
            or_(
                PipelineRun.ended_at.is_(None),
                and_(
                    PipelineRun.ended_at >= cutoff,
                    PipelineRun.conclusion.in_(("failure", "cancelled", "abandoned")),
                ),
            )
        )
        .order_by(PipelineRun.dispatched_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def claimed_run_ids(db: AsyncSession, *, since: datetime) -> set[int]:
    """GitHub run ids already linked to an operation dispatched after ``since``."""

    result = await db.execute(
        select(PipelineRun.run_id).where(
            PipelineRun.run_id.is_not(None), PipelineRun.dispatched_at >= since
        )
    )
    return {int(run_id) for run_id in result.scalars().all()}

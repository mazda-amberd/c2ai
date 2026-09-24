"""Live status of deployment operations in the ``PipelineStatusOut`` shape.

``/api/pipeline/active``, ``/status`` and ``/history`` read the operation log
(``pipeline_runs``), which every application type writes. An unfinished
operation is overlaid with its instance's live GitHub Actions progress; the
same lookup applies GitHub's final result, so polling also settles it.

Unsuccessful operations stay visible for ``TERMINAL_VISIBILITY_WINDOW``;
successful ones disappear as soon as they finish.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from c2ai.constants.registered_application import DeploymentInstanceStatus
from c2ai.deployments import operations
from c2ai.deployments.configuration import (
    configured_version,
)
from c2ai.deployments.tracking import (
    get_registered_deployment_workflow_progress,
)
from c2ai.models.pipeline_run import PipelineRun
from c2ai.models.registered_application import DeploymentInstance
from c2ai.schemas.deployment import PipelineStatusOut

TERMINAL_VISIBILITY_WINDOW = timedelta(minutes=5)
# Every open client polls /api/pipeline/active; cache each operation's GitHub
# lookup briefly so the poll rate does not reach the GitHub API.
STATUS_CACHE_TTL = timedelta(seconds=8)

_GH_CONCLUSION = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancelled",
    "abandoned": "failure",
}

_status_cache: dict[str, tuple[dict[str, Any], datetime]] = {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _step_label(step: str | None) -> str | None:
    """``waiting_for_rollout`` → ``Waiting for rollout``."""
    if not step:
        return None
    text = step.replace("_", " ")
    return text[:1].upper() + text[1:]


def _last_event_message(instance: DeploymentInstance) -> str | None:
    for event in reversed(instance.events or []):
        if event.message:
            return str(event.message)
    return None


def _active_job_and_step(progress: dict[str, Any]) -> tuple[str | None, str | None]:
    for job in progress.get("jobs") or []:
        if job.get("status") == "in_progress":
            step_name: str | None = None
            for step in job.get("steps") or []:
                if step.get("status") == "in_progress":
                    step_name = step.get("name")
                    break
                if step.get("status") == "completed":
                    step_name = step.get("name")
            return job.get("name"), step_name
    return None, None


def _base_values(run: PipelineRun, instance: DeploymentInstance | None) -> dict[str, Any]:
    reference = (instance.dispatch_reference or {}) if instance is not None else {}
    finished = run.ended_at is not None
    if finished:
        gh_status = "completed"
    elif instance is not None and instance.status == DeploymentInstanceStatus.PENDING.value:
        gh_status = "queued"
    else:
        gh_status = "in_progress"
    branch = run.branch
    if branch is None and instance is not None:
        branch = configured_version(
            instance.configuration or {}, instance.application.application_type
        ) if instance.application is not None else None
    values: dict[str, Any] = {
        **run.to_dict(),
        "branch": branch,
        "gh_status": gh_status,
        "gh_conclusion": _GH_CONCLUSION.get(run.conclusion or ""),
        "run_url": reference.get("html_url"),
        "active_job": None,
        "current_step": None,
        "started_at": _iso(run.dispatched_at),
        "completed_at": _iso(run.ended_at),
    }
    if instance is not None:
        values["active_job"] = None if finished else _step_label(instance.current_step)
        values["current_step"] = instance.failure_reason or _last_event_message(instance)
    return values


def _apply_progress(values: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    """Overlay live GitHub Actions run state on Athena's own record."""
    if progress.get("run_id") is not None:
        values["run_id"] = int(progress["run_id"])
    if progress.get("html_url"):
        values["run_url"] = progress["html_url"]
    values["gh_status"] = progress.get("status") or values["gh_status"]
    values["gh_conclusion"] = progress.get("conclusion") or values["gh_conclusion"]
    active_job, current_step = _active_job_and_step(progress)
    if active_job:
        values["active_job"] = active_job
        values["current_step"] = current_step or values["current_step"]
    values["started_at"] = progress.get("created_at") or values["started_at"]
    if progress.get("status") == "completed":
        values["completed_at"] = progress.get("updated_at") or values["completed_at"]
    return values


async def _load_instances(
    db: AsyncSession, runs: list[PipelineRun]
) -> dict[Any, DeploymentInstance]:
    ids = {run.deployment_instance_id for run in runs if run.deployment_instance_id}
    if not ids:
        return {}
    result = await db.execute(
        select(DeploymentInstance)
        .where(DeploymentInstance.id.in_(ids))
        .options(
            selectinload(DeploymentInstance.events),
            selectinload(DeploymentInstance.application),
        )
    )
    return {instance.id: instance for instance in result.scalars().unique().all()}


async def operation_status(
    db: AsyncSession,
    run: PipelineRun,
    instance: DeploymentInstance | None,
) -> PipelineStatusOut:
    """One operation's status; unfinished ones consult GitHub (briefly cached)."""

    now = datetime.now(UTC)
    if run.ended_at is None:
        cached = _status_cache.get(run.id)
        if cached and now - cached[1] < STATUS_CACHE_TTL:
            return PipelineStatusOut(**cached[0])

    progress = progress_error = None
    if run.ended_at is None and instance is not None:
        progress, progress_error = await get_registered_deployment_workflow_progress(
            db, instance
        )
    # The lookup may have settled the operation, so read the row after it.
    values = _base_values(run, instance)
    if progress:
        values = _apply_progress(values, progress)
    elif progress_error and run.ended_at is None:
        values["current_step"] = progress_error

    if run.ended_at is None:
        _status_cache[run.id] = (values, now)
    else:
        _status_cache.pop(run.id, None)
    return PipelineStatusOut(**values)


def _prune_cache() -> None:
    stale_before = datetime.now(UTC) - STATUS_CACHE_TTL
    for key in [k for k, (_, at) in _status_cache.items() if at < stale_before]:
        _status_cache.pop(key, None)


def _is_successfully_completed(status: PipelineStatusOut) -> bool:
    return status.gh_status == "completed" and status.gh_conclusion == "success"


async def list_active_statuses(db: AsyncSession) -> list[PipelineStatusOut]:
    """Every in-flight operation plus recent failures, newest first."""

    runs = await operations.visible_operations(db, failed_within=TERMINAL_VISIBILITY_WINDOW)
    instances = await _load_instances(db, runs)
    # One at a time: the session is not safe for concurrent use.
    statuses = [
        await operation_status(db, run, instances.get(run.deployment_instance_id))
        for run in runs
    ]
    _prune_cache()
    return [status for status in statuses if not _is_successfully_completed(status)]


async def latest_status_for_subdomain(
    db: AsyncSession, subdomain: str
) -> PipelineStatusOut | None:
    run = await operations.latest_operation_for_subdomain(db, subdomain)
    if run is None:
        return None
    instances = await _load_instances(db, [run])
    return await operation_status(db, run, instances.get(run.deployment_instance_id))

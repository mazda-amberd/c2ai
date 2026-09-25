"""Status of deployment operations in the ``PipelineStatusOut`` shape.

``/api/pipeline/active``, ``/status`` and ``/history`` read the operation log
(``pipeline_runs``), which every application type writes, including the
GitHub run snapshot the ``deployments.track`` job stores on it. Nothing here
calls GitHub or writes to the database: any number of browsers can poll
without adding GitHub API calls.

Unsuccessful operations stay visible for ``TERMINAL_VISIBILITY_WINDOW``;
successful ones disappear as soon as they finish.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from c2ai.constants.registered_application import (
    ADA_APPLICATION_ID,
    DeploymentInstanceStatus,
)
from c2ai.deployments import operations
from c2ai.deployments.configuration import (
    configured_version,
)
from c2ai.models.pipeline_run import PipelineRun
from c2ai.models.registered_application import DeploymentInstance
from c2ai.schemas.deployment import PipelineStatusOut

TERMINAL_VISIBILITY_WINDOW = timedelta(minutes=5)

_GH_CONCLUSION = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancelled",
    "abandoned": "failure",
}



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


def _application_name(instance: DeploymentInstance | None) -> str | None:
    """Registered application shown on the operation's card; ADA cards show the customer."""
    if instance is None or instance.application is None:
        return None
    if instance.application.id == ADA_APPLICATION_ID:
        return None
    return instance.application.name


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
        "application_name": _application_name(instance),
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


def operation_status(run: PipelineRun, instance: DeploymentInstance | None) -> PipelineStatusOut:
    """One operation's status from the log and its stored GitHub snapshot."""

    values = _base_values(run, instance)
    if run.progress:
        values = _apply_progress(values, run.progress)
    elif run.ended_at is None and run.dispatch_state == "dispatched":
        values["current_step"] = values["current_step"] or "Waiting for the GitHub Actions run."
    return PipelineStatusOut(**values)


def _is_successfully_completed(status: PipelineStatusOut) -> bool:
    return status.gh_status == "completed" and status.gh_conclusion == "success"


async def list_active_statuses(db: AsyncSession) -> list[PipelineStatusOut]:
    """Every in-flight operation plus recent failures, newest first."""

    runs = await operations.visible_operations(db, failed_within=TERMINAL_VISIBILITY_WINDOW)
    instances = await _load_instances(db, runs)
    statuses = [operation_status(run, instances.get(run.deployment_instance_id)) for run in runs]
    return [status for status in statuses if not _is_successfully_completed(status)]


async def latest_status_for_subdomain(
    db: AsyncSession, subdomain: str
) -> PipelineStatusOut | None:
    run = await operations.latest_operation_for_subdomain(db, subdomain)
    if run is None:
        return None
    instances = await _load_instances(db, [run])
    return operation_status(run, instances.get(run.deployment_instance_id))

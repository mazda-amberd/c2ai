# pylint: disable=import-error
"""
Expose registered-application deployments through the pipeline status contract.

``GET /api/pipeline/active`` was written for the direct ``/api/deploy`` flow,
whose runs live in ``pipeline_runs``. Registered applications dispatch their own
GitHub workflows (or container pipeline) and are tracked in
``deployment_instances`` instead, so they never appear in that table. The UI
polls one endpoint for live progress, so this module projects those deployment
instances onto the same ``PipelineStatusOut`` shape:

  * ``id`` is the deployment instance id — the handle the deploy modal holds.
  * live GitHub job/step text comes from the same lookup the deployment detail
    endpoint uses, which also makes GitHub's terminal result authoritative.
  * failed or cancelled deployments stay visible for
    ``TERMINAL_VISIBILITY_WINDOW``; successful actions disappear immediately.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from c2ai.models.registered_application import DeploymentInstance
from c2ai.constants.registered_application import DeploymentInstanceStatus
from c2ai.services.github_workflow_progress import (
    get_registered_deployment_workflow_progress,
)
from c2ai.services.registered_application_deployment import (
    resolve_github_workflow_subdomain,
)
from c2ai.schemas.deployment import PipelineStatusOut

# Statuses that mean "the pipeline is still running".
ACTIVE_STATUSES = frozenset(
    {
        DeploymentInstanceStatus.PENDING.value,
        DeploymentInstanceStatus.DEPLOYING.value,
        DeploymentInstanceStatus.UPDATING.value,
        DeploymentInstanceStatus.TERMINATING.value,
    }
)

# How long an unsuccessful deployment keeps being reported by /api/pipeline/active.
TERMINAL_VISIBILITY_WINDOW = timedelta(minutes=5)

VISIBLE_TERMINAL_STATUSES = frozenset(
    {
        DeploymentInstanceStatus.FAILED.value,
        DeploymentInstanceStatus.CANCELLED.value,
    }
)

# /api/pipeline/active is polled by every open client; cache the per-deployment
# GitHub lookup briefly so the poll rate does not reach the GitHub API.
STATUS_CACHE_TTL = timedelta(seconds=8)

# Newest-first cap — more concurrent lifecycle operations than this is not a
# situation the tier UI can render anyway.
_MAX_INSTANCES = 100

_GH_CONCLUSION_BY_STATUS = {
    DeploymentInstanceStatus.RUNNING.value: "success",
    DeploymentInstanceStatus.TERMINATED.value: "success",
    DeploymentInstanceStatus.FAILED.value: "failure",
    DeploymentInstanceStatus.CANCELLED.value: "cancelled",
}

# PipelineStatusOut.operation uses the direct-pipeline vocabulary; the
# registered-application side calls the same operation "upgrade".
_OPERATION_BY_STATUS = {
    DeploymentInstanceStatus.UPDATING.value: "update",
    DeploymentInstanceStatus.TERMINATING.value: "terminate",
    DeploymentInstanceStatus.TERMINATED.value: "terminate",
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


def _operation(instance: DeploymentInstance) -> str:
    reference = instance.dispatch_reference or {}
    declared = reference.get("operation")
    if declared == "upgrade":
        return "update"
    if declared in {"deploy", "terminate"}:
        return str(declared)
    return _OPERATION_BY_STATUS.get(instance.status, "deploy")


def _event_type(instance: DeploymentInstance) -> str:
    reference = instance.dispatch_reference or {}
    for key in ("workflow_id", "pipeline", "event_type"):
        value = reference.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"registered-application-{_operation(instance)}"


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _version(instance: DeploymentInstance) -> str | None:
    """The deployed ref/tag — rendered where the direct flow shows a branch.

    Looks where deployment snapshots actually store it: the container image
    tag, a GitHub upgrade target, or the GitHub ``branch`` input chosen at
    deploy time. The dispatch ``ref`` (the workflow's own branch, usually
    ``main``) is only a last resort.
    """
    configuration = instance.configuration or {}
    container = configuration.get("container")
    github = configuration.get("github")
    parameters = configuration.get("parameters")
    candidates = (
        container.get("image_tag") if isinstance(container, dict) else None,
        github.get("version") if isinstance(github, dict) else None,
        parameters.get("branch") if isinstance(parameters, dict) else None,
        # Top-level keys written by early snapshots.
        configuration.get("version"),
        configuration.get("tag"),
    )
    for candidate in candidates:
        if _text(candidate):
            return _text(candidate)
    return _text((instance.dispatch_reference or {}).get("ref"))


def _subdomain(instance: DeploymentInstance) -> str:
    if instance.subdomain:
        return instance.subdomain
    resolved = resolve_github_workflow_subdomain(
        instance.configuration or {},
        instance_name=instance.instance_name,
    )
    return resolved or instance.instance_name


def _last_event_message(instance: DeploymentInstance) -> str | None:
    for event in reversed(instance.events or []):
        if event.message:
            return str(event.message)
    return None


def _active_job_and_step(progress: dict[str, Any]) -> tuple[str | None, str | None]:
    """Running job and the step it is on, matching the direct-pipeline fields."""
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


def _ended_at(instance: DeploymentInstance) -> datetime | None:
    if instance.status in ACTIVE_STATUSES:
        return None
    return instance.completed_at or instance.terminated_at or instance.updated_at


def _base_values(instance: DeploymentInstance) -> dict[str, Any]:
    ended_at = _ended_at(instance)
    return {
        "id": str(instance.id),
        "subdomain": _subdomain(instance),
        "operation": _operation(instance),
        "event_type": _event_type(instance),
        "triggered_by": instance.triggered_by,
        "run_id": (instance.dispatch_reference or {}).get("run_id"),
        "tier": instance.tier,
        "branch": _version(instance),
        "dispatched_at": _iso(instance.created_at),
        "ended_at": _iso(ended_at),
        # Athena's own record of where the pipeline is, used verbatim when
        # GitHub has nothing to say (container pipelines, unresolved runs).
        "gh_status": (
            "queued"
            if instance.status == DeploymentInstanceStatus.PENDING.value
            else "in_progress"
            if instance.status in ACTIVE_STATUSES
            else "completed"
        ),
        "gh_conclusion": _GH_CONCLUSION_BY_STATUS.get(instance.status),
        "run_url": (instance.dispatch_reference or {}).get("html_url"),
        "active_job": _step_label(instance.current_step),
        "current_step": instance.failure_reason or _last_event_message(instance),
        "started_at": _iso(instance.created_at),
        "completed_at": _iso(instance.completed_at or instance.terminated_at),
    }


def _apply_progress(values: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    """Overlay live GitHub Actions run state on Athena's own record."""
    run_id = progress.get("run_id")
    if run_id is not None:
        values["run_id"] = int(run_id)
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


async def _status_for_instance(
    db: AsyncSession,
    instance: DeploymentInstance,
) -> PipelineStatusOut:
    cache_key = str(instance.id)
    now = datetime.now(tz=timezone.utc)
    is_active = instance.status in ACTIVE_STATUSES
    cached = _status_cache.get(cache_key)
    # Finished deployments are never served from the cache, so their terminal
    # state is always fresh — the same rule the direct pipeline cache follows.
    if is_active and cached and now - cached[1] < STATUS_CACHE_TTL:
        return PipelineStatusOut(**cached[0])

    progress, progress_error = await get_registered_deployment_workflow_progress(
        db,
        instance,
    )
    # The lookup above may have written GitHub's terminal result onto the
    # instance, so read Athena's own fields after it, not before.
    values = _base_values(instance)
    if progress:
        values = _apply_progress(values, progress)
    elif progress_error and instance.status in ACTIVE_STATUSES:
        values["current_step"] = progress_error

    if instance.status in ACTIVE_STATUSES:
        _status_cache[cache_key] = (values, now)
    else:
        _status_cache.pop(cache_key, None)
    return PipelineStatusOut(**values)


async def _load_visible_instances(db: AsyncSession) -> list[DeploymentInstance]:
    cutoff = datetime.now(tz=timezone.utc) - TERMINAL_VISIBILITY_WINDOW
    result = await db.execute(
        select(DeploymentInstance)
        .where(
            or_(
                DeploymentInstance.status.in_(tuple(ACTIVE_STATUSES)),
                and_(
                    DeploymentInstance.status.in_(
                        tuple(VISIBLE_TERMINAL_STATUSES)
                    ),
                    DeploymentInstance.updated_at >= cutoff,
                ),
            )
        )
        .options(selectinload(DeploymentInstance.events))
        .order_by(DeploymentInstance.created_at.desc(), DeploymentInstance.id.desc())
        .limit(_MAX_INSTANCES)
    )
    return list(result.scalars().unique().all())


def _prune_cache() -> None:
    """Drop entries for deployments that stopped being polled (e.g. deleted)."""
    stale_before = datetime.now(tz=timezone.utc) - STATUS_CACHE_TTL
    for key in [k for k, (_, at) in _status_cache.items() if at < stale_before]:
        _status_cache.pop(key, None)


async def list_active_registered_pipeline_statuses(
    db: AsyncSession,
) -> list[PipelineStatusOut]:
    """Registered-application deployments in the ``/api/pipeline/active`` shape."""

    instances = await _load_visible_instances(db)
    statuses = [await _status_for_instance(db, instance) for instance in instances]
    _prune_cache()
    return [
        status
        for status in statuses
        if not (
            status.gh_status == "completed"
            and status.gh_conclusion == "success"
        )
    ]

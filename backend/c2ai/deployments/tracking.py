"""Live GitHub Actions progress for registered application lifecycle operations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.crud import github_connection as crud_github_connection
from c2ai.deployments import lifecycle
from c2ai.deployments.configuration import (
    resolve_github_workflow_subdomain,
)
from c2ai.deployments.lifecycle import Outcome
from c2ai.deployments.operations import active_operation, claimed_run_ids, record_dispatch
from c2ai.deployments.repository import (
    settle_operation,
)
from c2ai.models.registered_application import DeploymentInstance

logger = logging.getLogger(__name__)

_FAILED_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}


def _failure_reason(progress: dict[str, Any]) -> str:
    for job in progress.get("jobs", []):
        if job.get("conclusion") in _FAILED_CONCLUSIONS:
            for step in job.get("steps", []):
                if step.get("conclusion") in _FAILED_CONCLUSIONS:
                    return (
                        f"GitHub Actions job '{job['name']}' failed at "
                        f"step '{step['name']}'."
                    )
            return f"GitHub Actions job '{job['name']}' did not complete successfully."
    conclusion = progress.get("conclusion") or "unknown"
    return f"GitHub Actions workflow finished with conclusion '{conclusion}'."


def _github_completed_at(progress: dict[str, Any]) -> datetime:
    value = progress.get("updated_at")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


async def _synchronize_terminal_state(
    db: AsyncSession,
    instance: DeploymentInstance,
    progress: dict[str, Any],
) -> None:
    """Make GitHub's terminal result authoritative when callbacks are absent."""

    if progress.get("status") != "completed" or instance.status not in lifecycle.IN_PROGRESS:
        return
    operation = lifecycle.operation_of(instance)
    success = progress.get("conclusion") == "success"
    run_number = progress.get("run_number")
    run_label = f" #{run_number}" if run_number is not None else ""
    label = operation.value.replace("_", " ")
    await settle_operation(
        db,
        instance,
        Outcome.SUCCESS if success else Outcome.FAILURE,
        at=_github_completed_at(progress),
        failure_reason=None if success else _failure_reason(progress),
        message=(
            f"GitHub Actions {label} workflow{run_label} completed successfully."
            if success
            else f"GitHub Actions {label} workflow{run_label} failed."
        ),
        created_by="github-actions",
    )


async def client_for_reference(
    db: AsyncSession, reference: dict[str, Any]
) -> GitHubActionsClient | None:
    """The GitHub client (and credentials) a dispatch reference was sent with."""

    owner = reference.get("repo_owner")
    repository = reference.get("repo_name")
    if not owner or not repository:
        return None
    options: dict[str, Any] = {
        "repo_owner": str(owner),
        "repo_name": str(repository),
        "api_base_url": str(reference.get("api_base_url") or "https://api.github.com"),
    }
    connection_id = reference.get("connection")
    if connection_id:
        runtime = await crud_github_connection.resolve_github_connection(db, str(connection_id))
        if runtime is not None:
            options["github_token"] = runtime.token
            options["api_base_url"] = runtime.api_base_url
    return GitHubActionsClient(**options)


def _dispatched_at(reference: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(reference["dispatched_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return datetime.now(UTC) - timedelta(hours=1)


async def get_registered_deployment_workflow_progress(
    db: AsyncSession,
    instance: DeploymentInstance,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load GitHub's jobs/steps and persist correlation plus terminal state."""

    reference = dict(instance.dispatch_reference or {})
    if not reference.get("workflow_id"):
        return None, None
    try:
        client = await client_for_reference(db, reference)
        if client is None:
            return None, None
        exclude: frozenset[int] = frozenset()
        if reference.get("run_id") is None:
            claimed = await claimed_run_ids(
                db, since=_dispatched_at(reference) - timedelta(minutes=5)
            )
            exclude = frozenset(claimed)
        # Runs are titled with the subdomain the workflow was given, which is the
        # derived host label rather than the Athena instance name.
        progress = await client.get_workflow_progress(
            reference,
            instance_name=str(
                reference.get("subdomain")
                or resolve_github_workflow_subdomain(
                    instance.configuration or {},
                    instance_name=instance.instance_name,
                )
            ),
            deployment_id=str(instance.id),
            exclude_run_ids=exclude,
        )
        if progress is None:
            return None, "Waiting for the matching GitHub Actions run."

        changed = False
        if reference.get("run_id") != progress["run_id"]:
            reference["run_id"] = progress["run_id"]
            instance.dispatch_reference = reference
            record_dispatch(await active_operation(db, instance.id), reference)
            changed = True
        original_state = (
            instance.status,
            instance.current_step,
            instance.failure_reason,
        )
        await _synchronize_terminal_state(db, instance, progress)
        if changed or original_state != (
            instance.status,
            instance.current_step,
            instance.failure_reason,
        ):
            db.add(instance)
            await db.commit()
        return progress, None
    except (httpx.HTTPError, ServiceUnavailableError, ValueError) as error:
        logger.warning(
            "GitHub workflow progress unavailable deployment=%s repo=%s/%s: %s",
            instance.id,
            reference.get("repo_owner"),
            reference.get("repo_name"),
            error,
        )
        return None, "GitHub Actions progress could not be loaded."

"""Live GitHub Actions progress for registered application lifecycle operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.registered_application import (
    DeploymentInstance,
    DeploymentInstanceEvent,
)
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.constants.registered_application import (
    DeploymentInstanceStatus,
    DeploymentStep,
)
from c2ai.crud import github_connection as crud_github_connection
from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.services.registered_application_deployment import (
    resolve_github_workflow_subdomain,
)

logger = logging.getLogger(__name__)

_FAILED_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}


def _operation(reference: dict[str, Any], instance: DeploymentInstance) -> str:
    value = reference.get("operation")
    if value in {"deploy", "upgrade", "terminate"}:
        return str(value)
    if instance.status == DeploymentInstanceStatus.UPDATING.value:
        return "upgrade"
    if instance.status in {
        DeploymentInstanceStatus.TERMINATING.value,
        DeploymentInstanceStatus.TERMINATED.value,
    }:
        return "terminate"
    pipeline = str(reference.get("pipeline", ""))
    if "upgrade" in pipeline:
        return "upgrade"
    if "termination" in pipeline or "terminate" in pipeline:
        return "terminate"
    return "deploy"


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
    return datetime.now(timezone.utc)


async def _synchronize_terminal_state(
    db: AsyncSession,
    instance: DeploymentInstance,
    progress: dict[str, Any],
) -> None:
    """Make GitHub's terminal result authoritative when callbacks are absent."""

    if progress.get("status") != "completed":
        return
    operation = _operation(instance.dispatch_reference or {}, instance)
    conclusion = progress.get("conclusion")
    success = conclusion == "success"
    next_step = (
        DeploymentStep.COMPLETED.value if success else DeploymentStep.FAILED.value
    )
    next_status = (
        DeploymentInstanceStatus.TERMINATED.value
        if success and operation == "terminate"
        else DeploymentInstanceStatus.RUNNING.value
        if success
        else DeploymentInstanceStatus.FAILED.value
    )
    failure_reason = None if success else _failure_reason(progress)

    if (
        instance.current_step == next_step
        and instance.status == next_status
        and instance.failure_reason == failure_reason
    ):
        return

    completed_at = _github_completed_at(progress)
    instance.current_step = next_step
    instance.status = next_status
    instance.failure_reason = failure_reason
    instance.completed_at = completed_at
    if success and operation == "terminate":
        instance.terminated_at = completed_at
    if instance.subdomain:
        if not success:
            instance.dns_status = "failed"
        elif operation == "terminate":
            instance.dns_status = "deleted"
        elif operation == "deploy":
            instance.dns_status = "active"

    run_number = progress.get("run_number")
    run_label = f" #{run_number}" if run_number is not None else ""
    message = (
        f"GitHub Actions {operation} workflow{run_label} completed successfully."
        if success
        else f"GitHub Actions {operation} workflow{run_label} failed."
    )
    instance.events.append(
        DeploymentInstanceEvent(
            step=next_step,
            status=next_status,
            message=message,
            failure_reason=failure_reason,
            created_by="github-actions",
        )
    )
    db.add(instance)


async def get_registered_deployment_workflow_progress(
    db: AsyncSession,
    instance: DeploymentInstance,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load GitHub's jobs/steps and persist correlation plus terminal state."""

    reference = dict(instance.dispatch_reference or {})
    owner = reference.get("repo_owner")
    repository = reference.get("repo_name")
    workflow_id = reference.get("workflow_id")
    if not owner or not repository or not workflow_id:
        return None, None

    client_options: dict[str, Any] = {
        "repo_owner": str(owner),
        "repo_name": str(repository),
        "api_base_url": str(
            reference.get("api_base_url") or "https://api.github.com"
        ),
    }
    connection_id = reference.get("connection")
    try:
        if connection_id:
            runtime = await crud_github_connection.resolve_github_connection(
                db,
                str(connection_id),
            )
            if runtime is not None:
                client_options["github_token"] = runtime.token
                client_options["api_base_url"] = runtime.api_base_url
        client = GitHubActionsClient(**client_options)
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
        )
        if progress is None:
            return None, "Waiting for the matching GitHub Actions run."

        changed = False
        if reference.get("run_id") != progress["run_id"]:
            reference["run_id"] = progress["run_id"]
            instance.dispatch_reference = reference
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
            owner,
            repository,
            error,
        )
        return None, "GitHub Actions progress could not be loaded."

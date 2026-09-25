"""GitHub Actions progress for deployment operations.

Tracking runs in three steps so no database transaction is open while GitHub
is called:

    plan = await plan_fetch(db, instance)         # database reads only
    <caller ends the transaction>
    progress = await fetch(plan)                  # GitHub only
    await apply_progress(db, run, instance, progress)   # short locked write
    <caller commits>

The ``deployments.track`` job does this for every open operation, so the
status endpoints only read what it stored (``pipeline_runs.progress``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.crud import github_connection as crud_github_connection
from c2ai.deployments import lifecycle
from c2ai.deployments.configuration import resolve_github_workflow_subdomain
from c2ai.deployments.lifecycle import Outcome
from c2ai.deployments.operations import claimed_run_ids, record_progress
from c2ai.deployments.repository import settle_operation
from c2ai.models.pipeline_run import PipelineRun
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


def _dispatched_at(reference: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(reference["dispatched_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return datetime.now(UTC) - timedelta(hours=1)


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


@dataclass(frozen=True)
class FetchPlan:
    client: GitHubActionsClient
    reference: dict[str, Any]
    instance_name: str
    deployment_id: str
    exclude_run_ids: frozenset[int]


async def plan_fetch(db: AsyncSession, instance: DeploymentInstance) -> FetchPlan | None:
    """Everything the GitHub lookup needs from the database (credentials, claimed runs)."""

    reference = dict(instance.dispatch_reference or {})
    if not reference.get("workflow_id"):
        return None
    client = await client_for_reference(db, reference)
    if client is None:
        return None
    exclude: frozenset[int] = frozenset()
    if reference.get("run_id") is None:
        since = _dispatched_at(reference) - timedelta(minutes=5)
        exclude = frozenset(await claimed_run_ids(db, since=since))
    # Runs are titled with the subdomain the workflow was given, which is the
    # derived host label rather than the Athena instance name.
    name = str(
        reference.get("subdomain")
        or resolve_github_workflow_subdomain(
            instance.configuration or {}, instance_name=instance.instance_name
        )
    )
    return FetchPlan(client, reference, name, str(instance.id), exclude)


async def fetch(plan: FetchPlan) -> tuple[dict[str, Any] | None, str | None]:
    """GitHub's run, jobs and steps for the operation (no database access)."""

    try:
        progress = await plan.client.get_workflow_progress(
            plan.reference,
            instance_name=plan.instance_name,
            deployment_id=plan.deployment_id,
            exclude_run_ids=plan.exclude_run_ids,
        )
    except (httpx.HTTPError, ServiceUnavailableError, ValueError) as error:
        logger.warning(
            "GitHub workflow progress unavailable deployment=%s: %s", plan.deployment_id, error
        )
        return None, "GitHub Actions progress could not be loaded."
    if progress is None:
        return None, "Waiting for the matching GitHub Actions run."
    return progress, None


async def apply_progress(
    db: AsyncSession,
    run: PipelineRun,
    instance: DeploymentInstance,
    progress: dict[str, Any],
) -> None:
    """Store the snapshot, link the run, and settle the operation if GitHub finished it.

    Call with the instance locked; the caller commits.
    """

    record_progress(run, progress)
    reference = dict(instance.dispatch_reference or {})
    linked = {"run_id": progress["run_id"]}
    if progress.get("html_url") and not reference.get("html_url"):
        linked["html_url"] = progress["html_url"]
    if any(reference.get(key) != value for key, value in linked.items()):
        instance.dispatch_reference = {**reference, **linked}
    if progress.get("status") != "completed" or instance.status not in lifecycle.IN_PROGRESS:
        return
    success = progress.get("conclusion") == "success"
    run_number = progress.get("run_number")
    run_label = f" #{run_number}" if run_number is not None else ""
    label = (run.kind or lifecycle.operation_of(instance).value).replace("_", " ")
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

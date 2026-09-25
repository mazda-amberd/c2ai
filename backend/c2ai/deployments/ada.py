"""ADA: Amberd's own application, deployed through the registered-application flow.

The tier pages still speak the original ``/api/deploy*`` contract (instances
addressed by subdomain, a free-form branch). These functions translate it onto
the one deployment model: ADA is a registered GitHub Workflow application
(seeded by migration 0023, repository and ref kept in sync with settings), and
each subdomain is a ``deployment_instances`` row with an operation log.

An instance that runs in the cluster but that Athena never deployed (it only
shows up in the Grafana inventory) is adopted as an ADA instance the first
time someone updates, moves or terminates it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.config import get_settings
from c2ai.constants.registered_application import DeploymentInstanceStatus, DeploymentStep
from c2ai.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from c2ai.deployments import lifecycle, operations, repository as instances, service, tracking
from c2ai.deployments.configuration import (
    build_deployment_configuration,
)
from c2ai.deployments.lifecycle import Outcome
from c2ai.jobs.store import JobStore
from c2ai.models.application_instance import ApplicationInstance
from c2ai.models.pipeline_run import PipelineRun
from c2ai.models.registered_application import (
    DeploymentInstance,
    DeploymentInstanceEvent,
    GitHubApplicationConfiguration,
    RegisteredApplicationVersion,
)
from c2ai.registration import repository as applications
from c2ai.schemas.deployment import DeployRequest
from c2ai.schemas.registered_application import RegisteredApplicationDeploymentCreate

logger = logging.getLogger(__name__)

ADA_APPLICATION_ID = UUID("ada00000-0000-4000-8000-000000000001")
ADA_WORKFLOW_PATH = ".github/workflows/ada-deploy.yaml"
# With a fresh Grafana inventory, an unknown instance really does not exist;
# with a stale one the check is advisory.
_INVENTORY_STALE_AFTER = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# The ADA application
# ---------------------------------------------------------------------------


async def ada_version(db: AsyncSession) -> RegisteredApplicationVersion:
    """ADA's current version, with its GitHub settings synced from configuration."""

    version = await applications.get_current_registered_application_version(db, ADA_APPLICATION_ID)
    if version is None or version.github_configuration is None:
        raise ServiceUnavailableError(
            "The ADA application is not registered. Run `python -m c2ai.db.migrate`."
        )
    settings = get_settings()
    wanted = {
        "repository": f"{settings.github_repo_owner}/{settings.github_repo_name}",
        "code_repository": f"{settings.github_repo_owner}/{settings.deploy_source_repo}",
        "ref": settings.devops_branch,
        "workflow_file_path": ADA_WORKFLOW_PATH,
    }
    config: GitHubApplicationConfiguration = version.github_configuration
    if any(getattr(config, key) != value for key, value in wanted.items()):
        for key, value in wanted.items():
            setattr(config, key, value)
        db.add(config)
        await db.flush()
    return version


def _devops_client() -> GitHubActionsClient:
    settings = get_settings()
    try:
        return GitHubActionsClient(
            repo_owner=settings.github_repo_owner, repo_name=settings.deploy_source_repo
        )
    except ValueError as error:
        raise ServiceUnavailableError(
            "GitHub integration is not configured (GITHUB_PAT is unset or empty)."
        ) from error


async def list_source_refs(repo: str, kind: str) -> list[str]:
    """Branches or tags of an ADA source repository for the branch picker."""

    settings = get_settings()
    try:
        client = GitHubActionsClient(repo_owner=settings.github_repo_owner, repo_name=repo)
    except ValueError as error:
        raise ServiceUnavailableError(
            "GitHub integration is not configured (GITHUB_PAT is unset or empty)."
        ) from error
    try:
        if kind == "tags":
            refs = await client.list_repository_tags(limit=1000)
        else:
            refs = await client.list_repository_branches(limit=1000)
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("GitHub %s unavailable for %s: %s", kind, repo, error)
        return []
    return sorted(refs)


async def ensure_ref_exists(branch: str) -> None:
    client = _devops_client()
    if not await client.ref_exists(branch):
        raise UnprocessableEntityError(
            f"Branch or tag '{branch}' does not exist in "
            f"{client.repo_owner}/{client.repo_name}."
        )


# ---------------------------------------------------------------------------
# Instances by subdomain
# ---------------------------------------------------------------------------


async def live_instance(db: AsyncSession, subdomain: str) -> DeploymentInstance | None:
    """The instance (of any application) currently using ``subdomain``."""

    result = await db.execute(
        select(DeploymentInstance.id)
        .where(
            (DeploymentInstance.subdomain == subdomain)
            | (DeploymentInstance.instance_name == subdomain),
            DeploymentInstance.status.not_in(tuple(lifecycle.FINISHED)),
        )
        .order_by(DeploymentInstance.created_at.desc())
        .limit(1)
    )
    instance_id = result.scalar_one_or_none()
    if instance_id is None:
        return None
    return await instances.get_registered_application_deployment(db, instance_id)


async def _inventory_entry(db: AsyncSession, subdomain: str) -> ApplicationInstance | None:
    # ApplicationInstance.nodename is the Kubernetes namespace, which is the
    # subdomain the devops workflows address.
    result = await db.execute(
        select(ApplicationInstance).where(ApplicationInstance.nodename == subdomain).limit(1)
    )
    return result.scalar_one_or_none()


def _is_fresh(entry: ApplicationInstance | None) -> bool:
    if entry is None or entry.updated_at is None:
        return False
    updated = entry.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated < _INVENTORY_STALE_AFTER


async def _inventory_is_fresh(db: AsyncSession) -> bool:
    result = await db.execute(select(ApplicationInstance).limit(1))
    return _is_fresh(result.scalar_one_or_none())


async def instance_for_operation(
    db: AsyncSession, subdomain: str, *, tier: int | None, operation: str, user: AthenaTokenUser
) -> DeploymentInstance:
    """The live instance at ``subdomain``, adopting a cluster-only ADA instance."""

    instance = await live_instance(db, subdomain)
    if instance is not None:
        return instance
    entry = await _inventory_entry(db, subdomain)
    if entry is None and await _inventory_is_fresh(db):
        raise UnprocessableEntityError(
            f"Instance '{subdomain}' was not found in the instance inventory. "
            f"Cannot {operation} a non-existent instance."
        )
    if entry is None:
        logger.warning(
            "%s: instance %r not in a stale inventory; adopting it anyway", operation, subdomain
        )
    return await _adopt(db, subdomain, tier=tier or _tier_of(entry), user=user)


def _tier_of(entry: ApplicationInstance | None) -> int:
    digits = "".join(ch for ch in (entry.tier_name if entry else "") if ch.isdigit())
    return int(digits) if digits and 1 <= int(digits) <= 4 else 1


async def _adopt(
    db: AsyncSession, subdomain: str, *, tier: int, user: AthenaTokenUser
) -> DeploymentInstance:
    version = await ada_version(db)
    now = datetime.now(UTC)
    instance = DeploymentInstance(
        application=version.application,
        application_version=version,
        instance_name=subdomain,
        tier=tier,
        status=DeploymentInstanceStatus.RUNNING.value,
        configuration={"parameters": {"subdomain": subdomain}, "github": {}},
        triggered_by=user.identifier,
        current_step=DeploymentStep.COMPLETED.value,
        completed_at=now,
        rollback_count=0,
    )
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.COMPLETED.value,
            status=DeploymentInstanceStatus.RUNNING.value,
            message="Running instance adopted from the cluster inventory.",
            created_by=user.identifier,
        )
    )
    db.add(instance)
    await db.flush()
    logger.info("Adopting cluster instance %s as an ADA deployment", subdomain)
    return await instances.get_registered_application_deployment(db, instance.id)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _slack_user(user: AthenaTokenUser) -> str:
    return str(user.metadata.get("slack_username") or user.identifier)


async def _operation_row(db: AsyncSession, instance: DeploymentInstance) -> PipelineRun:
    """The operation just dispatched for ``instance`` (open, or already settled)."""

    run = await operations.active_operation(db, instance.id)
    if run is None:
        run = await operations.latest_operation_for_subdomain(
            db, operations.log_subdomain(instance)
        )
    return run


async def deploy(
    db: AsyncSession, store: JobStore, body: DeployRequest, user: AthenaTokenUser
) -> PipelineRun:
    subdomain = body.subdomain
    await ensure_ref_exists(body.branch)  # GitHub first, before any transaction
    existing = await live_instance(db, subdomain)
    entry = await _inventory_entry(db, subdomain)
    if existing is not None or _is_fresh(entry):
        raise ConflictError(
            f"Instance '{subdomain}' is already running. Use the update operation to redeploy it."
        )

    version = await ada_version(db)
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            tier=body.tier,
            instance_name=subdomain,
            parameters={
                "customer_name": body.customer_name.lower(),
                "env_instance": body.env_instance.lower(),
            },
            version=body.branch,
        ),
        instance_name=subdomain,
        triggered_by=_slack_user(user),
    )
    configuration["domain"] = body.domain
    instance = await service.deploy(
        db,
        store,
        version,
        instance_name=subdomain,
        tier=body.tier,
        configuration=configuration,
        triggered_by=user.identifier,
    )
    return await _operation_row(db, instance)


async def update(
    db: AsyncSession, store: JobStore, body: DeployRequest, user: AthenaTokenUser
) -> PipelineRun:
    await ensure_ref_exists(body.branch)
    instance = await instance_for_operation(
        db, body.subdomain, tier=body.tier, operation="update", user=user
    )
    instance = await service.upgrade(
        db,
        store,
        instance.id,
        target_version=body.branch,
        triggered_by=user.identifier,
        allow_same_version=True,
    )
    return await _operation_row(db, instance)


async def move_tier(
    db: AsyncSession, store: JobStore, subdomain: str, tier: int, user: AthenaTokenUser
) -> PipelineRun:
    instance = await instance_for_operation(
        db, subdomain, tier=None, operation="move-tier", user=user
    )
    instance = await service.move_tier(
        db,
        store,
        instance.id,
        target_tier=tier,
        triggered_by=user.identifier,
        dispatch_user=_slack_user(user),
    )
    return await _operation_row(db, instance)


async def terminate(
    db: AsyncSession, store: JobStore, subdomain: str, user: AthenaTokenUser
) -> PipelineRun:
    instance = await instance_for_operation(
        db, subdomain, tier=None, operation="terminate", user=user
    )
    instance = await service.terminate(db, store, instance.id, triggered_by=user.identifier)
    return await _operation_row(db, instance)


async def cancel(db: AsyncSession, operation_id: str, user: AthenaTokenUser) -> PipelineRun:
    """Cancel an operation's GitHub run (only its requester may) and settle it.

    GitHub is called with no transaction open; the settlement is a short
    locked write afterwards.
    """

    run = await operations.get_operation(db, operation_id)
    if run is None:
        # The UI may hold a deployment instance id instead of an operation id.
        try:
            run = await operations.active_operation(db, UUID(operation_id))
        except ValueError:
            run = None
    if run is None:
        raise NotFoundError(f"Pipeline run '{operation_id}' not found.")
    if run.ended_at is not None:
        raise ConflictError("This pipeline run has already finished.")
    if run.triggered_by != user.identifier:
        raise ForbiddenError("You can only cancel runs that you started.")
    if run.deployment_instance_id is None:
        raise ConflictError("This pipeline run has no deployment to cancel.")
    instance = await instances.get_registered_application_deployment(
        db, run.deployment_instance_id
    )
    reference = dict(instance.dispatch_reference or {})
    run_id = run.run_id
    plan = await tracking.plan_fetch(db, instance) if run_id is None else None
    client = await tracking.client_for_reference(db, reference)
    await db.commit()  # no transaction while GitHub answers

    if run_id is None and plan is not None:
        progress, _error = await tracking.fetch(plan)
        run_id = progress["run_id"] if progress else None
    if run_id is None:
        raise ConflictError(
            "Cannot cancel yet: the GitHub Actions run is not linked. "
            "Wait a few seconds and try again."
        )
    if client is None:
        raise ConflictError("This pipeline run cannot be cancelled from Athena.")
    try:
        await client.cancel_workflow_run(int(run_id))
    except httpx.HTTPError as error:
        raise ServiceUnavailableError(f"GitHub refused to cancel run {run_id}.") from error

    instance = await instances.get_registered_application_deployment(
        db, run.deployment_instance_id, for_update=True
    )
    run = await operations.get_operation(db, run.id)
    if run is not None and run.ended_at is None:
        run.run_id = run.run_id or int(run_id)
        await instances.settle_operation(
            db,
            instance,
            Outcome.CANCELLED,
            failure_reason=f"Cancelled by {user.identifier}.",
            message=f"Pipeline run cancelled by {user.identifier}.",
            created_by=user.identifier,
        )
    await db.commit()
    return run

"""Deployment instances: persistence of every lifecycle step (via ``lifecycle``)."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from c2ai.constants.registered_application import (
    DEPLOYMENT_STEP_ORDER,
    ApplicationType,
    DeploymentInstanceStatus,
    DeploymentStep,
)
from c2ai.core.exceptions import (
    DeploymentAlreadyAtVersion,
    DeploymentInstanceNotFound,
    DeploymentProgressConflict,
    DeploymentTerminationNotSupported,
    DeploymentUpgradeNotSupported,
    DuplicateDeploymentInstance,
    DuplicateDeploymentSubdomain,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from c2ai.db.errors import violated_constraint
from c2ai.deployments import lifecycle
from c2ai.deployments.configuration import (
    configured_version,
)
from c2ai.deployments.lifecycle import Operation, Outcome
from c2ai.deployments.operations import (
    active_operation,
    close_operation,
    is_active_operation_conflict,
    log_subdomain,
    open_operation,
    operation_conflict,
    record_dispatch,
)
from c2ai.models.registered_application import (
    DeploymentInstance,
    DeploymentInstanceEvent,
    RegisteredApplicationVersion,
)
from c2ai.schemas.registered_application import (
    RegisteredApplicationDeploymentProgressUpdate,
)

logger = logging.getLogger(__name__)


_DEPLOYMENT_SUBDOMAIN_CONSTRAINT = "uq_deployment_instances_dns_subdomain_active"
_DEPLOYMENT_NAME_CONSTRAINT = "uq_deployment_instances_tier_instance_name"


@dataclass(frozen=True)
class RegisteredApplicationDeploymentPage:
    """Paginated deployment history query result."""

    items: list[DeploymentInstance]
    total: int


async def _flush_operation(db: AsyncSession, instance: DeploymentInstance) -> None:
    """Flush a staged operation, turning index violations into API errors."""

    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        if is_active_operation_conflict(error):
            raise operation_conflict(log_subdomain(instance)) from error
        constraint = violated_constraint(error)
        if constraint == _DEPLOYMENT_SUBDOMAIN_CONSTRAINT:
            raise DuplicateDeploymentSubdomain(instance.subdomain) from error
        if constraint in (_DEPLOYMENT_NAME_CONSTRAINT, None):
            # ``None``: the driver did not report a name; the tier/name pair is
            # the only other unique index an instance row can violate.
            raise DuplicateDeploymentInstance(instance.instance_name, instance.tier) from error
        raise
    except SQLAlchemyError:
        await db.rollback()
        raise


async def create_registered_application_deployment(
    db: AsyncSession,
    version: RegisteredApplicationVersion,
    *,
    instance_name: str,
    tier: int,
    configuration: dict,
    triggered_by: str,
) -> DeploymentInstance:
    """Persist a pending instance and its deploy operation.

    The caller dispatches the pipeline and then commits (``complete_*``), or
    rolls back so a failed dispatch leaves nothing behind.
    """

    dns_configuration = configuration.get("dns")
    instance = DeploymentInstance(
        id=uuid4(),
        application=version.application,
        application_version=version,
        instance_name=instance_name,
        tier=tier,
        configuration=configuration,
        rollback_count=0,
        subdomain=(dns_configuration or {}).get("subdomain"),
        hostname=(dns_configuration or {}).get("hostname"),
    )
    lifecycle.begin(instance, Operation.DEPLOY, triggered_by=triggered_by)
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message="Deployment configuration validated.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    open_operation(
        db,
        instance,
        Operation.DEPLOY,
        triggered_by=triggered_by,
        version=configured_version(configuration, version.application.application_type),
    )
    await _flush_operation(db, instance)
    return instance


async def complete_operation_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
    *,
    event_message: str,
) -> DeploymentInstance:
    """Record the accepted dispatch on the instance and its log row, then commit."""

    lifecycle.mark_dispatched(instance)
    instance.dispatch_reference = dispatch_reference
    record_dispatch(await active_operation(db, instance.id), dispatch_reference)
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message=event_message,
            created_by=instance.triggered_by,
        )
    )
    db.add(instance)
    try:
        # ``updated_at`` is generated by PostgreSQL during UPDATE. Flush and
        # load it while still inside the async transaction so response mapping
        # never attempts an implicit async query after commit.
        await db.flush()
        await db.refresh(instance, attribute_names=["updated_at"])
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return instance


async def complete_registered_application_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
    *,
    event_message: str = "Deployment pipeline dispatched.",
) -> DeploymentInstance:
    """Mark a staged deployment (or redeploy) as dispatched and commit."""

    return await complete_operation_dispatch(
        db, instance, dispatch_reference, event_message=event_message
    )


async def _lock_instance(db: AsyncSession, deployment_id: UUID) -> DeploymentInstance:
    instance = await get_registered_application_deployment(db, deployment_id, for_update=True)
    if instance is None:
        await db.rollback()
        raise DeploymentInstanceNotFound(deployment_id)
    return instance


async def _ensure_can_begin(
    db: AsyncSession, instance: DeploymentInstance, operation: Operation
) -> None:
    try:
        lifecycle.ensure_can_begin(instance, operation)
    except Exception:
        await db.rollback()
        raise


async def prepare_registered_application_upgrade(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    target_version: str,
    triggered_by: str,
    allow_same_version: bool = False,
) -> DeploymentInstance:
    """Lock a running instance and stage its type-specific version upgrade.

    ``allow_same_version`` re-applies the current version (the ADA "update"
    button redeploys a branch in place).
    """

    instance = await _lock_instance(db, deployment_id)
    application_type = instance.application.application_type
    if application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        await db.rollback()
        raise DeploymentUpgradeNotSupported()
    await _ensure_can_begin(db, instance, Operation.UPGRADE)

    # A failed instance may retry the version its previous upgrade stored.
    retrying_failed_upgrade = instance.status == DeploymentInstanceStatus.FAILED.value
    configuration = deepcopy(instance.configuration)
    if application_type == ApplicationType.CONTAINERIZED.value:
        container = configuration.get("container")
        if not isinstance(container, dict) or not isinstance(
            container.get("image_tag"), str
        ):
            await db.rollback()
            raise ServiceUnavailableError(
                "The deployment has no upgradeable container configuration."
            )
        container["image_tag"] = target_version
    else:
        github = configuration.get("github")
        if not isinstance(github, dict):
            await db.rollback()
            raise ServiceUnavailableError(
                "The deployment has no upgradeable GitHub configuration."
            )
        github["version"] = target_version
    current = configured_version(instance.configuration, application_type)
    if current == target_version and not (retrying_failed_upgrade or allow_same_version):
        await db.rollback()
        raise DeploymentAlreadyAtVersion(target_version)
    # Keep the last configuration that ran successfully for rollback. Retrying
    # a failed upgrade must not replace it with the failed attempt.
    if instance.status == DeploymentInstanceStatus.RUNNING.value or (
        instance.previous_configuration is None
    ):
        instance.previous_configuration = deepcopy(instance.configuration)
    instance.configuration = configuration
    lifecycle.begin(instance, Operation.UPGRADE, triggered_by=triggered_by)
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message=f"Upgrade to version '{target_version}' requested.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    open_operation(
        db, instance, Operation.UPGRADE, triggered_by=triggered_by, version=target_version
    )
    await _flush_operation(db, instance)
    return instance


async def complete_registered_application_upgrade_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
    *,
    event_message: str | None = None,
) -> DeploymentInstance:
    """Commit a version change after its update pipeline is dispatched."""

    target_version = configured_version(
        instance.configuration, instance.application.application_type
    )
    return await complete_operation_dispatch(
        db,
        instance,
        dispatch_reference,
        event_message=event_message
        or f"Application upgrade pipeline dispatched for version '{target_version}'.",
    )


async def prepare_registered_application_termination(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    triggered_by: str,
) -> DeploymentInstance:
    """Lock a supported instance and stage its destructive termination."""

    instance = await _lock_instance(db, deployment_id)
    if instance.application.application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        await db.rollback()
        raise DeploymentTerminationNotSupported()
    await _ensure_can_begin(db, instance, Operation.TERMINATE)

    lifecycle.begin(instance, Operation.TERMINATE, triggered_by=triggered_by)
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message="Application deployment termination requested.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    open_operation(db, instance, Operation.TERMINATE, triggered_by=triggered_by)
    await _flush_operation(db, instance)
    return instance


async def complete_registered_application_termination_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
) -> DeploymentInstance:
    """Commit terminating state after the cleanup pipeline is dispatched."""

    return await complete_operation_dispatch(
        db,
        instance,
        dispatch_reference,
        event_message="Application termination pipeline dispatched.",
    )


async def prepare_registered_application_move_tier(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    target_tier: int,
    triggered_by: str,
) -> DeploymentInstance:
    """Lock a GitHub Workflow instance and stage its move to ``target_tier``."""

    instance = await _lock_instance(db, deployment_id)
    if instance.application.application_type != ApplicationType.GITHUB_WORKFLOW.value:
        await db.rollback()
        raise UnprocessableEntityError(
            "Only GitHub Workflow deployments can move to another tier."
        )
    await _ensure_can_begin(db, instance, Operation.MOVE_TIER)
    if instance.tier == target_tier:
        await db.rollback()
        raise UnprocessableEntityError(f"The deployment already runs in Tier {target_tier}.")

    lifecycle.begin(instance, Operation.MOVE_TIER, triggered_by=triggered_by)
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message=f"Move from Tier {instance.tier} to Tier {target_tier} requested.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    open_operation(
        db, instance, Operation.MOVE_TIER, triggered_by=triggered_by, tier=target_tier
    )
    await _flush_operation(db, instance)
    return instance


async def list_registered_application_deployments(
    db: AsyncSession,
    *,
    tier: int | None = None,
    application_id: UUID | None = None,
    instance: str | None = None,
    deployment_status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> RegisteredApplicationDeploymentPage:
    """Return deployment instances newest-first with optional filters."""

    filters = []
    if tier is not None:
        filters.append(DeploymentInstance.tier == tier)
    if application_id is not None:
        filters.append(DeploymentInstance.application_id == application_id)
    if instance is not None:
        filters.append(
            or_(
                DeploymentInstance.subdomain == instance,
                DeploymentInstance.instance_name == instance,
            )
        )
    if deployment_status is not None:
        filters.append(DeploymentInstance.status == deployment_status)

    count_result = await db.execute(
        select(func.count(DeploymentInstance.id)).where(*filters)
    )
    total = int(count_result.scalar_one())
    result = await db.execute(
        select(DeploymentInstance)
        .where(*filters)
        .options(
            joinedload(DeploymentInstance.application),
            joinedload(DeploymentInstance.application_version),
        )
        .order_by(DeploymentInstance.created_at.desc(), DeploymentInstance.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return RegisteredApplicationDeploymentPage(
        items=list(result.scalars().all()),
        total=total,
    )


async def get_registered_application_deployment(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    for_update: bool = False,
) -> DeploymentInstance | None:
    """Load one deployment instance, template version, and event history."""

    statement = (
        select(DeploymentInstance)
        .where(DeploymentInstance.id == deployment_id)
        .options(
            joinedload(DeploymentInstance.application),
            joinedload(DeploymentInstance.application_version).joinedload(
                RegisteredApplicationVersion.application
            ),
            joinedload(DeploymentInstance.application_version).joinedload(
                RegisteredApplicationVersion.github_configuration
            ),
            joinedload(DeploymentInstance.application_version).joinedload(
                RegisteredApplicationVersion.container_configuration
            ),
            selectinload(DeploymentInstance.events),
        )
    )
    if for_update:
        statement = statement.with_for_update(of=DeploymentInstance)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def settle_operation(
    db: AsyncSession,
    instance: DeploymentInstance,
    outcome: Outcome,
    *,
    at: datetime | None = None,
    failure_reason: str | None = None,
    message: str | None = None,
    created_by: str = "deployment-pipeline",
) -> None:
    """Apply an operation's final outcome to the instance and close its log row.

    The caller commits. Used by pipeline callbacks, GitHub run conclusions,
    cancellation, and the reconciler.
    """

    at = at or datetime.now(UTC)
    run = await active_operation(db, instance.id)
    operation = lifecycle.operation_of(instance)
    target_tier = run.tier if run is not None and operation is Operation.MOVE_TIER else None
    status = lifecycle.settle(
        instance,
        operation,
        outcome,
        at=at,
        failure_reason=failure_reason,
        target_tier=target_tier,
    )
    close_operation(run, outcome, at=at)
    instance.events.append(
        DeploymentInstanceEvent(
            step=instance.current_step,
            status=status,
            message=message,
            failure_reason=instance.failure_reason,
            created_by=created_by,
        )
    )
    db.add(instance)


async def update_registered_application_deployment_progress(
    db: AsyncSession,
    deployment_id: UUID,
    payload: RegisteredApplicationDeploymentProgressUpdate,
) -> DeploymentInstance:
    """Apply one monotonic, idempotent pipeline progress update."""

    instance = await _lock_instance(db, deployment_id)

    next_step = payload.current_step.value
    next_status = payload.status.value
    is_upgrade = instance.status == DeploymentInstanceStatus.UPDATING.value
    is_termination = instance.status == DeploymentInstanceStatus.TERMINATING.value
    if (
        instance.current_step == next_step
        and instance.status == next_status
        and instance.failure_reason == payload.failure_reason
    ):
        await db.commit()
        return instance

    if instance.current_step in {
        DeploymentStep.COMPLETED.value,
        DeploymentStep.FAILED.value,
    }:
        await db.rollback()
        raise DeploymentProgressConflict(
            f"Deployment is already terminal at step '{instance.current_step}'."
        )

    is_terminal_step = next_step in {
        DeploymentStep.COMPLETED.value,
        DeploymentStep.FAILED.value,
    }
    if is_upgrade and not is_terminal_step and next_status != (
        DeploymentInstanceStatus.UPDATING.value
    ):
        await db.rollback()
        raise DeploymentProgressConflict(
            "Upgrade progress must retain updating status until it completes or fails."
        )
    if not is_upgrade and next_status == DeploymentInstanceStatus.UPDATING.value:
        await db.rollback()
        raise DeploymentProgressConflict(
            "Updating status is only valid after an upgrade has been requested."
        )
    if is_termination and not is_terminal_step and next_status != (
        DeploymentInstanceStatus.TERMINATING.value
    ):
        await db.rollback()
        raise DeploymentProgressConflict(
            "Termination progress must retain terminating status until it completes or fails."
        )
    if (
        is_termination
        and next_step == DeploymentStep.COMPLETED.value
        and next_status != DeploymentInstanceStatus.TERMINATED.value
    ):
        await db.rollback()
        raise DeploymentProgressConflict(
            "Termination completion requires terminated status."
        )
    if not is_termination and next_status in {
        DeploymentInstanceStatus.TERMINATING.value,
        DeploymentInstanceStatus.TERMINATED.value,
    }:
        await db.rollback()
        raise DeploymentProgressConflict(
            "Termination status is only valid after termination has been requested."
        )

    if next_step not in {DeploymentStep.FAILED.value, DeploymentStep.COMPLETED.value}:
        current_position = DEPLOYMENT_STEP_ORDER.index(instance.current_step)
        next_position = DEPLOYMENT_STEP_ORDER.index(next_step)
        if next_position < current_position:
            await db.rollback()
            raise DeploymentProgressConflict(
                f"Deployment progress cannot move backwards from "
                f"'{instance.current_step}' to '{next_step}'."
            )

    if next_step == DeploymentStep.CONFIGURING_DNS.value and (
        not instance.subdomain or is_upgrade
    ):
        await db.rollback()
        raise DeploymentProgressConflict(
            "DNS progress is only supported for initial containerized deployments."
        )
    if (
        instance.subdomain
        and not is_upgrade
        and next_step == DeploymentStep.COMPLETED.value
        and instance.current_step != DeploymentStep.CONFIGURING_DNS.value
    ):
        await db.rollback()
        raise DeploymentProgressConflict(
            "Containerized deployments must configure DNS before completion."
        )

    if is_terminal_step:
        outcome = (
            Outcome.SUCCESS if next_step == DeploymentStep.COMPLETED.value else Outcome.FAILURE
        )
        await settle_operation(
            db,
            instance,
            outcome,
            failure_reason=payload.failure_reason,
            message=payload.message,
        )
    else:
        # In-progress report: the step advances, the status stays the
        # operation's in-progress status (validated above).
        instance.current_step = next_step
        instance.status = next_status
        instance.failure_reason = payload.failure_reason
        if next_step == DeploymentStep.CONFIGURING_DNS.value:
            instance.dns_status = "deleting" if is_termination else "configuring"
        instance.events.append(
            DeploymentInstanceEvent(
                step=next_step,
                status=next_status,
                message=payload.message,
                failure_reason=payload.failure_reason,
                created_by="deployment-pipeline",
            )
        )
        db.add(instance)
    try:
        await db.flush()
        await db.refresh(instance, attribute_names=["updated_at"])
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return instance


async def prepare_registered_application_rollback(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    triggered_by: str,
) -> DeploymentInstance:
    """Lock an instance and stage a return to its previous version (or a redeploy)."""

    instance = await _lock_instance(db, deployment_id)
    await _ensure_can_begin(db, instance, Operation.ROLLBACK)

    application_type = instance.application.application_type
    instance.rollback_count += 1
    previous = instance.previous_configuration
    previous_version = (
        configured_version(previous, application_type) if isinstance(previous, dict) else None
    )
    if previous_version is not None:
        # Return to the configuration that ran before the latest upgrade. The
        # in-place upgrade pipeline performs the change, so progress follows
        # the upgrade rules (status "updating"); swapping keeps a roll-forward.
        instance.previous_configuration = deepcopy(instance.configuration)
        instance.configuration = deepcopy(previous)
        lifecycle.begin(instance, Operation.ROLLBACK, triggered_by=triggered_by)
        logged = Operation.ROLLBACK
        message = (
            f"Rollback #{instance.rollback_count} to version '{previous_version}' requested."
        )
    else:
        # Never upgraded: a rollback re-applies the stored configuration.
        lifecycle.begin(instance, Operation.ROLLBACK, triggered_by=triggered_by, redeploy=True)
        logged = Operation.DEPLOY
        message = f"Rollback #{instance.rollback_count} requested."
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=instance.status,
            message=message,
            created_by=triggered_by,
        )
    )
    db.add(instance)
    open_operation(
        db,
        instance,
        logged,
        triggered_by=triggered_by,
        version=configured_version(instance.configuration, application_type),
    )
    await _flush_operation(db, instance)
    return instance

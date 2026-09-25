"""Deployment instances of registered applications: deploy, upgrade, roll back,
terminate, progress callbacks, and history."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.api.registered_applications import clients
from c2ai.api.registered_applications.clients import PREFIX, TAGS
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.container_registry import ContainerRegistryClient
from c2ai.constants.registered_application import (
    ApplicationType,
    DeploymentInstanceStatus,
    DeploymentStep,
)
from c2ai.core.exceptions import (
    ContainerDeploymentNotSupported,
    DeploymentInstanceNotFound,
    DeploymentTerminationConfirmationMismatch,
    DeploymentTerminationNotAvailable,
    DeploymentTerminationNotSupported,
    DeploymentUpgradeNotAvailable,
    DeploymentUpgradeNotSupported,
    RegisteredApplicationNotFound,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from c2ai.db.session import get_db_session as db_session
from c2ai.deployments import operations, repository as instances, service
from c2ai.deployments.callbacks import verify_callback
from c2ai.deployments.configuration import (
    build_container_deployment_configuration,
    build_deployment_configuration,
    default_github_instance_name,
    resolve_github_deployment_instance_name,
)
from c2ai.jobs import JobStore, get_job_store
from c2ai.registration import credentials, repository as applications, secrets as secret_store
from c2ai.schemas.registered_application import (
    ContainerRegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentDetail,
    RegisteredApplicationDeploymentEventOut,
    RegisteredApplicationDeploymentList,
    RegisteredApplicationDeploymentOut,
    RegisteredApplicationDeploymentProgressUpdate,
    RegisteredApplicationDeploymentTerminate,
    RegisteredApplicationDeploymentUpgrade,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix=PREFIX, tags=TAGS)


async def _require_deployment_callback_token(
    deployment_id: UUID,
    callback_token: str | None = Header(default=None, alias="X-Athena-Deployment-Token"),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Authenticate a pipeline callback for the deployment's open operation."""

    await verify_callback(db, deployment_id, callback_token)


def _deployment_out(
    instance,
    *,
    include_events: bool = False,
    application=None,
    application_version=None,
    workflow_progress=None,
    workflow_progress_error: str | None = None,
) -> RegisteredApplicationDeploymentOut | RegisteredApplicationDeploymentDetail:
    """Map a deployment ORM graph to its public lifecycle contract."""

    model = (
        RegisteredApplicationDeploymentDetail
        if include_events
        else RegisteredApplicationDeploymentOut
    )
    application = application or instance.application
    application_version = application_version or instance.application_version
    values = {
        "id": instance.id,
        "application_id": instance.application_id or application.id,
        "application_name": application.name,
        "application_type": application.application_type,
        "application_version": application_version.version,
        "instance_name": instance.instance_name,
        "tier": instance.tier,
        "status": instance.status,
        "configuration": instance.configuration,
        "triggered_by": instance.triggered_by,
        "dispatch_reference": instance.dispatch_reference,
        "current_step": instance.current_step
        or DeploymentStep.VALIDATING_CONFIGURATION.value,
        "failure_reason": instance.failure_reason,
        "completed_at": instance.completed_at,
        "terminated_at": instance.terminated_at,
        "rollback_count": instance.rollback_count or 0,
        "can_rollback": instance.status
        in {
            DeploymentInstanceStatus.RUNNING.value,
            DeploymentInstanceStatus.FAILED.value,
        },
        "subdomain": instance.subdomain,
        "hostname": instance.hostname,
        "dns_status": instance.dns_status,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at or instance.created_at,
    }
    if include_events:
        values["events"] = [
            RegisteredApplicationDeploymentEventOut(
                id=event.id,
                step=event.step,
                status=event.status,
                message=event.message,
                failure_reason=event.failure_reason,
                created_by=event.created_by,
                created_at=event.created_at,
            )
            for event in instance.events
        ]
        values["workflow_progress"] = workflow_progress
        values["workflow_progress_error"] = workflow_progress_error
    return model(**values)


@router.get(
    "/deployments",
    response_model=RegisteredApplicationDeploymentList,
    status_code=status.HTTP_200_OK,
    summary="List registered application deployment instances",
)
async def list_registered_application_deployments(
    tier: int | None = Query(default=None, ge=1, le=4),
    application_id: UUID | None = Query(default=None),
    instance: str | None = Query(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description="Exact deployment subdomain or instance name.",
    ),
    deployment_status: DeploymentInstanceStatus | None = Query(
        default=None,
        alias="status",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationDeploymentList:
    """Return durable deployment history, optionally scoped to one Tier."""

    page = await instances.list_registered_application_deployments(
        db,
        tier=tier,
        application_id=application_id,
        instance=instance,
        deployment_status=(
            deployment_status.value if deployment_status is not None else None
        ),
        offset=offset,
        limit=limit,
    )
    return RegisteredApplicationDeploymentList(
        items=[_deployment_out(instance) for instance in page.items],
        total=page.total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/deployments/{deployment_id}",
    response_model=RegisteredApplicationDeploymentDetail,
    status_code=status.HTTP_200_OK,
    summary="Get deployment progress and history",
)
async def get_registered_application_deployment(
    deployment_id: UUID,
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationDeploymentDetail:
    """Return current progress, immutable configuration, and lifecycle events."""

    instance = await instances.get_registered_application_deployment(
        db,
        deployment_id,
    )
    if instance is None:
        raise DeploymentInstanceNotFound(deployment_id)
    run = await operations.latest_operation_for_instance(db, instance.id)
    workflow_progress = run.progress if run is not None else None
    workflow_progress_error = (
        "Waiting for the matching GitHub Actions run."
        if run is not None and run.ended_at is None and run.progress is None
        and run.dispatch_state == "dispatched"
        else None
    )
    return _deployment_out(
        instance,
        include_events=True,
        workflow_progress=workflow_progress,
        workflow_progress_error=workflow_progress_error,
    )


@router.post(
    "/deployments/{deployment_id}/progress",
    response_model=RegisteredApplicationDeploymentDetail,
    status_code=status.HTTP_200_OK,
    summary="Report deployment pipeline progress",
    dependencies=[Depends(_require_deployment_callback_token)],
)
async def report_registered_application_deployment_progress(
    deployment_id: UUID,
    payload: RegisteredApplicationDeploymentProgressUpdate,
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationDeploymentDetail:
    """Accept an authenticated, idempotent lifecycle update from the pipeline."""

    instance = (
        await instances.update_registered_application_deployment_progress(
            db,
            deployment_id,
            payload,
        )
    )
    await db.commit()
    return _deployment_out(instance, include_events=True)


@router.post(
    "/deployments/{deployment_id}/rollback",
    response_model=RegisteredApplicationDeploymentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rollback a registered application deployment",
)
async def rollback_registered_application_deployment(
    deployment_id: UUID,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> RegisteredApplicationDeploymentDetail:
    """Return the instance to its previous version, or redeploy it.

    After an upgrade the instance keeps the configuration it ran before; a
    rollback dispatches the in-place upgrade pipeline back to that version.
    An instance that was never upgraded has nothing to return to, so its
    stored configuration is deployed again (a retry of a failed deployment).
    """

    instance = await service.rollback(
        db, store, deployment_id, triggered_by=current_user.identifier
    )
    logger.info(
        "Rollback #%s dispatched id=%s by=%s",
        instance.rollback_count,
        instance.id,
        current_user.identifier,
    )
    return _deployment_out(instance, include_events=True)


@router.post(
    "/deployments/{deployment_id}/upgrade",
    response_model=RegisteredApplicationDeploymentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upgrade a registered-application deployment",
)
async def upgrade_registered_application_deployment(
    deployment_id: UUID,
    payload: RegisteredApplicationDeploymentUpgrade,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
    registry_client: ContainerRegistryClient = Depends(clients.container_registry_client),
    store: JobStore = Depends(get_job_store),
) -> RegisteredApplicationDeploymentDetail:
    """Validate one version and dispatch the type-specific upgrade workflow."""

    instance = await instances.get_registered_application_deployment(
        db,
        deployment_id,
    )
    if instance is None:
        raise DeploymentInstanceNotFound(deployment_id)
    application_type = instance.application.application_type
    if application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        raise DeploymentUpgradeNotSupported()
    if instance.status not in {
        DeploymentInstanceStatus.RUNNING.value,
        DeploymentInstanceStatus.FAILED.value,
    }:
        raise DeploymentUpgradeNotAvailable(instance.status)

    if application_type == ApplicationType.CONTAINERIZED.value:
        container_configuration = instance.configuration.get("container")
        if not isinstance(container_configuration, dict):
            raise ServiceUnavailableError(
                "The deployment has no upgradeable container configuration."
            )
        registry = container_configuration.get("registry")
        repository = container_configuration.get("image_repository")
        credential_id = container_configuration.get("image_pull_secret")
        if not isinstance(registry, str) or not isinstance(repository, str):
            raise ServiceUnavailableError(
                "The deployment has no upgradeable container image reference."
            )
        if credential_id is not None and not isinstance(credential_id, str):
            raise ServiceUnavailableError(
                "The deployment has an invalid registry credential reference."
            )

        # A private registry rejects an anonymous manifest request, so the tag
        # check authenticates with the same stored credential the deployment
        # was registered with.
        registry_runtime = (
            await credentials.resolve_container_registry_credentials(
                db,
                instance.application_version_id,
            )
        )
        await db.commit()  # no transaction while the registry answers
        await registry_client.get_tag(
            registry=registry,
            repository=repository,
            tag=payload.version,
            credential_id=credential_id,
            username=registry_runtime.username if registry_runtime else None,
            password=registry_runtime.password if registry_runtime else None,
        )
    instance = await service.upgrade(
        db,
        store,
        deployment_id,
        target_version=payload.version,
        triggered_by=current_user.identifier,
    )
    logger.info(
        "Registered deployment upgrade dispatched id=%s type=%s version=%s by=%s",
        instance.id,
        application_type,
        payload.version,
        current_user.identifier,
    )
    return _deployment_out(instance, include_events=True)


@router.post(
    "/deployments/{deployment_id}/terminate",
    response_model=RegisteredApplicationDeploymentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Terminate a registered-application deployment",
)
async def terminate_registered_application_deployment(
    deployment_id: UUID,
    payload: RegisteredApplicationDeploymentTerminate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> RegisteredApplicationDeploymentDetail:
    """Confirm and dispatch the type-specific termination workflow."""

    instance = await instances.get_registered_application_deployment(
        db,
        deployment_id,
    )
    if instance is None:
        raise DeploymentInstanceNotFound(deployment_id)
    application_type = instance.application.application_type
    if application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        raise DeploymentTerminationNotSupported()
    if instance.status not in {
        DeploymentInstanceStatus.RUNNING.value,
        DeploymentInstanceStatus.FAILED.value,
    }:
        raise DeploymentTerminationNotAvailable(instance.status)
    if payload.confirmation != instance.instance_name:
        raise DeploymentTerminationConfirmationMismatch()

    instance = await service.terminate(
        db, store, deployment_id, triggered_by=current_user.identifier
    )
    logger.info(
        "Registered deployment termination dispatched id=%s type=%s by=%s",
        instance.id,
        application_type,
        current_user.identifier,
    )
    return _deployment_out(instance, include_events=True)


@router.post(
    "/{application_id}/tiers/{tier}/deployments",
    response_model=RegisteredApplicationDeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy a registered containerized application into a Tier",
)
async def deploy_registered_container_application(
    application_id: UUID,
    tier: Annotated[int, Path(ge=1, le=4)],
    payload: ContainerRegisteredApplicationDeploymentCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
    registry_client: ContainerRegistryClient = Depends(clients.container_registry_client),
    store: JobStore = Depends(get_job_store),
) -> RegisteredApplicationDeploymentOut:
    """Validate a tag and deploy the stored container template into the path Tier."""

    version = (
        await applications.get_current_registered_application_version(
            db,
            application_id,
        )
    )
    if version is None:
        raise RegisteredApplicationNotFound(application_id)
    if version.application.application_type != ApplicationType.CONTAINERIZED.value:
        raise ContainerDeploymentNotSupported()

    template = version.container_configuration
    if template is None:
        raise RuntimeError("Container application version has no container configuration")

    registry_runtime = None
    if template.registry_password_encrypted is not None:
        registry_runtime = (
            await credentials.resolve_container_registry_credentials(
                db,
                version.id,
            )
        )
    await db.commit()  # no transaction while the registry answers
    await registry_client.get_tag(
        registry=template.registry,
        repository=template.image_repository,
        tag=payload.version,
        credential_id=template.registry_credential_id,
        username=registry_runtime.username if registry_runtime else None,
        password=registry_runtime.password if registry_runtime else None,
    )

    configuration = build_container_deployment_configuration(
        version,
        payload,
        tier=tier,
        managed_secrets=await secret_store.list_active_container_application_secrets(
            db, application_id
        ),
    )
    instance = await service.deploy(
        db,
        store,
        version,
        instance_name=payload.instance_name,
        tier=tier,
        configuration=configuration,
        triggered_by=current_user.identifier,
    )
    logger.info(
        "Container deployment dispatched id=%s application=%s tier=%s by=%s",
        instance.id,
        version.application.name,
        tier,
        current_user.identifier,
    )
    return _deployment_out(
        instance,
        application=version.application,
        application_version=version,
    )


@router.post(
    "/{application_id}/deployments",
    response_model=RegisteredApplicationDeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy a registered application",
)
async def deploy_registered_application(
    application_id: UUID,
    payload: RegisteredApplicationDeploymentCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
    store: JobStore = Depends(get_job_store),
) -> RegisteredApplicationDeploymentOut:
    """Validate, persist, and dispatch a deployment from the current template version."""

    version = (
        await applications.get_current_registered_application_version(
            db,
            application_id,
        )
    )
    if version is None:
        raise RegisteredApplicationNotFound(application_id)
    if version.application.application_type == ApplicationType.CONTAINERIZED.value:
        raise UnprocessableEntityError(
            "Use the Tier-scoped container deployment endpoint for containerized "
            "applications."
        )

    fallback_instance_name = payload.instance_name or default_github_instance_name(
        version.application.name,
        payload.tier,
    )
    configuration = build_deployment_configuration(
        version,
        payload,
        instance_name=fallback_instance_name,
        triggered_by=current_user.identifier,
    )
    instance_name = resolve_github_deployment_instance_name(
        configuration,
        application_name=version.application.name,
        tier=payload.tier,
        supplied_instance_name=payload.instance_name,
    )
    instance = await service.deploy(
        db,
        store,
        version,
        instance_name=instance_name,
        tier=payload.tier,
        configuration=configuration,
        triggered_by=current_user.identifier,
    )
    logger.info(
        "Registered application deployment dispatched id=%s application=%s tier=%s by=%s",
        instance.id,
        version.application.name,
        instance.tier,
        current_user.identifier,
    )
    return _deployment_out(
        instance,
        application=version.application,
        application_version=version,
    )

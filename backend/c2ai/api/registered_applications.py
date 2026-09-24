# pylint: disable=import-error
"""API routes for registering reusable application templates."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.registered_application import RegisteredApplicationVersion
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.container_registry import (
    ContainerRegistryClient,
    build_image_reference,
)
from c2ai.clients.container_secret_provider import ContainerSecretProviderClient
from c2ai.constants.registered_application import (
    ApplicationStatus,
    ApplicationType,
    DeploymentInstanceStatus,
    DeploymentStep,
)
from c2ai.crud import (
    github_connection as crud_github_connection,
    registered_application as crud_registered_application,
)
from c2ai.db.session import get_db_session as db_session
from c2ai.core.exceptions import (
    ContainerDeploymentNotSupported,
    ContainerImageTagsNotSupported,
    DeploymentInstanceNotFound,
    DeploymentTerminationConfirmationMismatch,
    DeploymentTerminationNotAvailable,
    DeploymentTerminationNotSupported,
    DeploymentUpgradeNotAvailable,
    DeploymentUpgradeNotSupported,
    RegisteredApplicationNotFound,
    ServiceUnavailableError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from c2ai.services.github_workflow_progress import (
    get_registered_deployment_workflow_progress,
)
from c2ai.services.llm_models import (
    PRIVATE_MODEL_NAMES,
    PUBLIC_MODEL_NAMES,
    is_supported_model,
    normalize_model_name,
    pricing_unavailable_message,
    resolve_llm_provider,
)
from c2ai.services.registered_application_deployment import (
    build_container_deployment_configuration,
    build_deployment_configuration,
    configured_version,
    default_github_instance_name,
    dispatch_registered_application_deployment,
    dispatch_registered_application_termination,
    dispatch_registered_application_upgrade,
    resolve_github_deployment_instance_name,
)
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerApplicationSecretList,
    ContainerApplicationSecretOut,
    ContainerApplicationSecretUpdate,
    ContainerConfigurationOut,
    ContainerImageTagList,
    ContainerImageTagOut,
    ContainerRegisteredApplicationCreate,
    ContainerRegisteredApplicationDeploymentCreate,
    ContainerRegisteredApplicationDetail,
    GitHubRegisteredApplicationCreate,
    GitHubRegisteredApplicationDetail,
    GitHubRepositoryTagList,
    GitHubWorkflowConfiguration,
    LLMConfigurationOut,
    LLMModelList,
    LLMModelOut,
    RegisteredApplicationCatalogItem,
    RegisteredApplicationCatalogResponse,
    RegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentDetail,
    RegisteredApplicationDeploymentEventOut,
    RegisteredApplicationDeploymentList,
    RegisteredApplicationDeploymentOut,
    RegisteredApplicationDeploymentProgressUpdate,
    RegisteredApplicationDeploymentTerminate,
    RegisteredApplicationDeploymentUpgrade,
    RegisteredApplicationDetail,
    RegisteredContainerParameterValue,
    RegisteredParameterDefinition,
    TierDeploymentSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/registered-applications",
    tags=["Registered Applications"],
)


def _get_container_secret_provider() -> ContainerSecretProviderClient:
    """Build the configured write-only secret broker client."""

    return ContainerSecretProviderClient()


def _get_container_registry_client() -> ContainerRegistryClient:
    """Build the registry adapter used for image-tag discovery."""

    return ContainerRegistryClient()


def _require_deployment_callback_token(
    callback_token: str | None = Header(
        default=None,
        alias="X-Athena-Deployment-Token",
    ),
) -> None:
    """Authenticate progress callbacks from the external deployment pipeline."""

    expected_token = os.getenv("DEPLOYMENT_CALLBACK_TOKEN")
    if not expected_token:
        raise ServiceUnavailableError(
            "Deployment progress callbacks are not configured."
        )
    if not callback_token or not hmac.compare_digest(callback_token, expected_token):
        raise UnauthorizedError(
            "Invalid deployment callback token.",
            code="InvalidDeploymentCallbackToken",
        )


async def _dispatch_deployment(
    db: AsyncSession,
    version: RegisteredApplicationVersion,
    *,
    deployment_id: UUID,
    instance_name: str,
    tier: int,
    configuration: dict,
    triggered_by: str,
) -> dict:
    """Dispatch a deploy pipeline with the credentials its application type needs.

    Used for both first deployments and rollbacks, so a redeploy always sends
    the same registry, LLM, and GitHub credentials the original did.
    """

    options: dict = {}
    if version.application.application_type == ApplicationType.GITHUB_WORKFLOW.value:
        github = version.github_configuration
        if github is not None:
            runtime = await crud_github_connection.resolve_github_connection(
                db, github.github_connection_id
            )
            if runtime is not None:
                options["github_token"] = runtime.token
                options["github_api_base_url"] = runtime.api_base_url
    else:
        template = version.container_configuration
        if template is not None and template.registry_password_encrypted is not None:
            registry = await crud_registered_application.resolve_container_registry_credentials(
                db, version.id
            )
            if registry is not None:
                options["registry_username"] = registry.username
                options["registry_token"] = registry.password
        options["llm_api_token"] = await crud_registered_application.resolve_llm_api_token(
            db, version.id
        )
    return await dispatch_registered_application_deployment(
        version,
        deployment_id=deployment_id,
        instance_name=instance_name,
        tier=tier,
        configuration=configuration,
        triggered_by=triggered_by,
        **options,
    )


def _github_registration_detail(
    version: RegisteredApplicationVersion,
) -> GitHubRegisteredApplicationDetail:
    """Map a persisted version graph to the public GitHub detail contract."""

    application = version.application
    configuration = version.github_configuration
    if configuration is None:
        raise RuntimeError("GitHub application version has no GitHub configuration")

    llm_configuration = version.llm_configuration
    return GitHubRegisteredApplicationDetail(
        id=application.id,
        name=application.name,
        description=version.description,
        application_type=application.application_type,
        status=application.status,
        version=version.version,
        github=GitHubWorkflowConfiguration(
            github_connection_id=configuration.github_connection_id,
            trigger_method=configuration.trigger_method,
            repository=configuration.repository,
            code_repository=configuration.code_repository,
            workflow_file_path=configuration.workflow_file_path,
            ref=configuration.ref,
        ),
        parameters=[
            RegisteredParameterDefinition(
                key=parameter.key,
                type=parameter.parameter_type,
            )
            for parameter in version.parameters
        ],
        llm=(
            LLMConfigurationOut(
                endpoint=llm_configuration.endpoint,
                model_name=llm_configuration.model_name,
            )
            if llm_configuration is not None
            else None
        ),
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _container_registration_detail(
    version: RegisteredApplicationVersion,
) -> ContainerRegisteredApplicationDetail:
    """Map a persisted version graph to the public container detail contract."""

    application = version.application
    configuration = version.container_configuration
    if configuration is None:
        raise RuntimeError("Container application version has no container configuration")

    llm_configuration = version.llm_configuration
    return ContainerRegisteredApplicationDetail(
        id=application.id,
        name=application.name,
        description=version.description,
        application_type=application.application_type,
        status=application.status,
        version=version.version,
        container=ContainerConfigurationOut(
            registry=configuration.registry,
            image_registry=configuration.image_repository,
            registry_username=configuration.registry_username,
            tag=configuration.default_image_tag,
            pull_policy=configuration.image_pull_policy,
            port=configuration.container_port,
            expose_public_service=configuration.expose_public_service,
            gpu_request=configuration.gpu_request,
            cpu_request=configuration.cpu_request,
            memory_request=configuration.memory_request,
            scaling=configuration.scaling,
            storage=configuration.storage,
        ),
        parameters=[
            RegisteredContainerParameterValue(
                key=parameter.key,
                value=(
                    parameter.default_value
                    if isinstance(parameter.default_value, str)
                    else ""
                ),
            )
            for parameter in version.parameters
        ],
        llm=(
            LLMConfigurationOut(
                endpoint=llm_configuration.endpoint,
                model_name=llm_configuration.model_name,
            )
            if llm_configuration is not None
            else None
        ),
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _catalog_item(
    record: crud_registered_application.RegisteredApplicationCatalogRecord,
) -> RegisteredApplicationCatalogItem:
    """Map one catalog database aggregate to its public contract."""

    application = record.application
    return RegisteredApplicationCatalogItem(
        id=application.id,
        name=application.name,
        description=record.description,
        application_type=application.application_type,
        status=application.status,
        current_version=application.current_version,
        total_deployed_instances=record.total_deployed_instances,
        tiers_deployed_to=[
            TierDeploymentSummary(tier=f"Tier {tier}", instances=count)
            for tier, count in sorted(record.tiers_deployed_to.items())
        ],
        can_delete=record.can_delete,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


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


def _container_secret_out(secret) -> ContainerApplicationSecretOut:
    """Map secret metadata without ever accessing or exposing its value."""

    if not secret.secret_reference:
        raise RuntimeError("Managed secret has no external provider reference")
    return ContainerApplicationSecretOut(
        id=secret.id,
        application_id=secret.application_id,
        name=secret.name,
        environment_variable=secret.environment_variable,
        reference=secret.secret_reference,
        created_by=secret.created_by,
        updated_by=secret.updated_by,
        created_at=secret.created_at,
        updated_at=secret.updated_at or secret.created_at,
    )


@router.get(
    "",
    response_model=RegisteredApplicationCatalogResponse,
    status_code=status.HTTP_200_OK,
    summary="List registered applications",
)
async def list_registered_applications(
    search: str | None = Query(default=None, max_length=200),
    application_type: ApplicationType | None = Query(default=None),
    application_status: ApplicationStatus | None = Query(default=None, alias="status"),
    tier: int | None = Query(default=None, ge=1, le=4),
    sort_by: Literal["name", "type", "instances", "tiers", "created"] = Query(
        default="name"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationCatalogResponse:
    """Search, filter, sort, and paginate the global application catalog."""

    page = await crud_registered_application.list_registered_applications(
        db,
        search=search,
        application_type=application_type.value if application_type else None,
        application_status=application_status.value if application_status else None,
        tier=tier,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
        limit=limit,
    )
    return RegisteredApplicationCatalogResponse(
        items=[_catalog_item(record) for record in page.items],
        total=page.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/github",
    response_model=GitHubRegisteredApplicationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Register a GitHub Workflow application",
)
async def register_github_application(
    payload: GitHubRegisteredApplicationCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> GitHubRegisteredApplicationDetail:
    """Create a global GitHub Workflow template at version 1."""

    version = await crud_registered_application.create_github_registered_application(
        db,
        payload,
        created_by=current_user.identifier,
    )
    logger.info(
        "GitHub Workflow registration completed name=%s by=%s",
        payload.name,
        current_user.identifier,
    )
    return _github_registration_detail(version)


@router.post(
    "/container",
    response_model=ContainerRegisteredApplicationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Register a Containerized application",
)
async def register_container_application(
    payload: ContainerRegisteredApplicationCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ContainerRegisteredApplicationDetail:
    """Create a global Containerized application template at version 1."""

    version = await crud_registered_application.create_container_registered_application(
        db,
        payload,
        created_by=current_user.identifier,
    )
    logger.info(
        "Containerized application registration completed name=%s by=%s",
        payload.name,
        current_user.identifier,
    )
    return _container_registration_detail(version)


def _llm_model_out(model_name: str, *, pricing_available: bool) -> LLMModelOut:
    provider = resolve_llm_provider(model_name)
    return LLMModelOut(
        model_name=normalize_model_name(model_name),
        provider=provider,
        pricing_available=pricing_available,
        message=(
            None
            if pricing_available
            else pricing_unavailable_message(model_name, provider)
        ),
    )


@router.get(
    "/llm-models",
    response_model=LLMModelList,
    status_code=status.HTTP_200_OK,
    summary="List the LLM models Athena can price",
)
async def list_llm_models(
    _current_user: AthenaTokenUser = Depends(require_admin),
) -> LLMModelList:
    """
    Suggest model names for registration.

    These are the models Athena supports: the private in-cluster models and the
    public models the gateway routes. A registration using any other name is
    reported as unsupported.
    """

    names = [*PRIVATE_MODEL_NAMES, *PUBLIC_MODEL_NAMES]
    items = [
        _llm_model_out(name, pricing_available=True)
        for name in dict.fromkeys(names)
    ]
    return LLMModelList(items=items, total=len(items))


@router.get(
    "/llm-models/pricing",
    response_model=LLMModelOut,
    status_code=status.HTTP_200_OK,
    summary="Report whether a model name is priced",
)
async def get_llm_model_pricing(
    model_name: str = Query(..., min_length=1, max_length=255),
    _current_user: AthenaTokenUser = Depends(require_admin),
) -> LLMModelOut:
    """Resolve one supplied model name to its provider and pricing state."""

    normalized = normalize_model_name(model_name)
    # Private vLLM usage is billed from the GPU-hour rate rather than per
    # token, and a supported public model is costed from its published rate.
    return _llm_model_out(
        normalized,
        pricing_available=is_supported_model(normalized),
    )


@router.get(
    "/{application_id}/image-tags",
    response_model=ContainerImageTagList,
    status_code=status.HTTP_200_OK,
    summary="List image tags for a containerized application",
)
async def list_container_application_image_tags(
    application_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ContainerImageTagList:
    """Resolve the current registry configuration and return safe tag metadata."""

    version = (
        await crud_registered_application.get_current_registered_application_version(
            db,
            application_id,
        )
    )
    if version is None:
        raise RegisteredApplicationNotFound(application_id)
    if version.application.application_type != ApplicationType.CONTAINERIZED.value:
        raise ContainerImageTagsNotSupported()

    configuration = version.container_configuration
    if configuration is None:
        raise RuntimeError("Container application version has no container configuration")

    registry_runtime = None
    if configuration.registry_password_encrypted is not None:
        registry_runtime = (
            await crud_registered_application.resolve_container_registry_credentials(
                db,
                version.id,
            )
        )

    page = await _get_container_registry_client().list_tags(
        registry=configuration.registry,
        repository=configuration.image_repository,
        credential_id=configuration.registry_credential_id,
        limit=limit,
        username=registry_runtime.username if registry_runtime else None,
        password=registry_runtime.password if registry_runtime else None,
    )
    return ContainerImageTagList(
        application_id=application_id,
        registry=configuration.registry,
        repository=configuration.image_repository,
        default_tag=configuration.default_image_tag,
        items=[
            ContainerImageTagOut(
                tag=item.name,
                image_reference=build_image_reference(
                    configuration.registry,
                    configuration.image_repository,
                    item.name,
                ),
                digest=item.digest,
                last_updated=item.last_updated,
                is_default=item.name == configuration.default_image_tag,
            )
            for item in page.items
        ],
        total=page.total,
        limit=limit,
    )


@router.get(
    "/{application_id}/github-tags",
    response_model=GitHubRepositoryTagList,
    summary="List GitHub application version tags",
)
async def list_github_application_tags(
    application_id: UUID,
    limit: int = Query(default=200, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> GitHubRepositoryTagList:
    version = await crud_registered_application.get_current_registered_application_version(
        db, application_id
    )
    if version is None:
        raise RegisteredApplicationNotFound(application_id)
    if version.application.application_type != ApplicationType.GITHUB_WORKFLOW.value:
        raise UnprocessableEntityError("GitHub tags are only available for GitHub applications.")
    configuration = version.github_configuration
    if configuration is None:
        raise RuntimeError("GitHub application has no workflow configuration")
    repository = configuration.code_repository or configuration.repository
    runtime = await crud_github_connection.resolve_github_connection(
        db, configuration.github_connection_id
    )
    owner, name = repository.split("/", 1)
    try:
        client = GitHubActionsClient(
            repo_owner=owner,
            repo_name=name,
            github_token=runtime.token if runtime else None,
            api_base_url=runtime.api_base_url if runtime else "https://api.github.com",
        )
        branches = await client.list_repository_branches(limit=limit)
        tags = await client.list_repository_tags(limit=limit)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 404):
            raise UnprocessableEntityError(
                "Cannot read repository tags. Check the repository and the selected "
                "GitHub connection's access permissions."
            ) from exc
        raise ServiceUnavailableError("GitHub repository tags could not be loaded.") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise ServiceUnavailableError("GitHub repository tags could not be loaded.") from exc
    return GitHubRepositoryTagList(
        application_id=application_id,
        repository=repository,
        branches=branches,
        tags=tags,
        items=list(dict.fromkeys([*branches, *tags])),
    )


@router.get(
    "/{application_id}/secrets",
    response_model=ContainerApplicationSecretList,
    status_code=status.HTTP_200_OK,
    summary="List managed secrets for a containerized application",
)
async def list_container_application_secrets(
    application_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ContainerApplicationSecretList:
    """List safe secret metadata; values remain exclusively in the provider."""

    page = await crud_registered_application.list_container_application_secrets(
        db,
        application_id,
        offset=offset,
        limit=limit,
    )
    return ContainerApplicationSecretList(
        items=[_container_secret_out(secret) for secret in page.items],
        total=page.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/{application_id}/secrets",
    response_model=ContainerApplicationSecretOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a managed secret for a containerized application",
)
async def create_container_application_secret(
    application_id: UUID,
    payload: ContainerApplicationSecretCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ContainerApplicationSecretOut:
    """Write secret material to the provider and persist only its reference."""

    secret = await crud_registered_application.prepare_container_application_secret_create(
        db,
        application_id,
        payload,
        created_by=current_user.identifier,
    )
    try:
        provider = _get_container_secret_provider()
        reference = await provider.upsert_secret(
            secret_id=secret.id,
            application_id=application_id,
            name=secret.name,
            environment_variable=secret.environment_variable,
            secret_value=payload.secret_value.get_secret_value(),
        )
    except Exception:
        await db.rollback()
        raise
    secret = await crud_registered_application.complete_container_application_secret_write(
        db,
        secret,
        reference=reference,
        updated_by=current_user.identifier,
    )
    logger.info(
        "Container application secret created id=%s application=%s by=%s",
        secret.id,
        application_id,
        current_user.identifier,
    )
    return _container_secret_out(secret)


@router.patch(
    "/{application_id}/secrets/{secret_id}",
    response_model=ContainerApplicationSecretOut,
    status_code=status.HTTP_200_OK,
    summary="Update or rotate a managed container secret",
)
async def update_container_application_secret(
    application_id: UUID,
    secret_id: UUID,
    payload: ContainerApplicationSecretUpdate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ContainerApplicationSecretOut:
    """Update metadata and optionally replace the provider-held value."""

    secret = await crud_registered_application.prepare_container_application_secret_update(
        db,
        application_id,
        secret_id,
        payload,
        updated_by=current_user.identifier,
    )
    try:
        provider = _get_container_secret_provider()
        reference = await provider.upsert_secret(
            secret_id=secret.id,
            application_id=application_id,
            name=secret.name,
            environment_variable=secret.environment_variable,
            secret_value=(
                payload.secret_value.get_secret_value()
                if payload.secret_value is not None
                else None
            ),
        )
    except Exception:
        await db.rollback()
        raise
    secret = await crud_registered_application.complete_container_application_secret_write(
        db,
        secret,
        reference=reference,
        updated_by=current_user.identifier,
    )
    logger.info(
        "Container application secret updated id=%s application=%s by=%s",
        secret.id,
        application_id,
        current_user.identifier,
    )
    return _container_secret_out(secret)


@router.delete(
    "/{application_id}/secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a managed container secret",
)
async def delete_container_application_secret(
    application_id: UUID,
    secret_id: UUID,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> Response:
    """Delete provider material before soft-deleting Athena metadata."""

    secret = await crud_registered_application.prepare_container_application_secret_delete(
        db,
        application_id,
        secret_id,
    )
    if not secret.secret_reference:
        await db.rollback()
        raise ServiceUnavailableError("The managed secret has no provider reference.")
    try:
        provider = _get_container_secret_provider()
        await provider.delete_secret(
            secret_id=secret.id,
            reference=secret.secret_reference,
        )
    except Exception:
        await db.rollback()
        raise
    await crud_registered_application.complete_container_application_secret_delete(
        db,
        secret,
        deleted_by=current_user.identifier,
    )
    logger.info(
        "Container application secret deleted id=%s application=%s by=%s",
        secret.id,
        application_id,
        current_user.identifier,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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

    page = await crud_registered_application.list_registered_application_deployments(
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

    instance = await crud_registered_application.get_registered_application_deployment(
        db,
        deployment_id,
    )
    if instance is None:
        raise DeploymentInstanceNotFound(deployment_id)
    workflow_progress, workflow_progress_error = (
        await get_registered_deployment_workflow_progress(db, instance)
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
        await crud_registered_application.update_registered_application_deployment_progress(
            db,
            deployment_id,
            payload,
        )
    )
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
) -> RegisteredApplicationDeploymentDetail:
    """Return the instance to its previous version, or redeploy it.

    After an upgrade the instance keeps the configuration it ran before; a
    rollback dispatches the in-place upgrade pipeline back to that version.
    An instance that was never upgraded has nothing to return to, so its
    stored configuration is deployed again (a retry of a failed deployment).
    """

    instance = await crud_registered_application.prepare_registered_application_rollback(
        db,
        deployment_id,
        triggered_by=current_user.identifier,
    )
    application_type = instance.application.application_type
    restoring_version = instance.status == DeploymentInstanceStatus.UPDATING.value
    try:
        if restoring_version:
            target_version = configured_version(instance.configuration, application_type)
            dispatch_reference = await dispatch_registered_application_upgrade(
                instance.application_version,
                deployment_id=instance.id,
                instance_name=instance.instance_name,
                tier=instance.tier,
                target_version=target_version,
                configuration=instance.configuration,
                triggered_by=current_user.identifier,
                rollback=True,
            )
        else:
            dispatch_reference = await _dispatch_deployment(
                db,
                instance.application_version,
                deployment_id=instance.id,
                instance_name=instance.instance_name,
                tier=instance.tier,
                configuration=instance.configuration,
                triggered_by=current_user.identifier,
            )
    except Exception as error:
        await db.rollback()
        logger.exception("Deployment rollback dispatch failed id=%s", deployment_id)
        raise ServiceUnavailableError(
            "The rollback pipeline could not be triggered."
        ) from error

    if restoring_version:
        instance = await crud_registered_application.complete_registered_application_upgrade_dispatch(
            db,
            instance,
            dispatch_reference,
            event_message=(
                f"Rollback #{instance.rollback_count} pipeline dispatched for version "
                f"'{target_version}'."
            ),
        )
    else:
        instance = await crud_registered_application.complete_registered_application_dispatch(
            db,
            instance,
            dispatch_reference,
            event_message=f"Rollback #{instance.rollback_count} pipeline dispatched.",
        )
    logger.info(
        "Rollback #%s dispatched id=%s mode=%s by=%s",
        instance.rollback_count,
        instance.id,
        "version" if restoring_version else "redeploy",
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
) -> RegisteredApplicationDeploymentDetail:
    """Validate one version and dispatch the type-specific upgrade workflow."""

    instance = await crud_registered_application.get_registered_application_deployment(
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
            await crud_registered_application.resolve_container_registry_credentials(
                db,
                instance.application_version_id,
            )
        )
        await _get_container_registry_client().get_tag(
            registry=registry,
            repository=repository,
            tag=payload.version,
            credential_id=credential_id,
            username=registry_runtime.username if registry_runtime else None,
            password=registry_runtime.password if registry_runtime else None,
        )
    instance = await crud_registered_application.prepare_registered_application_upgrade(
        db,
        deployment_id,
        target_version=payload.version,
        triggered_by=current_user.identifier,
    )
    try:
        dispatch_reference = await dispatch_registered_application_upgrade(
            instance.application_version,
            deployment_id=instance.id,
            instance_name=instance.instance_name,
            tier=instance.tier,
            target_version=payload.version,
            configuration=instance.configuration,
            triggered_by=current_user.identifier,
        )
    except Exception as error:
        await db.rollback()
        logger.exception(
            "Registered deployment upgrade dispatch failed id=%s type=%s",
            deployment_id,
            application_type,
        )
        raise ServiceUnavailableError(
            "The upgrade pipeline could not be triggered."
        ) from error

    instance = (
        await crud_registered_application.complete_registered_application_upgrade_dispatch(
            db,
            instance,
            dispatch_reference,
        )
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
) -> RegisteredApplicationDeploymentDetail:
    """Confirm and dispatch the type-specific termination workflow."""

    instance = await crud_registered_application.get_registered_application_deployment(
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

    instance = (
        await crud_registered_application.prepare_registered_application_termination(
            db,
            deployment_id,
            triggered_by=current_user.identifier,
        )
    )
    try:
        dispatch_reference = await dispatch_registered_application_termination(
            instance.application_version,
            deployment_id=instance.id,
            instance_name=instance.instance_name,
            tier=instance.tier,
            configuration=instance.configuration,
            triggered_by=current_user.identifier,
        )
    except Exception as error:
        await db.rollback()
        logger.exception(
            "Registered deployment termination dispatch failed id=%s type=%s",
            deployment_id,
            application_type,
        )
        raise ServiceUnavailableError(
            "The termination pipeline could not be triggered."
        ) from error

    instance = (
        await crud_registered_application.complete_registered_application_termination_dispatch(
            db,
            instance,
            dispatch_reference,
        )
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
) -> RegisteredApplicationDeploymentOut:
    """Validate a tag and deploy the stored container template into the path Tier."""

    version = (
        await crud_registered_application.get_current_registered_application_version(
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
            await crud_registered_application.resolve_container_registry_credentials(
                db,
                version.id,
            )
        )
    await _get_container_registry_client().get_tag(
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
        managed_secrets=await crud_registered_application.list_active_container_application_secrets(
            db, application_id
        ),
    )
    instance = await crud_registered_application.create_registered_application_deployment(
        db,
        version,
        instance_name=payload.instance_name,
        tier=tier,
        configuration=configuration,
        triggered_by=current_user.identifier,
    )
    try:
        dispatch_reference = await _dispatch_deployment(
            db,
            version,
            deployment_id=instance.id,
            instance_name=instance.instance_name,
            tier=instance.tier,
            configuration=configuration,
            triggered_by=current_user.identifier,
        )
    except Exception as error:
        await db.rollback()
        logger.exception(
            "Container deployment dispatch failed application=%s tier=%s",
            application_id,
            tier,
        )
        raise ServiceUnavailableError(
            "The deployment pipeline could not be triggered."
        ) from error

    instance = await crud_registered_application.complete_registered_application_dispatch(
        db,
        instance,
        dispatch_reference,
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
) -> RegisteredApplicationDeploymentOut:
    """Validate, persist, and dispatch a deployment from the current template version."""

    version = (
        await crud_registered_application.get_current_registered_application_version(
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
    instance = await crud_registered_application.create_registered_application_deployment(
        db,
        version,
        instance_name=instance_name,
        tier=payload.tier,
        configuration=configuration,
        triggered_by=current_user.identifier,
    )
    try:
        dispatch_reference = await _dispatch_deployment(
            db,
            version,
            deployment_id=instance.id,
            instance_name=instance.instance_name,
            tier=instance.tier,
            configuration=configuration,
            triggered_by=current_user.identifier,
        )
    except Exception as error:
        await db.rollback()
        logger.exception(
            "Registered application deployment dispatch failed application=%s tier=%s",
            application_id,
            payload.tier,
        )
        raise ServiceUnavailableError(
            "The deployment pipeline could not be triggered."
        ) from error

    instance = await crud_registered_application.complete_registered_application_dispatch(
        db,
        instance,
        dispatch_reference,
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


@router.get(
    "/{application_id}",
    response_model=RegisteredApplicationDetail,
    status_code=status.HTTP_200_OK,
    summary="Get a registered application",
)
async def get_registered_application(
    application_id: UUID,
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationDetail:
    """Return the current deployable version of a registered application."""

    version = (
        await crud_registered_application.get_current_registered_application_version(
            db,
            application_id,
        )
    )
    if version is None:
        raise RegisteredApplicationNotFound(application_id)
    if version.application.application_type == ApplicationType.GITHUB_WORKFLOW.value:
        return _github_registration_detail(version)
    if version.application.application_type == ApplicationType.CONTAINERIZED.value:
        return _container_registration_detail(version)
    raise RuntimeError(
        f"Unsupported registered application type: {version.application.application_type}"
    )


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a registered application",
)
async def delete_registered_application(
    application_id: UUID,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> Response:
    """Delete a template only when it has no active deployment instances."""

    await crud_registered_application.delete_registered_application(
        db,
        application_id,
        deleted_by=current_user.identifier,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

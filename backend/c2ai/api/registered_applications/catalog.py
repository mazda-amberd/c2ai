"""Registering, listing, reading and deleting application templates."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.api.registered_applications import clients
from c2ai.api.registered_applications.clients import PREFIX, TAGS
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.container_registry import (
    build_image_reference,
)
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.constants.registered_application import (
    ApplicationStatus,
    ApplicationType,
)
from c2ai.core.exceptions import (
    ContainerImageTagsNotSupported,
    RegisteredApplicationNotFound,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from c2ai.crud import (
    github_connection as crud_github_connection,
)
from c2ai.db.session import get_db_session as db_session
from c2ai.models.registered_application import RegisteredApplicationVersion
from c2ai.registration import credentials, repository as applications
from c2ai.schemas.registered_application import (
    ContainerConfigurationOut,
    ContainerImageTagList,
    ContainerImageTagOut,
    ContainerRegisteredApplicationCreate,
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
    RegisteredApplicationDetail,
    RegisteredContainerParameterValue,
    RegisteredParameterDefinition,
    TierDeploymentSummary,
)
from c2ai.services.llm_models import (
    PRIVATE_MODEL_NAMES,
    PUBLIC_MODEL_NAMES,
    is_supported_model,
    normalize_model_name,
    pricing_unavailable_message,
    resolve_llm_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix=PREFIX, tags=TAGS)


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
    record: applications.RegisteredApplicationCatalogRecord,
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
    sort_by: Literal["name", "type", "instances", "tiers", "created", "created_at"] = Query(
        default="name"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> RegisteredApplicationCatalogResponse:
    """Search, filter, sort, and paginate the global application catalog."""

    page = await applications.list_registered_applications(
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

    version = await applications.create_github_registered_application(
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

    version = await applications.create_container_registered_application(
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
        await applications.get_current_registered_application_version(
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
            await credentials.resolve_container_registry_credentials(
                db,
                version.id,
            )
        )

    page = await clients.container_registry_client().list_tags(
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
    version = await applications.get_current_registered_application_version(
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
        await applications.get_current_registered_application_version(
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

    await applications.delete_registered_application(
        db,
        application_id,
        deleted_by=current_user.identifier,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

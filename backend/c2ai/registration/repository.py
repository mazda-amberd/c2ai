"""The registered-application catalog: registration, edits, versions, listing, deletion.

Functions here only flush; the route commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload, selectinload

from c2ai.constants.registered_application import (
    ACTIVE_DEPLOYMENT_INSTANCE_STATUSES,
    ApplicationStatus,
    ApplicationType,
    ParameterType,
)
from c2ai.core.exceptions import (
    DuplicateRegisteredApplication,
    RegisteredApplicationHasManagedSecrets,
    RegisteredApplicationHasRunningInstances,
    RegisteredApplicationNotFound,
    UnprocessableEntityError,
)
from c2ai.db.errors import violated_constraint
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ApplicationSecretReference,
    ContainerApplicationConfiguration,
    ContainerApplicationSecret,
    DeploymentInstance,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.registered_application import (
    ContainerRegisteredApplicationCreate,
    ContainerRegisteredApplicationUpdate,
    GitHubRegisteredApplicationCreate,
    GitHubRegisteredApplicationUpdate,
    LLMConfigurationCreate,
)
from c2ai.security import crypto

logger = logging.getLogger(__name__)


_APPLICATION_NAME_CONSTRAINT = "uq_registered_applications_name_ci"


@dataclass(frozen=True)
class RegisteredApplicationCatalogRecord:
    """Database result used to build one catalog response item."""

    application: RegisteredApplication
    description: str | None
    total_deployed_instances: int
    tiers_deployed_to: dict[int, int]
    managed_secret_count: int = 0

    @property
    def can_delete(self) -> bool:
        """Deletion needs no active instances and no provider-held secrets."""

        return self.total_deployed_instances == 0 and self.managed_secret_count == 0

    @property
    def can_edit(self) -> bool:
        """An edit waits until nothing of the template is deployed."""

        return self.total_deployed_instances == 0


@dataclass(frozen=True)
class RegisteredApplicationCatalogPage:
    """Paginated catalog query result."""

    items: list[RegisteredApplicationCatalogRecord]
    total: int


async def get_registered_application_by_name(
    db: AsyncSession,
    name: str,
) -> RegisteredApplication | None:
    """Return an application by name using catalog uniqueness semantics."""

    # Soft-deleted templates leave the catalog, so they release their name too.
    result = await db.execute(
        select(RegisteredApplication).where(
            func.lower(RegisteredApplication.name) == name.lower(),
            RegisteredApplication.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _persist_new_application(
    db: AsyncSession,
    application: RegisteredApplication,
) -> None:
    """Flush a complete registration graph (the caller commits)."""

    db.add(application)
    try:
        await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == _APPLICATION_NAME_CONSTRAINT:
            raise DuplicateRegisteredApplication(application.name) from error
        raise


def _sealed(secret: SecretStr | None, *, column: str, kept: bytes | None) -> bytes | None:
    """Encrypt a newly supplied secret; without one, the stored one carries over."""

    if secret is None:
        return kept
    return crypto.encrypt(secret.get_secret_value(), column=column)


def _llm_configuration(
    llm: LLMConfigurationCreate,
    *,
    kept: ApplicationLLMConfiguration | None,
) -> ApplicationLLMConfiguration:
    token = _sealed(
        llm.api_token,
        column=crypto.LLM_API_TOKEN,
        kept=kept.api_token_encrypted if kept else None,
    )
    if token is None:
        raise UnprocessableEntityError(
            "Enter the LLM API token: none is stored for this application yet.",
            code="LLMApiTokenRequired",
        )
    return ApplicationLLMConfiguration(
        endpoint=llm.endpoint,
        api_token_encrypted=token,
        model_name=llm.model_name,
    )


def _github_version(
    payload: GitHubRegisteredApplicationCreate,
    *,
    number: int,
    created_by: str,
    previous: RegisteredApplicationVersion | None = None,
) -> RegisteredApplicationVersion:
    """A GitHub Workflow version graph; ``previous`` supplies secrets left out of an edit."""

    application_version = RegisteredApplicationVersion(
        version=number,
        description=payload.description,
        created_by=created_by,
    )
    application_version.github_configuration = GitHubApplicationConfiguration(
        github_connection_id=payload.github.github_connection_id,
        trigger_method=payload.github.trigger_method.value,
        repository=payload.github.repository,
        code_repository=payload.github.code_repository,
        workflow_file_path=payload.github.workflow_file_path,
        ref=payload.github.ref,
    )
    application_version.llm_configuration = _llm_configuration(
        payload.llm, kept=previous.llm_configuration if previous else None
    )
    application_version.parameters = [
        ApplicationParameterDefinition(
            position=position,
            label=parameter.key,
            key=parameter.key,
            parameter_type=parameter.parameter_type.value,
            required=True,
            default_value=None,
            options=[],
        )
        for position, parameter in enumerate(payload.parameters)
    ]
    return application_version


def _container_version(
    payload: ContainerRegisteredApplicationCreate,
    *,
    number: int,
    created_by: str,
    previous: RegisteredApplicationVersion | None = None,
) -> RegisteredApplicationVersion:
    """A Containerized version graph; ``previous`` supplies secrets left out of an edit."""

    kept = previous.container_configuration if previous else None
    application_version = RegisteredApplicationVersion(
        version=number,
        description=payload.description,
        created_by=created_by,
    )
    application_version.container_configuration = ContainerApplicationConfiguration(
        registry=payload.container.registry,
        registry_credential_id=kept.registry_credential_id if kept else None,
        registry_username=payload.container.registry_username,
        registry_password_encrypted=_sealed(
            payload.container.registry_password,
            column=crypto.REGISTRY_PASSWORD,
            kept=kept.registry_password_encrypted if kept else None,
        ),
        image_repository=payload.container.image_registry,
        default_image_tag=payload.container.tag,
        image_pull_policy=payload.container.pull_policy.value,
        container_port=payload.container.port,
        expose_public_service=payload.container.expose_public_service,
        gpu_request=payload.container.gpu_request,
        cpu_request=payload.container.cpu_request,
        memory_request=payload.container.memory_request,
        scaling=payload.container.scaling,
        storage=payload.container.storage,
        environment_variables=list(kept.environment_variables or []) if kept else [],
    )
    application_version.llm_configuration = _llm_configuration(
        payload.llm, kept=previous.llm_configuration if previous else None
    )
    # Container environment values are fixed at registration, so each key is
    # stored with the value every deployment of this template will use.
    application_version.parameters = [
        ApplicationParameterDefinition(
            position=position,
            label=parameter.key,
            key=parameter.key,
            parameter_type=ParameterType.TEXT.value,
            required=True,
            default_value=parameter.value,
            options=[],
        )
        for position, parameter in enumerate(payload.parameters)
    ]
    return application_version


async def create_github_registered_application(
    db: AsyncSession,
    payload: GitHubRegisteredApplicationCreate,
    *,
    created_by: str,
) -> RegisteredApplicationVersion:
    """Persist a GitHub Workflow template and immutable version 1 atomically."""

    if await get_registered_application_by_name(db, payload.name):
        raise DuplicateRegisteredApplication(payload.name)

    application = RegisteredApplication(
        name=payload.name,
        application_type=ApplicationType.GITHUB_WORKFLOW.value,
        status=ApplicationStatus.ACTIVE.value,
        current_version=1,
        created_by=created_by,
    )
    application_version = _github_version(payload, number=1, created_by=created_by)
    application.versions.append(application_version)

    await _persist_new_application(db, application)

    logger.info(
        "Registered GitHub Workflow application id=%s name=%s by=%s",
        application.id,
        application.name,
        created_by,
    )
    return application_version


async def create_container_registered_application(
    db: AsyncSession,
    payload: ContainerRegisteredApplicationCreate,
    *,
    created_by: str,
) -> RegisteredApplicationVersion:
    """Persist a Containerized template and immutable version 1 atomically."""

    if await get_registered_application_by_name(db, payload.name):
        raise DuplicateRegisteredApplication(payload.name)

    application = RegisteredApplication(
        name=payload.name,
        application_type=ApplicationType.CONTAINERIZED.value,
        status=ApplicationStatus.ACTIVE.value,
        current_version=1,
        created_by=created_by,
    )
    application_version = _container_version(payload, number=1, created_by=created_by)
    application.versions.append(application_version)

    await _persist_new_application(db, application)

    logger.info(
        "Registered Containerized application id=%s name=%s by=%s",
        application.id,
        application.name,
        created_by,
    )
    return application_version


async def _active_instances(db: AsyncSession, application_id: UUID) -> list[tuple[str, int]]:
    """(name, tier) of every instance of the template that is not finished."""

    result = await db.execute(
        select(DeploymentInstance.instance_name, DeploymentInstance.tier)
        .where(
            DeploymentInstance.application_id == application_id,
            DeploymentInstance.status.in_(ACTIVE_DEPLOYMENT_INSTANCE_STATUSES),
        )
        .order_by(DeploymentInstance.tier.asc(), DeploymentInstance.instance_name.asc())
    )
    return [(str(name), int(tier)) for name, tier in result.all()]


async def _locked_application(db: AsyncSession, application_id: UUID) -> RegisteredApplication:
    result = await db.execute(
        select(RegisteredApplication)
        .where(
            RegisteredApplication.id == application_id,
            RegisteredApplication.deleted_at.is_(None),
        )
        .with_for_update()
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise RegisteredApplicationNotFound(application_id)
    return application


async def update_registered_application(
    db: AsyncSession,
    application_id: UUID,
    payload: GitHubRegisteredApplicationUpdate | ContainerRegisteredApplicationUpdate,
    *,
    updated_by: str,
) -> RegisteredApplicationVersion:
    """Save an edit as the template's next version, while nothing of it is deployed.

    Deployments point at the version they ran, so an edit never rewrites one:
    it adds the next, and a finished instance still shows what it ran. A
    secret left out of the edit carries over from the current version.
    """

    application = await _locked_application(db, application_id)
    if payload.application_type.value != application.application_type:
        raise UnprocessableEntityError(
            "An application's type cannot be changed. Register a new application instead.",
            code="RegisteredApplicationTypeChange",
        )
    remaining = await _active_instances(db, application_id)
    if remaining:
        raise RegisteredApplicationHasRunningInstances(
            application.name, remaining, action="edited"
        )
    if payload.name.lower() != application.name.lower() and await get_registered_application_by_name(
        db, payload.name
    ):
        raise DuplicateRegisteredApplication(payload.name)

    previous = await get_current_registered_application_version(db, application_id)
    if previous is None:
        raise RegisteredApplicationNotFound(application_id)
    number = application.current_version + 1
    if isinstance(payload, GitHubRegisteredApplicationUpdate):
        version = _github_version(payload, number=number, created_by=updated_by, previous=previous)
    else:
        version = _container_version(payload, number=number, created_by=updated_by, previous=previous)
    version.secret_references = [
        ApplicationSecretReference(
            position=reference.position,
            label=reference.label,
            key=reference.key,
            required=reference.required,
            secret_reference=reference.secret_reference,
        )
        for reference in previous.secret_references
    ]
    # Set from this side: application.versions is not loaded, and loading it
    # just to append would read every version.
    version.application = application
    db.add(version)
    application.name = payload.name
    application.current_version = number
    application.updated_by = updated_by
    try:
        await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == _APPLICATION_NAME_CONSTRAINT:
            raise DuplicateRegisteredApplication(payload.name) from error
        raise

    logger.info(
        "Edited registered application id=%s name=%s version=%s by=%s",
        application.id,
        application.name,
        number,
        updated_by,
    )
    return version


def _catalog_filters(
    *,
    search: str | None,
    application_type: str | None,
    application_status: str | None,
    tier: int | None,
) -> list:
    """Build filters shared by the catalog count and data queries."""

    filters = [RegisteredApplication.deleted_at.is_(None)]
    if search:
        escaped = (
            search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                RegisteredApplication.name.ilike(pattern, escape="\\"),
                RegisteredApplicationVersion.description.ilike(pattern, escape="\\"),
            )
        )
    if application_type:
        filters.append(RegisteredApplication.application_type == application_type)
    if application_status:
        filters.append(RegisteredApplication.status == application_status)
    if tier is not None:
        tier_instance = aliased(DeploymentInstance)
        filters.append(
            exists(
                select(1).where(
                    tier_instance.application_id == RegisteredApplication.id,
                    tier_instance.tier == tier,
                    tier_instance.status.in_(ACTIVE_DEPLOYMENT_INSTANCE_STATUSES),
                )
            )
        )
    return filters


async def list_registered_applications(
    db: AsyncSession,
    *,
    search: str | None = None,
    application_type: str | None = None,
    application_status: str | None = None,
    tier: int | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
    offset: int = 0,
    limit: int = 50,
) -> RegisteredApplicationCatalogPage:
    """Return a filtered catalog page with active deployment aggregates."""

    current_version_join = and_(
        RegisteredApplicationVersion.application_id == RegisteredApplication.id,
        RegisteredApplicationVersion.version == RegisteredApplication.current_version,
    )
    active_deployment_join = and_(
        DeploymentInstance.application_id == RegisteredApplication.id,
        DeploymentInstance.status.in_(ACTIVE_DEPLOYMENT_INSTANCE_STATUSES),
    )
    filters = _catalog_filters(
        search=search,
        application_type=application_type,
        application_status=application_status,
        tier=tier,
    )

    count_statement = (
        select(func.count(RegisteredApplication.id))
        .select_from(RegisteredApplication)
        .join(RegisteredApplicationVersion, current_version_join)
        .where(*filters)
    )
    count_result = await db.execute(count_statement)
    total = int(count_result.scalar_one())

    instance_count = func.count(DeploymentInstance.id)
    tier_count = func.count(func.distinct(DeploymentInstance.tier))
    statement = (
        select(
            RegisteredApplication,
            RegisteredApplicationVersion.description,
            instance_count.label("instance_count"),
            tier_count.label("tier_count"),
        )
        .join(RegisteredApplicationVersion, current_version_join)
        .outerjoin(DeploymentInstance, active_deployment_join)
        .where(*filters)
        .group_by(RegisteredApplication.id, RegisteredApplicationVersion.id)
    )

    sort_expressions = {
        "name": func.lower(RegisteredApplication.name),
        "type": RegisteredApplication.application_type,
        "instances": instance_count,
        "tiers": tier_count,
        "created": RegisteredApplication.created_at,
        "created_at": RegisteredApplication.created_at,
    }
    sort_expression = sort_expressions.get(sort_by, sort_expressions["name"])
    ordered = sort_expression.desc() if sort_order == "desc" else sort_expression.asc()
    statement = statement.order_by(
        ordered,
        func.lower(RegisteredApplication.name).asc(),
        RegisteredApplication.id.asc(),
    ).offset(offset).limit(limit)

    result = await db.execute(statement)
    rows = list(result.all())
    if not rows:
        return RegisteredApplicationCatalogPage(items=[], total=total)

    application_ids = [row[0].id for row in rows]
    tier_statement = (
        select(
            DeploymentInstance.application_id,
            DeploymentInstance.tier,
            func.count(DeploymentInstance.id),
        )
        .where(
            DeploymentInstance.application_id.in_(application_ids),
            DeploymentInstance.status.in_(ACTIVE_DEPLOYMENT_INSTANCE_STATUSES),
        )
        .group_by(DeploymentInstance.application_id, DeploymentInstance.tier)
    )
    tier_result = await db.execute(tier_statement)
    tiers_by_application: dict[UUID, dict[int, int]] = {
        application_id: {} for application_id in application_ids
    }
    for application_id, deployment_tier, deployment_count in tier_result.all():
        tiers_by_application[application_id][int(deployment_tier)] = int(
            deployment_count
        )

    secret_result = await db.execute(
        select(
            ContainerApplicationSecret.application_id,
            func.count(ContainerApplicationSecret.id),
        )
        .where(
            ContainerApplicationSecret.application_id.in_(application_ids),
            ContainerApplicationSecret.deleted_at.is_(None),
        )
        .group_by(ContainerApplicationSecret.application_id)
    )
    secrets_by_application = {
        application_id: int(count) for application_id, count in secret_result.all()
    }

    records = [
        RegisteredApplicationCatalogRecord(
            application=application,
            description=description,
            total_deployed_instances=int(deployment_count),
            tiers_deployed_to=tiers_by_application[application.id],
            managed_secret_count=secrets_by_application.get(application.id, 0),
        )
        for application, description, deployment_count, _ in rows
    ]
    return RegisteredApplicationCatalogPage(items=records, total=total)


async def get_current_registered_application_version(
    db: AsyncSession,
    application_id: UUID,
) -> RegisteredApplicationVersion | None:
    """Load the current version graph for a non-deleted application."""

    statement = (
        select(RegisteredApplicationVersion)
        .join(RegisteredApplication)
        .where(
            RegisteredApplication.id == application_id,
            RegisteredApplication.deleted_at.is_(None),
            RegisteredApplicationVersion.version
            == RegisteredApplication.current_version,
        )
        .options(
            joinedload(RegisteredApplicationVersion.application),
            joinedload(RegisteredApplicationVersion.github_configuration),
            joinedload(RegisteredApplicationVersion.container_configuration),
            joinedload(RegisteredApplicationVersion.llm_configuration),
            selectinload(RegisteredApplicationVersion.parameters),
            selectinload(RegisteredApplicationVersion.secret_references),
        )
    )
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def delete_registered_application(
    db: AsyncSession,
    application_id: UUID,
    *,
    deleted_by: str,
) -> None:
    """Soft-delete a template after locking it and checking active instances."""

    application = await _locked_application(db, application_id)
    remaining_instances = await _active_instances(db, application_id)
    if remaining_instances:
        raise RegisteredApplicationHasRunningInstances(
            application.name,
            remaining_instances,
        )

    secret_result = await db.execute(
        select(func.count(ContainerApplicationSecret.id)).where(
            ContainerApplicationSecret.application_id == application_id,
            ContainerApplicationSecret.deleted_at.is_(None),
        )
    )
    managed_secret_count = int(secret_result.scalar_one())
    if managed_secret_count:
        raise RegisteredApplicationHasManagedSecrets(
            application.name,
            managed_secret_count,
        )

    application.deleted_at = datetime.now(tz=UTC)
    application.updated_by = deleted_by
    db.add(application)
    await db.flush()

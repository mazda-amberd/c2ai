"""The registered-application catalog: registration, versions, listing, deletion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
)
from c2ai.db.errors import violated_constraint
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ContainerApplicationConfiguration,
    ContainerApplicationSecret,
    DeploymentInstance,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.registered_application import (
    ContainerRegisteredApplicationCreate,
    GitHubRegisteredApplicationCreate,
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
    """Flush and commit a complete registration graph in one transaction."""

    db.add(application)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if violated_constraint(error) == _APPLICATION_NAME_CONSTRAINT:
            raise DuplicateRegisteredApplication(application.name) from error
        raise
    except SQLAlchemyError:
        await db.rollback()
        raise


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
    application_version = RegisteredApplicationVersion(
        version=1,
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
    application_version.llm_configuration = ApplicationLLMConfiguration(
        endpoint=payload.llm.endpoint,
        api_token_encrypted=crypto.encrypt(
            payload.llm.api_token.get_secret_value(), column=crypto.LLM_API_TOKEN
        ),
        model_name=payload.llm.model_name,
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
    application_version = RegisteredApplicationVersion(
        version=1,
        description=payload.description,
        created_by=created_by,
    )
    application_version.container_configuration = ContainerApplicationConfiguration(
        registry=payload.container.registry,
        registry_credential_id=None,
        registry_username=payload.container.registry_username,
        registry_password_encrypted=crypto.encrypt(
            payload.container.registry_password.get_secret_value(),
            column=crypto.REGISTRY_PASSWORD,
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
        environment_variables=[],
    )
    application_version.llm_configuration = ApplicationLLMConfiguration(
        endpoint=payload.llm.endpoint,
        api_token_encrypted=crypto.encrypt(
            payload.llm.api_token.get_secret_value(), column=crypto.LLM_API_TOKEN
        ),
        model_name=payload.llm.model_name,
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
    application.versions.append(application_version)

    await _persist_new_application(db, application)

    logger.info(
        "Registered Containerized application id=%s name=%s by=%s",
        application.id,
        application.name,
        created_by,
    )
    return application_version


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

    application_result = await db.execute(
        select(RegisteredApplication)
        .where(
            RegisteredApplication.id == application_id,
            RegisteredApplication.deleted_at.is_(None),
        )
        .with_for_update()
    )
    application = application_result.scalar_one_or_none()
    if application is None:
        await db.rollback()
        raise RegisteredApplicationNotFound(application_id)

    instance_result = await db.execute(
        select(DeploymentInstance.instance_name, DeploymentInstance.tier)
        .where(
            DeploymentInstance.application_id == application_id,
            DeploymentInstance.status.in_(ACTIVE_DEPLOYMENT_INSTANCE_STATUSES),
        )
        .order_by(DeploymentInstance.tier.asc(), DeploymentInstance.instance_name.asc())
    )
    remaining_instances = [(str(name), int(tier)) for name, tier in instance_result.all()]
    if remaining_instances:
        await db.rollback()
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
        await db.rollback()
        raise RegisteredApplicationHasManagedSecrets(
            application.name,
            managed_secret_count,
        )

    application.deleted_at = datetime.now(tz=UTC)
    application.updated_by = deleted_by
    db.add(application)
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise

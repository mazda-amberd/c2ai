# pylint: disable=import-error
"""Persistence operations for reusable registered applications."""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload, selectinload

from c2ai.constants.registered_application import (
    ACTIVE_DEPLOYMENT_INSTANCE_STATUSES,
    DEPLOYMENT_STEP_ORDER,
    ApplicationStatus,
    ApplicationType,
    DeploymentInstanceStatus,
    DeploymentStep,
    ParameterType,
)
from c2ai.core.exceptions import (
    ContainerApplicationSecretInUse,
    ContainerApplicationSecretNotFound,
    ContainerSecretsNotSupported,
    DeploymentAlreadyAtVersion,
    DeploymentInstanceNotFound,
    DeploymentProgressConflict,
    DeploymentRollbackNotAvailable,
    DeploymentTerminationNotAvailable,
    DeploymentTerminationNotSupported,
    DeploymentUpgradeNotAvailable,
    DeploymentUpgradeNotSupported,
    DuplicateContainerApplicationSecret,
    DuplicateDeploymentInstance,
    DuplicateDeploymentSubdomain,
    DuplicateRegisteredApplication,
    RegisteredApplicationHasManagedSecrets,
    RegisteredApplicationHasRunningInstances,
    RegisteredApplicationNotFound,
    ServiceUnavailableError,
)
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ContainerApplicationConfiguration,
    ContainerApplicationSecret,
    DeploymentInstance,
    DeploymentInstanceEvent,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerApplicationSecretUpdate,
    ContainerRegisteredApplicationCreate,
    GitHubRegisteredApplicationCreate,
    RegisteredApplicationDeploymentProgressUpdate,
)
from c2ai.services.registered_application_deployment import configured_version

logger = logging.getLogger(__name__)

_APPLICATION_NAME_CONSTRAINT = "uq_registered_applications_name_ci"
_DEPLOYMENT_SUBDOMAIN_CONSTRAINT = "uq_deployment_instances_dns_subdomain_active"
_DEPLOYMENT_NAME_CONSTRAINT = "uq_deployment_instances_tier_instance_name"
_CONTAINER_SECRET_CONSTRAINTS = {
    "uq_container_application_secrets_name_active",
    "uq_container_application_secrets_env_active",
}


def _credential_encryption_key() -> str:
    key = os.getenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ServiceUnavailableError(
            "Credential storage is not configured. Set "
            "ATHENA_CREDENTIAL_ENCRYPTION_KEY."
        )
    return key


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


@dataclass(frozen=True)
class RegisteredApplicationDeploymentPage:
    """Paginated deployment history query result."""

    items: list[DeploymentInstance]
    total: int


@dataclass(frozen=True)
class ContainerApplicationSecretPage:
    """Paginated active managed secrets for one container application."""

    items: list[ContainerApplicationSecret]
    total: int


@dataclass(frozen=True)
class ContainerRegistryRuntime:
    """Decrypted registry values that must never be serialized to API clients."""

    username: str
    password: str


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


def _violated_constraint(error: IntegrityError) -> str | None:
    """Extract a PostgreSQL constraint name without depending on psycopg internals."""

    original = getattr(error, "orig", None)
    candidates = (
        original,
        getattr(original, "orig", None),
        getattr(original, "__cause__", None),
    )
    for candidate in candidates:
        constraint_name = getattr(candidate, "constraint_name", None)
        if constraint_name:
            return constraint_name
        diagnostic = getattr(candidate, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name:
            return constraint_name
    return None


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
        if _violated_constraint(error) == _APPLICATION_NAME_CONSTRAINT:
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
        api_token_encrypted=func.pgp_sym_encrypt(
            payload.llm.api_token.get_secret_value(),
            _credential_encryption_key(),
            "cipher-algo=aes256",
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
        registry_password_encrypted=func.pgp_sym_encrypt(
            payload.container.registry_password.get_secret_value(),
            _credential_encryption_key(),
            "cipher-algo=aes256",
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
        api_token_encrypted=func.pgp_sym_encrypt(
            payload.llm.api_token.get_secret_value(),
            _credential_encryption_key(),
            "cipher-algo=aes256",
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


async def resolve_container_registry_credentials(
    db: AsyncSession,
    application_version_id: UUID,
) -> ContainerRegistryRuntime | None:
    """Decrypt a managed registry credential for an outbound registry call."""

    result = await db.execute(
        select(
            ContainerApplicationConfiguration.registry_username,
            func.pgp_sym_decrypt(
                ContainerApplicationConfiguration.registry_password_encrypted,
                _credential_encryption_key(),
            ),
        ).where(
            ContainerApplicationConfiguration.application_version_id
            == application_version_id,
            ContainerApplicationConfiguration.registry_password_encrypted.is_not(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    username, password = row
    if (
        not isinstance(username, str)
        or not username
        or not isinstance(password, str)
        or not password
    ):
        raise ServiceUnavailableError("The container registry credential is invalid.")
    return ContainerRegistryRuntime(username=username, password=password)


async def resolve_llm_api_token(
    db: AsyncSession,
    application_version_id: UUID,
) -> str | None:
    """Decrypt the stored LLM token for one outbound pipeline dispatch."""

    result = await db.execute(
        select(
            func.pgp_sym_decrypt(
                ApplicationLLMConfiguration.api_token_encrypted,
                _credential_encryption_key(),
            )
        ).where(
            ApplicationLLMConfiguration.application_version_id == application_version_id,
            ApplicationLLMConfiguration.api_token_encrypted.is_not(None),
        )
    )
    token = result.scalar_one_or_none()
    if not isinstance(token, str) or not token:
        return None
    return token


async def _require_container_application(
    db: AsyncSession,
    application_id: UUID,
) -> RegisteredApplication:
    """Load a non-deleted application and enforce the managed-secret boundary."""

    result = await db.execute(
        select(RegisteredApplication).where(
            RegisteredApplication.id == application_id,
            RegisteredApplication.deleted_at.is_(None),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise RegisteredApplicationNotFound(application_id)
    if application.application_type != ApplicationType.CONTAINERIZED.value:
        raise ContainerSecretsNotSupported()
    return application


async def prepare_container_application_secret_create(
    db: AsyncSession,
    application_id: UUID,
    payload: ContainerApplicationSecretCreate,
    *,
    created_by: str,
) -> ContainerApplicationSecret:
    """Validate and flush secret metadata before writing external material."""

    await _require_container_application(db, application_id)
    secret = ContainerApplicationSecret(
        application_id=application_id,
        name=payload.name,
        environment_variable=payload.environment_variable,
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(secret)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        if _violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
            raise DuplicateContainerApplicationSecret() from error
        raise
    return secret


async def complete_container_application_secret_write(
    db: AsyncSession,
    secret: ContainerApplicationSecret,
    *,
    reference: str,
    updated_by: str,
) -> ContainerApplicationSecret:
    """Persist an opaque provider reference after a successful broker write."""

    secret.secret_reference = reference
    secret.updated_by = updated_by
    db.add(secret)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if _violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
            raise DuplicateContainerApplicationSecret() from error
        raise
    except SQLAlchemyError:
        await db.rollback()
        raise
    return secret


async def list_container_application_secrets(
    db: AsyncSession,
    application_id: UUID,
    *,
    offset: int = 0,
    limit: int = 50,
) -> ContainerApplicationSecretPage:
    """Return active secret metadata without retrieving secret material."""

    await _require_container_application(db, application_id)
    filters = (
        ContainerApplicationSecret.application_id == application_id,
        ContainerApplicationSecret.deleted_at.is_(None),
    )
    count_result = await db.execute(
        select(func.count(ContainerApplicationSecret.id)).where(*filters)
    )
    result = await db.execute(
        select(ContainerApplicationSecret)
        .where(*filters)
        .order_by(
            ContainerApplicationSecret.created_at.asc(),
            ContainerApplicationSecret.id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return ContainerApplicationSecretPage(
        items=list(result.scalars().all()),
        total=int(count_result.scalar_one()),
    )


async def get_container_application_secret(
    db: AsyncSession,
    application_id: UUID,
    secret_id: UUID,
    *,
    for_update: bool = False,
) -> ContainerApplicationSecret:
    """Load one active managed secret after enforcing container-only access."""

    await _require_container_application(db, application_id)
    statement = select(ContainerApplicationSecret).where(
        ContainerApplicationSecret.id == secret_id,
        ContainerApplicationSecret.application_id == application_id,
        ContainerApplicationSecret.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    secret = result.scalar_one_or_none()
    if secret is None:
        raise ContainerApplicationSecretNotFound(secret_id)
    return secret


async def prepare_container_application_secret_update(
    db: AsyncSession,
    application_id: UUID,
    secret_id: UUID,
    payload: ContainerApplicationSecretUpdate,
    *,
    updated_by: str,
) -> ContainerApplicationSecret:
    """Lock and flush managed-secret metadata before the provider update."""

    secret = await get_container_application_secret(
        db,
        application_id,
        secret_id,
        for_update=True,
    )
    if payload.name is not None:
        secret.name = payload.name
    if payload.environment_variable is not None:
        secret.environment_variable = payload.environment_variable
    secret.updated_by = updated_by
    db.add(secret)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        if _violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
            raise DuplicateContainerApplicationSecret() from error
        raise
    return secret


async def prepare_container_application_secret_delete(
    db: AsyncSession,
    application_id: UUID,
    secret_id: UUID,
) -> ContainerApplicationSecret:
    """Lock a managed secret before deleting its external material."""

    secret = await get_container_application_secret(
        db,
        application_id,
        secret_id,
        for_update=True,
    )
    reference_result = await db.execute(
        select(func.count(DeploymentInstance.id)).where(
            DeploymentInstance.application_id == application_id,
            DeploymentInstance.status.notin_(("terminated", "cancelled")),
            DeploymentInstance.configuration.contains(
                {"managed_secrets": [{"id": str(secret_id)}]}
            ),
        )
    )
    deployment_count = int(reference_result.scalar_one())
    if deployment_count:
        await db.rollback()
        raise ContainerApplicationSecretInUse(secret.name, deployment_count)
    return secret


async def complete_container_application_secret_delete(
    db: AsyncSession,
    secret: ContainerApplicationSecret,
    *,
    deleted_by: str,
) -> None:
    """Soft-delete metadata after its provider material has been removed."""

    secret.deleted_at = datetime.now(tz=UTC)
    secret.updated_by = deleted_by
    db.add(secret)
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise


async def list_active_container_application_secrets(
    db: AsyncSession,
    application_id: UUID,
) -> list[ContainerApplicationSecret]:
    """Every active secret with a provider reference, oldest first."""

    result = await db.execute(
        select(ContainerApplicationSecret)
        .where(
            ContainerApplicationSecret.application_id == application_id,
            ContainerApplicationSecret.deleted_at.is_(None),
            ContainerApplicationSecret.secret_reference.is_not(None),
        )
        .order_by(
            ContainerApplicationSecret.created_at.asc(),
            ContainerApplicationSecret.id.asc(),
        )
    )
    return list(result.scalars().all())


async def get_container_application_secrets_by_ids(
    db: AsyncSession,
    application_id: UUID,
    secret_ids: list[UUID],
) -> list[ContainerApplicationSecret]:
    """Resolve selected managed-secret references in request order."""

    if not secret_ids:
        return []
    result = await db.execute(
        select(ContainerApplicationSecret).where(
            ContainerApplicationSecret.application_id == application_id,
            ContainerApplicationSecret.id.in_(secret_ids),
            ContainerApplicationSecret.deleted_at.is_(None),
            ContainerApplicationSecret.secret_reference.is_not(None),
        )
    )
    secrets_by_id = {secret.id: secret for secret in result.scalars().all()}
    missing = [secret_id for secret_id in secret_ids if secret_id not in secrets_by_id]
    if missing:
        raise ContainerApplicationSecretNotFound(missing[0])
    return [secrets_by_id[secret_id] for secret_id in secret_ids]


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


async def create_registered_application_deployment(
    db: AsyncSession,
    version: RegisteredApplicationVersion,
    *,
    instance_name: str,
    tier: int,
    configuration: dict,
    triggered_by: str,
) -> DeploymentInstance:
    """Persist a pending instance; caller dispatches and commits atomically."""

    dns_configuration = configuration.get("dns")
    instance = DeploymentInstance(
        application=version.application,
        application_version=version,
        instance_name=instance_name,
        tier=tier,
        status=DeploymentInstanceStatus.PENDING.value,
        configuration=configuration,
        triggered_by=triggered_by,
        current_step=DeploymentStep.VALIDATING_CONFIGURATION.value,
        subdomain=(dns_configuration or {}).get("subdomain"),
        hostname=(dns_configuration or {}).get("hostname"),
        dns_status="pending" if dns_configuration else None,
    )
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.PENDING.value,
            message="Deployment configuration validated.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        constraint = _violated_constraint(error)
        if constraint == _DEPLOYMENT_SUBDOMAIN_CONSTRAINT:
            raise DuplicateDeploymentSubdomain(instance.subdomain) from error
        if constraint in (_DEPLOYMENT_NAME_CONSTRAINT, None):
            # ``None``: the driver did not report a name; the tier/name pair is
            # the only unique constraint a new instance row can violate.
            raise DuplicateDeploymentInstance(instance_name, tier) from error
        raise
    return instance


async def complete_registered_application_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
    *,
    event_message: str = "Deployment pipeline dispatched.",
) -> DeploymentInstance:
    """Mark a flushed deployment as dispatched and commit the transaction."""

    instance.status = DeploymentInstanceStatus.DEPLOYING.value
    instance.dispatch_reference = dispatch_reference
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.DEPLOYING.value,
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


async def prepare_registered_application_upgrade(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    target_version: str,
    triggered_by: str,
) -> DeploymentInstance:
    """Lock a running instance and stage its type-specific version upgrade."""

    instance = await get_registered_application_deployment(
        db,
        deployment_id,
        for_update=True,
    )
    if instance is None:
        await db.rollback()
        raise DeploymentInstanceNotFound(deployment_id)
    application_type = instance.application.application_type
    if application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        await db.rollback()
        raise DeploymentUpgradeNotSupported()
    if instance.status not in {
        DeploymentInstanceStatus.RUNNING.value,
        DeploymentInstanceStatus.FAILED.value,
    }:
        await db.rollback()
        raise DeploymentUpgradeNotAvailable(instance.status)

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
    if current == target_version and not retrying_failed_upgrade:
        await db.rollback()
        raise DeploymentAlreadyAtVersion(target_version)
    # Keep the last configuration that ran successfully for rollback. Retrying
    # a failed upgrade must not replace it with the failed attempt.
    if instance.status == DeploymentInstanceStatus.RUNNING.value or (
        instance.previous_configuration is None
    ):
        instance.previous_configuration = deepcopy(instance.configuration)
    instance.configuration = configuration
    instance.status = DeploymentInstanceStatus.UPDATING.value
    instance.current_step = DeploymentStep.VALIDATING_CONFIGURATION.value
    instance.failure_reason = None
    instance.completed_at = None
    instance.triggered_by = triggered_by
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.UPDATING.value,
            message=f"Upgrade to version '{target_version}' requested.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    try:
        await db.flush()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return instance


async def complete_registered_application_upgrade_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
    *,
    event_message: str | None = None,
) -> DeploymentInstance:
    """Commit a version change after its update pipeline is dispatched."""

    instance.status = DeploymentInstanceStatus.UPDATING.value
    instance.dispatch_reference = dispatch_reference
    target_version = configured_version(
        instance.configuration, instance.application.application_type
    )
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.UPDATING.value,
            message=event_message
            or f"Application upgrade pipeline dispatched for version '{target_version}'.",
            created_by=instance.triggered_by,
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


async def prepare_registered_application_termination(
    db: AsyncSession,
    deployment_id: UUID,
    *,
    triggered_by: str,
) -> DeploymentInstance:
    """Lock a supported instance and stage its destructive termination."""

    instance = await get_registered_application_deployment(
        db,
        deployment_id,
        for_update=True,
    )
    if instance is None:
        await db.rollback()
        raise DeploymentInstanceNotFound(deployment_id)
    if instance.application.application_type not in {
        ApplicationType.CONTAINERIZED.value,
        ApplicationType.GITHUB_WORKFLOW.value,
    }:
        await db.rollback()
        raise DeploymentTerminationNotSupported()
    if instance.status not in {
        DeploymentInstanceStatus.RUNNING.value,
        DeploymentInstanceStatus.FAILED.value,
    }:
        await db.rollback()
        raise DeploymentTerminationNotAvailable(instance.status)

    instance.status = DeploymentInstanceStatus.TERMINATING.value
    instance.current_step = DeploymentStep.VALIDATING_CONFIGURATION.value
    instance.failure_reason = None
    instance.completed_at = None
    instance.terminated_at = None
    instance.triggered_by = triggered_by
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.TERMINATING.value,
            message="Application deployment termination requested.",
            created_by=triggered_by,
        )
    )
    db.add(instance)
    try:
        await db.flush()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return instance


async def complete_registered_application_termination_dispatch(
    db: AsyncSession,
    instance: DeploymentInstance,
    dispatch_reference: dict,
) -> DeploymentInstance:
    """Commit terminating state after the cleanup pipeline is dispatched."""

    instance.status = DeploymentInstanceStatus.TERMINATING.value
    instance.dispatch_reference = dispatch_reference
    instance.events.append(
        DeploymentInstanceEvent(
            step=DeploymentStep.VALIDATING_CONFIGURATION.value,
            status=DeploymentInstanceStatus.TERMINATING.value,
            message="Application termination pipeline dispatched.",
            created_by=instance.triggered_by,
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


async def update_registered_application_deployment_progress(
    db: AsyncSession,
    deployment_id: UUID,
    payload: RegisteredApplicationDeploymentProgressUpdate,
) -> DeploymentInstance:
    """Apply one monotonic, idempotent pipeline progress update."""

    instance = await get_registered_application_deployment(
        db,
        deployment_id,
        for_update=True,
    )
    if instance is None:
        await db.rollback()
        raise DeploymentInstanceNotFound(deployment_id)

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

    now = datetime.now(tz=UTC)
    instance.current_step = next_step
    instance.status = next_status
    instance.failure_reason = payload.failure_reason
    if instance.subdomain and not is_upgrade:
        if is_termination:
            if next_step == DeploymentStep.CONFIGURING_DNS.value:
                instance.dns_status = "deleting"
            elif next_step == DeploymentStep.COMPLETED.value:
                instance.dns_status = "deleted"
            elif next_step == DeploymentStep.FAILED.value:
                instance.dns_status = "failed"
        elif next_step == DeploymentStep.CONFIGURING_DNS.value:
            instance.dns_status = "configuring"
        elif next_step == DeploymentStep.COMPLETED.value:
            instance.dns_status = "active"
        elif next_step == DeploymentStep.FAILED.value:
            instance.dns_status = "failed"
    instance.completed_at = (
        now
        if next_step in {DeploymentStep.COMPLETED.value, DeploymentStep.FAILED.value}
        else None
    )
    instance.terminated_at = (
        now
        if is_termination
        and next_step == DeploymentStep.COMPLETED.value
        and next_status == DeploymentInstanceStatus.TERMINATED.value
        else None
    )
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
    """Lock an instance and prepare redispatch of its stored configuration."""

    instance = await get_registered_application_deployment(
        db,
        deployment_id,
        for_update=True,
    )
    if instance is None:
        await db.rollback()
        raise DeploymentInstanceNotFound(deployment_id)
    if instance.status not in {
        DeploymentInstanceStatus.RUNNING.value,
        DeploymentInstanceStatus.FAILED.value,
    }:
        await db.rollback()
        raise DeploymentRollbackNotAvailable(instance.status)

    application_type = instance.application.application_type
    instance.current_step = DeploymentStep.VALIDATING_CONFIGURATION.value
    instance.failure_reason = None
    instance.completed_at = None
    instance.rollback_count += 1
    instance.triggered_by = triggered_by

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
        instance.status = DeploymentInstanceStatus.UPDATING.value
        message = (
            f"Rollback #{instance.rollback_count} to version '{previous_version}' requested."
        )
    else:
        # Never upgraded: a rollback re-applies the stored configuration.
        instance.status = DeploymentInstanceStatus.DEPLOYING.value
        if instance.subdomain:
            instance.dns_status = "pending"
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
    try:
        await db.flush()
    except SQLAlchemyError:
        await db.rollback()
        raise
    return instance

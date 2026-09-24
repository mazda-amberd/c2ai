"""Managed container secrets: metadata here, values only in the secret provider."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.constants.registered_application import (
    ApplicationType,
)
from c2ai.core.exceptions import (
    ContainerApplicationSecretInUse,
    ContainerApplicationSecretNotFound,
    ContainerSecretsNotSupported,
    DuplicateContainerApplicationSecret,
    RegisteredApplicationNotFound,
)
from c2ai.db.errors import violated_constraint
from c2ai.models.registered_application import (
    ContainerApplicationSecret,
    DeploymentInstance,
    RegisteredApplication,
)
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerApplicationSecretUpdate,
)

logger = logging.getLogger(__name__)


_CONTAINER_SECRET_CONSTRAINTS = {
    "uq_container_application_secrets_name_active",
    "uq_container_application_secrets_env_active",
}


@dataclass(frozen=True)
class ContainerApplicationSecretPage:
    """Paginated active managed secrets for one container application."""

    items: list[ContainerApplicationSecret]
    total: int


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
        if violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
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
        if violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
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
        if violated_constraint(error) in _CONTAINER_SECRET_CONSTRAINTS:
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

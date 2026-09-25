"""Managed container secrets (values are write-only; only references are stored)."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.api.registered_applications import clients
from c2ai.api.registered_applications.clients import PREFIX, TAGS
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.container_secret_provider import ContainerSecretProviderClient
from c2ai.core.exceptions import (
    ServiceUnavailableError,
)
from c2ai.db.session import get_db_session as db_session
from c2ai.registration import secrets as secret_store
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerApplicationSecretList,
    ContainerApplicationSecretOut,
    ContainerApplicationSecretUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix=PREFIX, tags=TAGS)


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

    page = await secret_store.list_container_application_secrets(
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
    provider: ContainerSecretProviderClient = Depends(clients.container_secret_provider),
) -> ContainerApplicationSecretOut:
    """Write secret material to the provider and persist only its reference."""

    secret = await secret_store.prepare_container_application_secret_create(
        db,
        application_id,
        payload,
        created_by=current_user.identifier,
    )
    reference = await provider.upsert_secret(
        secret_id=secret.id,
        application_id=application_id,
        name=secret.name,
        environment_variable=secret.environment_variable,
        secret_value=payload.secret_value.get_secret_value(),
    )
    secret = await secret_store.complete_container_application_secret_write(
        db,
        secret,
        reference=reference,
        updated_by=current_user.identifier,
    )
    await db.commit()
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
    provider: ContainerSecretProviderClient = Depends(clients.container_secret_provider),
) -> ContainerApplicationSecretOut:
    """Update metadata and optionally replace the provider-held value."""

    secret = await secret_store.prepare_container_application_secret_update(
        db,
        application_id,
        secret_id,
        payload,
        updated_by=current_user.identifier,
    )
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
    secret = await secret_store.complete_container_application_secret_write(
        db,
        secret,
        reference=reference,
        updated_by=current_user.identifier,
    )
    await db.commit()
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
    provider: ContainerSecretProviderClient = Depends(clients.container_secret_provider),
) -> Response:
    """Delete provider material before soft-deleting Athena metadata."""

    secret = await secret_store.prepare_container_application_secret_delete(
        db,
        application_id,
        secret_id,
    )
    if not secret.secret_reference:
        raise ServiceUnavailableError("The managed secret has no provider reference.")
    await provider.delete_secret(
        secret_id=secret.id,
        reference=secret.secret_reference,
    )
    await secret_store.complete_container_application_secret_delete(
        db,
        secret,
        deleted_by=current_user.identifier,
    )
    await db.commit()
    logger.info(
        "Container application secret deleted id=%s application=%s by=%s",
        secret.id,
        application_id,
        current_user.identifier,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

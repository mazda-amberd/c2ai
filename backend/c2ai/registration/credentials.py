"""Stored registry and LLM credentials (pgcrypto-encrypted, decrypted only for use)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.core.exceptions import (
    ServiceUnavailableError,
)
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ContainerApplicationConfiguration,
)
from c2ai.security import crypto

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContainerRegistryRuntime:
    """Decrypted registry values that must never be serialized to API clients."""

    username: str
    password: str


async def resolve_container_registry_credentials(
    db: AsyncSession,
    application_version_id: UUID,
) -> ContainerRegistryRuntime | None:
    """Decrypt a managed registry credential for an outbound registry call."""

    result = await db.execute(
        select(
            ContainerApplicationConfiguration.registry_username,
            ContainerApplicationConfiguration.registry_password_encrypted,
        ).where(
            ContainerApplicationConfiguration.application_version_id
            == application_version_id,
            ContainerApplicationConfiguration.registry_password_encrypted.is_not(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    username, encrypted = row
    password = await crypto.decrypt_stored(db, encrypted, column=crypto.REGISTRY_PASSWORD)
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
        select(ApplicationLLMConfiguration.api_token_encrypted).where(
            ApplicationLLMConfiguration.application_version_id == application_version_id,
            ApplicationLLMConfiguration.api_token_encrypted.is_not(None),
        )
    )
    token = await crypto.decrypt_stored(
        db, result.scalar_one_or_none(), column=crypto.LLM_API_TOKEN
    )
    if not isinstance(token, str) or not token:
        return None
    return token

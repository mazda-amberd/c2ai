"""Stored registry and LLM credentials (pgcrypto-encrypted, decrypted only for use)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.core.exceptions import (
    ServiceUnavailableError,
)
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ContainerApplicationConfiguration,
)

logger = logging.getLogger(__name__)


def credential_encryption_key() -> str:
    key = get_settings().credential_encryption_key.strip()
    if not key:
        raise ServiceUnavailableError(
            "Credential storage is not configured. Set "
            "ATHENA_CREDENTIAL_ENCRYPTION_KEY."
        )
    return key


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
            func.pgp_sym_decrypt(
                ContainerApplicationConfiguration.registry_password_encrypted,
                credential_encryption_key(),
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
                credential_encryption_key(),
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

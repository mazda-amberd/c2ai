"""Persistence and credential resolution for reusable GitHub connections."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.core.exceptions import DuplicateGitHubConnection, ServiceUnavailableError
from c2ai.models.registered_application import (
    GitHubApplicationConfiguration,
    GitHubConnection,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.github_connection import GitHubConnectionCreate

_URL_CONSTRAINT = "uq_github_connections_url_active"


@dataclass(frozen=True)
class GitHubConnectionRuntime:
    """Decrypted runtime values that must never be serialized to API clients."""

    token: str
    api_base_url: str


def _encryption_key() -> str:
    key = os.getenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ServiceUnavailableError(
            "Credential storage is not configured. Set "
            "ATHENA_CREDENTIAL_ENCRYPTION_KEY."
        )
    return key


def github_api_base_url(connection_url: str) -> str:
    """Translate a GitHub web/organization URL into its REST API base URL."""

    parsed = urlsplit(connection_url)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"github.com", "www.github.com", "api.github.com"}:
        return "https://api.github.com"
    return f"{parsed.scheme}://{parsed.netloc}/api/v3"


def _violated_constraint(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    for candidate in (
        original,
        getattr(original, "orig", None),
        getattr(original, "__cause__", None),
    ):
        constraint_name = getattr(candidate, "constraint_name", None)
        if constraint_name:
            return constraint_name
        diagnostic = getattr(candidate, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name:
            return constraint_name
    return None


async def create_github_connection(
    db: AsyncSession,
    payload: GitHubConnectionCreate,
    *,
    created_by: str,
) -> GitHubConnection:
    """Encrypt and persist a GitHub token without retaining plaintext."""

    connection = GitHubConnection(
        display_name=payload.connection_name,
        connection_url=payload.connection_url,
        access_token_encrypted=func.pgp_sym_encrypt(
            payload.access_token.get_secret_value(),
            _encryption_key(),
            "cipher-algo=aes256",
        ),
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(connection)
    try:
        await db.flush()
        await db.commit()
        await db.refresh(connection)
    except IntegrityError as error:
        await db.rollback()
        if _violated_constraint(error) == _URL_CONSTRAINT:
            raise DuplicateGitHubConnection(payload.connection_url) from error
        raise
    except SQLAlchemyError:
        await db.rollback()
        raise
    return connection


async def list_github_connections(
    db: AsyncSession,
) -> tuple[list[GitHubConnection], list[str]]:
    """Return stored connections and legacy IDs referenced by old templates."""

    stored_result = await db.execute(
        select(GitHubConnection)
        .where(GitHubConnection.deleted_at.is_(None))
        .order_by(func.lower(GitHubConnection.display_name), GitHubConnection.id)
    )
    stored = list(stored_result.scalars().all())

    legacy_result = await db.execute(
        select(distinct(GitHubApplicationConfiguration.github_connection_id))
        .join(
            RegisteredApplicationVersion,
            RegisteredApplicationVersion.id
            == GitHubApplicationConfiguration.application_version_id,
        )
        .join(
            RegisteredApplication,
            RegisteredApplication.id == RegisteredApplicationVersion.application_id,
        )
        .where(RegisteredApplication.deleted_at.is_(None))
        .order_by(GitHubApplicationConfiguration.github_connection_id)
    )
    stored_ids = {str(connection.id) for connection in stored}
    legacy = [
        connection_id
        for connection_id in legacy_result.scalars().all()
        if connection_id and connection_id not in stored_ids
    ]
    return stored, legacy


async def resolve_github_connection(
    db: AsyncSession,
    connection_id: str,
) -> GitHubConnectionRuntime | None:
    """Resolve a managed connection; return None for legacy connection IDs."""

    try:
        parsed_id = UUID(connection_id)
    except (TypeError, ValueError):
        return None

    result = await db.execute(
        select(
            GitHubConnection.connection_url,
            func.pgp_sym_decrypt(
                GitHubConnection.access_token_encrypted,
                _encryption_key(),
            ),
        ).where(
            GitHubConnection.id == parsed_id,
            GitHubConnection.deleted_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    connection_url, token = row
    if not isinstance(token, str) or not token:
        raise ServiceUnavailableError("The GitHub connection credential is invalid.")
    return GitHubConnectionRuntime(
        token=token,
        api_base_url=github_api_base_url(connection_url),
    )

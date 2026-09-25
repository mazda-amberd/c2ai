"""Persistence and credential resolution for reusable GitHub connections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.core.exceptions import (
    DuplicateGitHubConnection,
    GitHubConnectionInUse,
    GitHubConnectionNotFound,
    ServiceUnavailableError,
)
from c2ai.db.errors import violated_constraint
from c2ai.models.registered_application import (
    GitHubApplicationConfiguration,
    GitHubConnection,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.github_connection import GitHubConnectionCreate
from c2ai.security import crypto

_URL_CONSTRAINT = "uq_github_connections_url_active"


@dataclass(frozen=True)
class GitHubConnectionRuntime:
    """Decrypted runtime values that must never be serialized to API clients."""

    token: str
    api_base_url: str


def github_api_base_url(connection_url: str) -> str:
    """Translate a GitHub web/organization URL into its REST API base URL."""

    parsed = urlsplit(connection_url)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"github.com", "www.github.com", "api.github.com"}:
        return "https://api.github.com"
    return f"{parsed.scheme}://{parsed.netloc}/api/v3"


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
        access_token_encrypted=crypto.encrypt(
            payload.access_token.get_secret_value(), column=crypto.GITHUB_TOKEN
        ),
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(connection)
    try:
        await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == _URL_CONSTRAINT:
            raise DuplicateGitHubConnection(payload.connection_url) from error
        raise
    await db.refresh(connection)
    return connection


def _current_versions():
    """Join from GitHub configurations to the non-deleted templates whose current version they are."""

    return (
        select(GitHubApplicationConfiguration.github_connection_id, RegisteredApplication.name)
        .join(
            RegisteredApplicationVersion,
            RegisteredApplicationVersion.id
            == GitHubApplicationConfiguration.application_version_id,
        )
        .join(
            RegisteredApplication,
            and_(
                RegisteredApplication.id == RegisteredApplicationVersion.application_id,
                RegisteredApplication.current_version == RegisteredApplicationVersion.version,
            ),
        )
        .where(RegisteredApplication.deleted_at.is_(None))
    )


async def connection_usage(db: AsyncSession) -> dict[str, list[str]]:
    """Connection id -> names of the templates that deploy with it now."""

    result = await db.execute(_current_versions().order_by(func.lower(RegisteredApplication.name)))
    usage: dict[str, list[str]] = defaultdict(list)
    for connection_id, name in result.all():
        usage[str(connection_id)].append(name)
    return dict(usage)


async def get_github_connection(
    db: AsyncSession, connection_id: UUID, *, for_update: bool = False
) -> GitHubConnection | None:
    statement = select(GitHubConnection).where(
        GitHubConnection.id == connection_id,
        GitHubConnection.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def update_github_connection(
    db: AsyncSession,
    connection: GitHubConnection,
    *,
    name: str,
    connection_url: str,
    access_token: str | None,
    updated_by: str,
) -> GitHubConnection:
    """Rename or repoint a connection; a new token replaces the stored one."""

    connection.display_name = name
    connection.connection_url = connection_url
    if access_token is not None:
        connection.access_token_encrypted = crypto.encrypt(access_token, column=crypto.GITHUB_TOKEN)
    connection.updated_by = updated_by
    try:
        await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == _URL_CONSTRAINT:
            raise DuplicateGitHubConnection(connection_url) from error
        raise
    await db.refresh(connection)
    return connection


async def delete_github_connection(
    db: AsyncSession, connection_id: UUID, *, deleted_by: str
) -> None:
    """Soft-delete a connection no template deploys with any more.

    A template still using it would fall back to the server's own token.
    """

    connection = await get_github_connection(db, connection_id, for_update=True)
    if connection is None:
        raise GitHubConnectionNotFound(connection_id)
    used_by = (await connection_usage(db)).get(str(connection_id))
    if used_by:
        raise GitHubConnectionInUse(connection.display_name, used_by)
    connection.deleted_at = datetime.now(tz=UTC)
    connection.updated_by = deleted_by
    await db.flush()


async def list_github_connections(
    db: AsyncSession,
) -> tuple[list[GitHubConnection], list[str]]:
    """Return stored connections and the legacy IDs templates deploy with now."""

    stored_result = await db.execute(
        select(GitHubConnection)
        .where(GitHubConnection.deleted_at.is_(None))
        .order_by(func.lower(GitHubConnection.display_name), GitHubConnection.id)
    )
    stored = list(stored_result.scalars().all())

    # Current versions only: an old version's connection is history.
    current = _current_versions().subquery()
    legacy_result = await db.execute(
        select(distinct(current.c.github_connection_id)).order_by(current.c.github_connection_id)
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
            GitHubConnection.access_token_encrypted,
        ).where(
            GitHubConnection.id == parsed_id,
            GitHubConnection.deleted_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    connection_url, encrypted = row
    token = await crypto.decrypt_stored(db, encrypted, column=crypto.GITHUB_TOKEN)
    if not isinstance(token, str) or not token:
        raise ServiceUnavailableError("The GitHub connection credential is invalid.")
    return GitHubConnectionRuntime(
        token=token,
        api_base_url=github_api_base_url(connection_url),
    )

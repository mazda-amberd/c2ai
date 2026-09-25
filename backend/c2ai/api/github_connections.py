# pylint: disable=import-error
"""Administrator API for reusable GitHub connections: list, test, create, edit, delete."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token, require_admin
from c2ai.clients.github_connection import (
    GitHubConnectionValidationResult,
    validate_github_repository_connection,
)
from c2ai.core.exceptions import GitHubConnectionNotFound, GitHubConnectionValidationFailed
from c2ai.crud import github_connection as crud_github_connection
from c2ai.db.session import get_db_session as db_session
from c2ai.schemas.github_connection import (
    GitHubConnectionCreate,
    GitHubConnectionCredentials,
    GitHubConnectionList,
    GitHubConnectionOut,
    GitHubConnectionUpdate,
    GitHubConnectionValidationOut,
)

router = APIRouter(prefix="/api/github-connections", tags=["GitHub Connections"])


async def _validate_credentials(
    payload: GitHubConnectionCredentials,
) -> GitHubConnectionValidationResult:
    return await validate_github_repository_connection(
        repository_url=payload.repository_url,
        access_token=payload.access_token.get_secret_value(),
    )


def _connection_out(connection, usage: dict[str, list[str]]) -> GitHubConnectionOut:
    return GitHubConnectionOut(
        id=connection.id,
        display_name=connection.display_name,
        connection_url=connection.connection_url,
        legacy=False,
        created_by=connection.created_by,
        created_at=connection.created_at,
        used_by=usage.get(str(connection.id), []),
    )


@router.get("", response_model=GitHubConnectionList)
async def list_github_connections(
    _current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(db_session),
) -> GitHubConnectionList:
    """List managed connections plus IDs referenced by existing applications.

    Names and URLs only (never a token), so any signed-in user may read them:
    the read-only catalog names each application's connection.
    """

    stored, legacy = await crud_github_connection.list_github_connections(db)
    usage = await crud_github_connection.connection_usage(db)
    items = [_connection_out(connection, usage) for connection in stored]
    items.extend(
        GitHubConnectionOut(
            id=connection_id,
            display_name=connection_id,
            connection_url=None,
            legacy=True,
            used_by=usage.get(connection_id, []),
        )
        for connection_id in legacy
    )
    return GitHubConnectionList(items=items, total=len(items))


@router.post("/validate", response_model=GitHubConnectionValidationOut)
async def validate_github_connection(
    payload: GitHubConnectionCredentials,
    _current_user: AthenaTokenUser = Depends(require_admin),
) -> GitHubConnectionValidationOut:
    """Validate repository access without storing the supplied token."""

    result = await _validate_credentials(payload)
    return GitHubConnectionValidationOut(
        valid=result.valid,
        message=result.message,
        repository=result.repository,
    )


@router.post(
    "",
    response_model=GitHubConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_github_connection(
    payload: GitHubConnectionCreate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> GitHubConnectionOut:
    """Validate and create a connection without ever returning its token."""

    validation = await _validate_credentials(payload)
    if not validation.valid:
        raise GitHubConnectionValidationFailed(validation.message)

    connection = await crud_github_connection.create_github_connection(
        db,
        payload,
        created_by=current_user.identifier,
    )
    await db.commit()
    return _connection_out(connection, {})


@router.put("/{connection_id}", response_model=GitHubConnectionOut)
async def update_github_connection(
    connection_id: UUID,
    payload: GitHubConnectionUpdate,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> GitHubConnectionOut:
    """Rename a connection, point it elsewhere, or give it a new token.

    A new URL or token is checked with GitHub first (a new URL with the
    stored token). Every template using the connection deploys with the
    change from then on.
    """

    connection = await crud_github_connection.get_github_connection(db, connection_id)
    if connection is None:
        raise GitHubConnectionNotFound(connection_id)
    token = payload.access_token.get_secret_value() if payload.access_token else None
    if token is not None or payload.repository_url != connection.connection_url:
        stored = await crud_github_connection.resolve_github_connection(db, str(connection_id))
        validation = await validate_github_repository_connection(
            repository_url=payload.repository_url,
            access_token=token or (stored.token if stored else ""),
        )
        if not validation.valid:
            raise GitHubConnectionValidationFailed(validation.message)

    connection = await crud_github_connection.update_github_connection(
        db,
        connection,
        name=payload.connection_name,
        connection_url=payload.repository_url,
        access_token=token,
        updated_by=current_user.identifier,
    )
    await db.commit()
    return _connection_out(connection, await crud_github_connection.connection_usage(db))


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_github_connection(
    connection_id: UUID,
    current_user: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> Response:
    """Delete a connection no template deploys with; one still in use is refused."""

    await crud_github_connection.delete_github_connection(
        db, connection_id, deleted_by=current_user.identifier
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

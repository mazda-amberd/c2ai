# pylint: disable=import-error
"""Administrator API for reusable GitHub connections."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token, require_admin
from c2ai.clients.github_connection import (
    GitHubConnectionValidationResult,
    validate_github_repository_connection,
)
from c2ai.core.exceptions import GitHubConnectionValidationFailed
from c2ai.crud import github_connection as crud_github_connection
from c2ai.db.session import get_db_session as db_session
from c2ai.schemas.github_connection import (
    GitHubConnectionCreate,
    GitHubConnectionCredentials,
    GitHubConnectionList,
    GitHubConnectionOut,
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


def _connection_out(connection) -> GitHubConnectionOut:
    return GitHubConnectionOut(
        id=connection.id,
        display_name=connection.display_name,
        connection_url=connection.connection_url,
        legacy=False,
        created_by=connection.created_by,
        created_at=connection.created_at,
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
    items = [_connection_out(connection) for connection in stored]
    items.extend(
        GitHubConnectionOut(
            id=connection_id,
            display_name=connection_id,
            connection_url=None,
            legacy=True,
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
    return _connection_out(connection)

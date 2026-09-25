"""User administration endpoints.

Visibility: a superuser (the bootstrap ``admin``) sees every user; any other
administrator sees the users they created plus themselves. Admin rights are
the ``users.user_type`` column, exposed to the UI as ``metadata.user_type``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.cookie import set_auth_cookie
from c2ai.auth.jwt import (
    AthenaTokenUser,
    default_token_ttl_seconds,
    get_current_user_token,
    issue_session_token,
    require_admin,
)
from c2ai.core.exceptions import (
    CannotDeleteLastAdminUser,
    CannotUpdateLastAdminToUser,
    DuplicateUser,
    FailedToDelete,
    NoUsersFound,
    UserNotFound,
    ValidationFailed,
)
from c2ai.crud import user as crud_user
from c2ai.db.session import get_db_session
from c2ai.models.user import ADMIN, parse_user_type
from c2ai.schemas import user as schemas_user
from c2ai.utils.password import generate_password
from c2ai.utils.validators import validate_identifier, validate_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

_USER_TYPE_ERROR = "metadata_.user_type must be either 'Admin' or 'User'"


@router.get(
    "/",
    response_model=schemas_user.UserOut | list[schemas_user.UserOut],
    status_code=status.HTTP_200_OK,
)
async def read_users(
    start: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user_name: str | None = Query(None),
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """List visible users, or return one when ``user_name`` is given."""

    if user_name:
        user = await crud_user.get_user_by_identifier(db, user_name, viewer=admin.viewer)
        if not user:
            raise UserNotFound(user_name)
        return schemas_user.UserOut.model_validate(user)

    users = await crud_user.get_users(db, start=start, limit=limit, viewer=admin.viewer)
    if not users:
        raise NoUsersFound()
    return [schemas_user.UserOut.model_validate(user) for user in users]


@router.post(
    "/",
    response_model=schemas_user.UserOutWithCredentials,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: schemas_user.UserCreate,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a user with a generated one-time password (returned once)."""

    ok, error, identifier = validate_identifier(payload.identifier)
    if not ok:
        raise ValidationFailed(error)
    metadata = dict(payload.metadata_ or {})
    if parse_user_type(metadata.get("user_type")) is None:
        raise ValidationFailed(_USER_TYPE_ERROR)
    if await crud_user.get_user_by_identifier(db, identifier):
        raise DuplicateUser(identifier)

    password = generate_password()
    ok, error = validate_password(password)
    if not ok:
        raise ValidationFailed(error)

    db_user = await crud_user.create_user(
        db,
        {
            "identifier": identifier,
            "password": password,
            "first_name": payload.first_name,
            "last_name": payload.last_name,
            "metadata_": metadata,
            "created_by": admin.identifier,
            "created_by_id": admin.user_id,
        },
    )
    await db.commit()
    logger.info("User '%s' created by '%s'.", identifier, admin.identifier)
    return schemas_user.UserOutWithCredentials(
        **schemas_user.UserOut.model_validate(db_user).model_dump(),
        temporary_password=password,
    )


@router.patch(
    "/",
    response_model=schemas_user.UserUpdateResponse,
    status_code=status.HTTP_200_OK,
)
async def update_existing_user(
    user_name: str,
    user_update_data: schemas_user.UserUpdate,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Update names or metadata of a user (passwords use their own endpoints)."""

    target = await crud_user.get_user_by_identifier(db, user_name, viewer=admin.viewer)
    if not target:
        raise UserNotFound(user_name)

    updates = user_update_data.model_dump(exclude_unset=True, exclude={"password"})
    updates["updated_by"] = admin.identifier
    incoming_meta = updates.get("metadata_")
    if isinstance(incoming_meta, dict) and "user_type" in incoming_meta:
        next_type = parse_user_type(incoming_meta.get("user_type"))
        if next_type is None:
            raise ValidationFailed(_USER_TYPE_ERROR)
        if target.user_type == ADMIN and next_type != ADMIN:
            admins = await crud_user.lock_admin_ids(db)
            if len(admins) <= 1:
                raise CannotUpdateLastAdminToUser()

    updated = await crud_user.update_user(db, target, updates)
    await db.commit()
    return {
        "message": f"User '{updated.identifier}' was successfully updated.",
        "user": schemas_user.UserOut.model_validate(updated),
    }


@router.patch(
    "/update_password",
    response_model=schemas_user.UserPasswordUpdate,
    status_code=status.HTTP_200_OK,
)
async def update_user_password(
    payload: schemas_user.UpdatePasswordPayload,
    response: Response,
    current_user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
):
    """Change the caller's own password and clear ``needs_password_reset``.

    Every other session of the account is signed out; this browser gets a
    fresh session cookie so it stays signed in.
    """

    user = await crud_user.get_user_by_identifier(db, current_user.identifier)
    if not user:
        raise UserNotFound(current_user.identifier)
    ok, error = validate_password(payload.new_password)
    if not ok:
        raise ValidationFailed(error)
    await crud_user.update_user_password(
        db,
        target_user=user,
        new_password=payload.new_password,
        needs_password_reset=False,
        updated_by=current_user.identifier,
    )
    await db.commit()
    ttl_seconds = default_token_ttl_seconds()
    set_auth_cookie(response, issue_session_token(user, ttl_seconds=ttl_seconds), max_age=ttl_seconds)
    logger.info("Password updated by '%s'.", current_user.identifier)
    return {"message": "Password updated successfully."}


@router.post(
    "/reset_password/{user_name}",
    response_model=schemas_user.UserPasswordUpdateResponse,
    status_code=status.HTTP_200_OK,
)
@router.get(
    "/reset_password/{user_name}",
    response_model=schemas_user.UserPasswordUpdateResponse,
    status_code=status.HTTP_200_OK,
    deprecated=True,
    include_in_schema=False,
)
async def reset_user_password(
    user_name: str,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Issue a new one-time password for a user (POST; GET kept for old clients)."""

    user = await crud_user.get_user_by_identifier(db, user_name, viewer=admin.viewer)
    if not user:
        raise UserNotFound(user_name)
    new_password = generate_password()
    ok, error = validate_password(new_password)
    if not ok:
        raise ValidationFailed(error)
    await crud_user.update_user_password(
        db,
        target_user=user,
        new_password=new_password,
        needs_password_reset=True,
        updated_by=admin.identifier,
    )
    await db.commit()
    logger.info("Password for '%s' reset by '%s'.", user_name, admin.identifier)
    return {
        "message": f"Password for user '{user_name}' has been reset successfully.",
        "identifier": user_name,
        "temporary_password": new_password,
    }


@router.delete("/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_existing_user(
    user_name: str = Query(..., description="Identifier of the user to delete"),
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete a user, refusing to remove the last administrator."""

    user = await crud_user.get_user_by_identifier(db, user_name, viewer=admin.viewer)
    if not user:
        raise UserNotFound(user_name)
    if user.user_type == ADMIN:
        admins = await crud_user.lock_admin_ids(db)
        if len(admins) <= 1:
            raise CannotDeleteLastAdminUser()
    if not await crud_user.delete_user(db, user):
        raise FailedToDelete(user_name)
    await db.commit()
    logger.info("User '%s' deleted by '%s'.", user_name, admin.identifier)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

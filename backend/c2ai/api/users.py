# pylint: disable=import-error
"""
API endpoints for user management.

Includes routes for creating, reading, updating, deleting users, and
updating/resetting passwords.

Aligned to the new `users` table/model:
- No role table/relationship
- created_by/updated_by are top-level columns
- metadata_ is JSONB (column name: metadata)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import decode_jwt, require_admin, get_access_token
from c2ai.auth.cookie import get_token_from_cookie_or_header

from c2ai.crud import user as crud_user
from c2ai.db.session import get_db_session as db_session
from c2ai.schemas import user as schemas_user

from c2ai.auth.dependencies import get_current_user_identifier
from c2ai.utils.password import generate_password
from c2ai.utils.validators import validate_identifier, validate_password

from c2ai.core.exceptions import (
    CannotDeleteLastAdminUser,
    CannotUpdateLastAdminToUser,
    DuplicateUser,
    FailedToDelete,
    NoUsersFound,
    UserNotFound,
    ValidationFailed,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


def _norm_user_type(value: object) -> str:
    """
    Normalize user_type string for comparison.

    Arges:
        value (object): The user_type value to normalize.

    Returns:
        str: Normalized user_type string.
    """
    return str(value or "").strip().lower()


@router.get(
    "/",
    response_model=Union[schemas_user.UserOut, List[schemas_user.UserOut]],
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_200_OK,
)
async def read_users(
    start: int = 0,
    limit: int = 100,
    user_name: Optional[str] = Query(None, alias="user_name"),
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Retrieve users, optionally filtered by user_name.

    Args:
        start (int): Pagination start index.
        limit (int): Maximum number of users to return.
        user_name (Optional[str]): Specific user identifier to filter by.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        Union[schemas_user.UserOut, List[schemas_user.UserOut]]: Single user or
        list of users.

    Raises:
        UserNotFound: If a specific user_name is provided but not found.
        NoUsersFound: If no users are found in the database.
    """
    curr_user_ident = decode_jwt(token).identifier
    logger.info(
        "Read users requested by '%s' (filter=%s, start=%d, limit=%d)",
        curr_user_ident,
        user_name,
        start,
        limit,
    )

    if user_name:
        user = await crud_user.get_user_by_identifier(
            db=db,
            identifier=user_name,
            curr_user_ident=curr_user_ident,
        )
        if not user:
            raise UserNotFound(user_name)
        return schemas_user.UserOut.model_validate(user)

    users = await crud_user.get_users(
        db=db,
        start=start,
        limit=limit,
        curr_user_ident=curr_user_ident,
    )
    if not users:
        raise NoUsersFound()

    return [schemas_user.UserOut.model_validate(u) for u in users]


@router.post(
    "/",
    response_model=schemas_user.UserOutWithCredentials,
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: schemas_user.UserCreate,
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Create a new user (admin-only). Generates a temporary password and sets
    metadata_.needs_password_reset = True via CRUD create_user().

    Args:
        payload (schemas_user.UserCreate): User creation payload.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        schemas_user.UserOutWithCredentials: Created user details with
        temporary password.

    Raises:
        ValidationFailed: If identifier or password validation fails.
        DuplicateUser: If a user with the same identifier already exists.
    """
    curr_user_ident = await get_current_user_identifier(token=token, db=db)
    logger.info("Create user requested by '%s' (identifier=%s)",
                curr_user_ident, payload.identifier)

    user_data = payload.model_dump()

    ok, error, new_ident = validate_identifier(user_data.get("identifier", ""))
    if not ok:
        logger.warning("Invalid identifier for user creation attempt: %s", error)
        raise ValidationFailed(error)

    password = generate_password()
    ok, error = validate_password(password)
    if not ok:
        logger.warning("Invalid password for user creation attempt: %s", error)
        raise ValidationFailed(error)

    existing = await crud_user.get_user_by_identifier(
                                db, new_ident,
                                curr_user_ident=curr_user_ident)
    if existing:
        logger.warning("Duplicate user creation attempt for identifier '%s'.",
                       new_ident)
        raise DuplicateUser(new_ident)

    metadata = user_data.get("metadata_")
    if not isinstance(metadata, dict):
        logger.warning("Metadata field of user creation attempt for identifier '%s'.",
                       new_ident)
        raise ValidationFailed("metadata_ must be a dictionary")

    user_type = metadata.get("user_type")
    if _norm_user_type(user_type) not in ("admin", "user"):
        logger.warning("User creation attempt for identifier '%s'.", new_ident)
        raise ValidationFailed("metadata_.user_type must be either 'Admin' or 'User'")

    user_data["identifier"] = new_ident
    user_data["password"] = password
    user_data["created_by"] = curr_user_ident
    user_data.setdefault("metadata_", {})
    user_data["metadata_"].setdefault("user_type", user_type)

    db_user = await crud_user.create_user(db, user_data)
    logger.info("User '%s' created successfully by '%s'.",
                new_ident, curr_user_ident)

    return schemas_user.UserOutWithCredentials(
        **schemas_user.UserOut.model_validate(db_user).model_dump(),
        temporary_password=password,
    )


@router.patch(
    "/",
    response_model=schemas_user.UserUpdateResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin)],
)
async def update_existing_user(
    user_name: str,
    user_update_data: schemas_user.UserUpdate,
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Update an existing user (admin-only), excluding password changes.

    Args:
        user_name (str): Unique identifier of the user to update.
        user_update_data (schemas_user.UserUpdate): Data for updating the user.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        schemas_user.UserUpdateResponse: Confirmation message and updated user data.

    Raises:
        UserNotFound: If the target user does not exist.
        CannotUpdateLastAdminToUser: If attempting to change the last admin to a non-admin
            user.
        ValidationFailed: If validation of the update data fails.
    """
    curr_user_ident = await get_current_user_identifier(token=token, db=db)
    logger.info("Update user requested by '%s' (target=%s)", curr_user_ident, user_name)

    target_user = await crud_user.get_user_by_identifier(db, user_name, curr_user_ident=curr_user_ident)
    if not target_user:
        raise UserNotFound(user_name)

    updates = user_update_data.model_dump(exclude_unset=True)

    incoming_meta = updates.get("metadata_") if isinstance(updates.get("metadata_"), dict) else None
    if incoming_meta is not None and "user_type" in incoming_meta:
        admin_count = await crud_user.get_admin_users_count(db)
        current_type = _norm_user_type((target_user.metadata_ or {}).get("user_type"))
        next_type = _norm_user_type(incoming_meta.get("user_type"))

        if admin_count <= 1 and current_type == "admin" and next_type != "admin":
            raise CannotUpdateLastAdminToUser()

        if next_type not in ("admin", "user"):
            raise ValidationFailed("metadata_.user_type must be either 'Admin' or 'User'")

    updated = await crud_user.update_user(
        db=db,
        target_user=target_user,
        updates=updates,
        curr_user_ident=curr_user_ident,
    )
    if not updated:
        raise UserNotFound(user_name)

    user_out = schemas_user.UserOut.model_validate(updated)
    return {
        "message": f"User '{updated.identifier}' was successfully updated.",
        "user": user_out,
    }


@router.patch(
    "/update_password",
    response_model=schemas_user.UserPasswordUpdate,
    status_code=status.HTTP_200_OK,
)
async def update_user_password(
    payload: schemas_user.UpdatePasswordPayload,
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Update the password for the current user. Sets needs_password_reset=False.

    Args:
        payload (schemas_user.UpdatePasswordPayload): Payload containing the new password.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        schemas_user.UserPasswordUpdate: Confirmation message.

    Raises:
        UserNotFound: If the current user does not exist.
        ValidationFailed: If the new password fails validation.
    """
    curr_user_ident = decode_jwt(token).identifier
    logger.info("Password update requested by '%s'", curr_user_ident)

    curr_user = await crud_user.get_user_by_identifier(db, curr_user_ident, curr_user_ident=curr_user_ident)
    if not curr_user:
        logger.info("Current user '%s' not found for password update.",
                    curr_user_ident)
        raise UserNotFound(curr_user_ident)

    ok, error = validate_password(payload.new_password)
    if not ok:
        logger.warning("Invalid password update attempt by '%s': %s",
                       curr_user_ident, error)
        raise ValidationFailed(error)

    updated = await crud_user.update_user_password(
        db=db,
        target_user=curr_user,
        new_password=payload.new_password,
        curr_user_ident=curr_user_ident,
        needs_password_reset=False,
        updated_by=curr_user_ident,
    )
    logger.info("Password update requested by '%s'", curr_user_ident)
    if not updated:
        raise UserNotFound(curr_user_ident)
    logger.info(
        "Password update attempt by '%s' was successfully updated.",
        curr_user_ident)
    return {"message": "Password updated successfully."}


@router.get(
    "/reset_password/{user_name}",
    response_model=schemas_user.UserPasswordUpdateResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin)],
)
async def reset_user_password(
    user_name: str,
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Reset a user's password (admin-only). Generates a new compliant password,
    updates hashed password, and sets needs_password_reset=True.

    Args:
        user_name (str): Unique identifier of the user whose password is to be reset.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        schemas_user.UserPasswordUpdateResponse: Confirmation message and
        temporary password.

    Raises:
        UserNotFound: If the target user does not exist.
        ValidationFailed: If the generated password fails validation.
    """
    curr_user_ident = await get_current_user_identifier(token=token, db=db)
    logger.info("Password reset requested by '%s' for '%s'",
                curr_user_ident, user_name)

    user_obj = await crud_user.get_user_by_identifier(
                                        db, user_name,
                                        curr_user_ident=curr_user_ident)
    if not user_obj:
        logger.info("Current user '%s' not found for password reset.",
                    user_name)
        raise UserNotFound(user_name)

    new_password = generate_password()
    ok, error = validate_password(new_password)
    if not ok:
        logger.warning("Invalid password reset attempt by '%s'",
                       user_name)
        raise ValidationFailed(error)

    updated = await crud_user.update_user_password(
        db=db,
        target_user=user_obj,
        new_password=new_password,
        curr_user_ident=curr_user_ident,
        needs_password_reset=True,
        updated_by=curr_user_ident,
    )
    logger.info("Password reset attempt by '%s' was successfully updated.",
                user_name)
    if not updated:
        raise UserNotFound(user_name)

    # Return the temporary password to the admin
    logger.info("Password for user '%s' has been reset by admin '%s'.",
                user_name, curr_user_ident)
    return {
        "message": f"Password for user '{user_name}' has been reset successfully.",
        "identifier": user_name,
        "temporary_password": new_password,
    }


@router.delete(
    "/",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_existing_user(
    user_name: str = Query(..., description="Unique identifier of the user"),
    token: str = Depends(get_access_token),
    db: AsyncSession = Depends(db_session),
):
    """
    Delete an existing user by identifier (admin-only).

    Args:
        user_name (str): Unique identifier of the user to delete.
        token (str): JWT token for authentication.
        db (AsyncSession): Database session.

    Returns:
        None

    Raises:
        UserNotFound: If the target user does not exist.
        CannotDeleteLastAdminUser: If attempting to delete the last admin user.
        FailedToDelete: If the deletion operation fails.
    """
    curr_user_ident = await get_current_user_identifier(token=token, db=db)
    logger.info("Delete user requested by '%s' (target=%s)",
                curr_user_ident, user_name)

    user_obj = await crud_user.get_user_by_identifier(
                                        db,
                                        user_name,
                                        curr_user_ident=curr_user_ident)
    if not user_obj:
        logger.info("User '%s' not found for deletion.", user_name)
        raise UserNotFound(user_name)

    admin_count = await crud_user.get_admin_users_count(db)
    logger.info("Delete user requested by '%s' (target=%s)",
                user_name, admin_count)
    if (_norm_user_type((user_obj.metadata_ or {}).get("user_type")) == "admin"
            and admin_count <= 1):
        logger.warning("Attempt to delete last admin user '%s'.", user_name)
        raise CannotDeleteLastAdminUser()

    deleted = await crud_user.delete_user(db, user_obj, curr_user_ident=curr_user_ident)
    logger.info("Delete user requested by '%s' (target=%s)",
                curr_user_ident, user_name)
    if not deleted:
        logger.info("User '%s' not found for deletion.", user_name)
        raise FailedToDelete(user_name)

    logger.info("User '%s' deleted successfully by '%s'.",
                user_name, curr_user_ident)
    return None

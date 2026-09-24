# pylint: disable=import-error
"""
CRUD operations for the User model.

This module provides asynchronous functions to create, read, update, and delete
users in the database.

Async SQLAlchemy (AsyncSession) + Argon2 hashing.
Aligned to `src/models/db/user.py`:
- No role table/relationship
- `created_by` and `updated_by` are top-level columns
- `metadata_` maps to DB column `metadata` (JSONB)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.user import User

logger = logging.getLogger(__name__)
ph = PasswordHasher()


def _json_text(col, key: str):
    """
    Cross-version compatible helper to read JSONB key as text.
    Uses PostgreSQL JSON operator: metadata ->> 'key'

    Args:
        col: SQLAlchemy column representing a JSONB field.
        key (str): The JSON key to extract.

    Returns:
        SQLAlchemy expression for the JSON text extraction.
    """
    return col.op("->>")(key)


async def count_users(db: AsyncSession) -> int:
    """
    Return total users count.

    Args:
        db (AsyncSession): Database session.

    Returns:
        int: Total number of users.
    """
    result = await db.execute(select(func.count()).select_from(User))
    return int(result.scalar() or 0)


async def count_users_by_created_by(db: AsyncSession, created_by: str) -> int:
    """
    Count users created by a specific identifier.

    Args:
        db (AsyncSession): Database session.
        created_by (str): Identifier of the creator.

    Returns:
        int: Number of users created by the specified identifier.
    """
    result = await db.execute(
        select(func.count()).select_from(User).where(User.created_by == created_by)
    )
    return int(result.scalar() or 0)


async def get_admin_users_count(db: AsyncSession) -> int:
    """
    Count users where metadata_['user_type'] == 'Admin' (case-insensitive).

    Args:
        db (AsyncSession): Database session.

    Returns:
        int: Number of admin users.
    """
    query = (
        select(func.count())
        .select_from(User)
        .where(User.metadata_['user_type'].astext == 'Admin')
    )
    result = await db.execute(query)
    return int(result.scalar() or 0)


async def get_user(db: AsyncSession, user_id: UUID) -> Optional[User]:
    """Retrieve a User by id.

    Args:
        db (AsyncSession): Database session.
        user_id (UUID): The UUID of the user to retrieve.

    Returns:
        Optional[User]: The User object if found, else None.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_identifier(
    db: AsyncSession,
    identifier: str,
    *,
    curr_user_ident: Optional[str] = None,
) -> Optional[User]:
    """
    Retrieve a User by identifier.

    If `curr_user_ident` is provided and not 'admin', limits visibility to:
    - users created_by == curr_user_ident, OR
    - the user is the current user (identifier == curr_user_ident)

    Args:
        db (AsyncSession): Database session.
        identifier (str): The identifier of the user to retrieve.
        curr_user_ident (Optional[str]): The identifier of the current user.
    Returns:
        Optional[User]: The User object if found, else None.
    """
    query = select(User).where(User.identifier == identifier)

    if curr_user_ident is not None and curr_user_ident.lower() != "admin":
        query = query.where(
            (User.created_by == curr_user_ident) | (User.identifier == curr_user_ident)
        )

    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_users(
    db: AsyncSession,
    *,
    start: int = 0,
    limit: int = 100,
    curr_user_ident: Optional[str] = None,
) -> List[User]:
    """
    Retrieve paginated users list.

    If `curr_user_ident` is provided and not 'admin', limits list to:
    - users created_by == curr_user_ident, OR
    - the current user (identifier == curr_user_ident)

    Args:
        db (AsyncSession): Database session.
        start (int): Offset for pagination.
        limit (int): Maximum number of users to return.
        curr_user_ident (Optional[str]): The identifier of the current user.

    Returns:
        List[User]: List of User objects.
    """
    query = (
        select(User)
        .order_by(User.first_name.asc(), User.last_name.asc())
        .offset(start)
        .limit(limit)
    )

    if curr_user_ident is not None and curr_user_ident.lower() != "admin":
        query = query.where(
            (User.created_by == curr_user_ident) | (User.identifier == curr_user_ident)
        )

    result = await db.execute(query)
    return list(result.scalars().all())


async def create_user(db: AsyncSession, user_data: Dict[str, Any]) -> User:
    """
    Create a user.

    Expected keys in user_data:
    - identifier, password, first_name, last_name
    - metadata_ (dict, optional)
    - created_by (str)
    - updated_by (str, optional)

    Args:
        db (AsyncSession): Database session.
        user_data (Dict[str, Any]): Data for the new user.

    Returns:
        User: The created User object.
    """
    identifier = user_data["identifier"]
    logger.info("Creating new user: %s", identifier)

    metadata = dict(user_data.get("metadata_") or {})
    metadata.setdefault("needs_password_reset", True)

    hashed_password = ph.hash(user_data["password"])

    db_user = User(
        identifier=identifier,
        password=hashed_password,
        first_name=user_data["first_name"],
        last_name=user_data["last_name"],
        metadata_=metadata,
        created_by=user_data["created_by"],
        updated_by=user_data.get("updated_by"),
    )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def update_user(
    db: AsyncSession,
    target_user: User,
    updates: Dict[str, Any],
    *,
    curr_user_ident: Optional[str] = None,
) -> Optional[User]:
    """
    Update user fields (not password).

    Rules:
    - `created_by` is immutable
    - `needs_password_reset` should usually be controlled by password flows

    Args:
        db (AsyncSession): Database session.
        target_user (User): The user to update.
        updates (Dict[str, Any]): Fields to update.
        curr_user_ident (Optional[str]): The identifier of the current user.

    Returns:
        Optional[User]: The updated User object if found, else None.
    """
    logger.info("Updating user: %s", target_user.identifier)

    user = await get_user_by_identifier(
        db, target_user.identifier, curr_user_ident=curr_user_ident
    )
    if not user:
        return None

    if "first_name" in updates and updates["first_name"] is not None:
        user.first_name = updates["first_name"]

    if "last_name" in updates and updates["last_name"] is not None:
        user.last_name = updates["last_name"]

    if "updated_by" in updates and updates["updated_by"] is not None:
        user.updated_by = updates["updated_by"]

    if "metadata_" in updates and updates["metadata_"] is not None:
        existing = dict(user.metadata_ or {})
        incoming: Dict[str, Any] = dict(updates["metadata_"] or {})

        # protect reserved keys
        incoming.pop("created_by", None)
        incoming.pop("needs_password_reset", None)

        existing.update(incoming)

        # keep reserved keys as-is if they exist
        if user.metadata_ and "needs_password_reset" in user.metadata_:
            existing["needs_password_reset"] = user.metadata_["needs_password_reset"]

        user.metadata_ = existing

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user_password(
    db: AsyncSession,
    target_user: User,
    new_password: str,
    *,
    curr_user_ident: Optional[str] = None,
    needs_password_reset: bool = False,
    updated_by: Optional[str] = None,
) -> Optional[User]:
    """
    Update password and manage metadata_.needs_password_reset.

    Args:
        db (AsyncSession): Database session.
        target_user (User): The user whose password is to be updated.
        new_password (str): The new password.
        curr_user_ident (Optional[str]): The identifier of the current user.
        needs_password_reset (bool): Flag to set in metadata_.
        updated_by (Optional[str]): Identifier of the last updater.

    Returns:
        Optional[User]: The updated User object if found, else None.
    """
    logger.info("Updating password for user: %s", target_user.identifier)

    user = await get_user_by_identifier(
        db, target_user.identifier, curr_user_ident=curr_user_ident
    )
    if not user:
        return None

    user.password = ph.hash(new_password)
    if updated_by is not None:
        user.updated_by = updated_by

    metadata = dict(user.metadata_ or {})
    metadata["needs_password_reset"] = bool(needs_password_reset)
    user.metadata_ = metadata

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(
    db: AsyncSession,
    target_user: User,
    *,
    curr_user_ident: Optional[str] = None,
) -> bool:
    """
    Delete a user. Returns True if deleted, False if not accessible/found.

    Args:
        db (AsyncSession): Database session.
        target_user (User): The user to delete.
        curr_user_ident (Optional[str]): The identifier of the current user.

    Returns:
        bool: True if the user was deleted, False otherwise.
    """
    logger.info("Deleting user: %s", target_user.identifier)

    user = await get_user_by_identifier(
        db, target_user.identifier, curr_user_ident=curr_user_ident
    )
    if not user:
        return False

    await db.delete(user)
    await db.commit()
    return True

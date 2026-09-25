"""Persistence for users (Argon2-hashed passwords, typed access columns)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.user import ADMIN, USER, User, parse_user_type

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

# Access is stored in columns; these keys are never kept in the metadata JSON.
_COLUMN_METADATA_KEYS = frozenset({"user_type"})
# Only the password endpoints may change these.
_PROTECTED_METADATA_KEYS = frozenset({"created_by", "needs_password_reset"})


@dataclass(frozen=True)
class Viewer:
    """Who is asking: superusers see every user, other admins their own."""

    user_id: UUID | None
    is_superuser: bool


def _visible_to(query, viewer: Viewer | None):
    if viewer is None or viewer.is_superuser:
        return query
    if viewer.user_id is None:
        return query.where(false())
    return query.where(or_(User.created_by_id == viewer.user_id, User.id == viewer.user_id))


def _split_metadata(metadata: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    """Separate the access column value from free-form profile metadata."""

    data = dict(metadata or {})
    user_type = parse_user_type(data.get("user_type")) if "user_type" in data else None
    for key in _COLUMN_METADATA_KEYS:
        data.pop(key, None)
    return user_type, data


async def lock_admin_ids(db: AsyncSession) -> list[UUID]:
    """Ids of every administrator, row-locked until the transaction ends.

    Demoting or deleting an admin checks this count; the lock stops two
    concurrent requests from each removing "one of two" admins.
    """

    result = await db.execute(
        select(User.id).where(User.user_type == ADMIN).with_for_update()
    )
    return list(result.scalars().all())


async def get_user_by_identifier(
    db: AsyncSession,
    identifier: str,
    *,
    viewer: Viewer | None = None,
) -> User | None:
    """Return a user, restricted to what ``viewer`` may see when given."""

    query = _visible_to(select(User).where(User.identifier == identifier), viewer)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_users(
    db: AsyncSession,
    *,
    start: int = 0,
    limit: int = 100,
    viewer: Viewer | None = None,
) -> list[User]:
    """Page of users visible to ``viewer``, ordered by name."""

    query = (
        select(User)
        .order_by(User.first_name.asc(), User.last_name.asc(), User.identifier.asc())
        .offset(start)
        .limit(limit)
    )
    result = await db.execute(_visible_to(query, viewer))
    return list(result.scalars().all())


async def create_user(db: AsyncSession, user_data: dict[str, Any]) -> User:
    """Insert a user; new accounts must change their password on first login."""

    user_type, metadata = _split_metadata(user_data.get("metadata_"))
    metadata.setdefault("needs_password_reset", True)
    user = User(
        identifier=user_data["identifier"],
        password=_hasher.hash(user_data["password"]),
        first_name=user_data["first_name"],
        last_name=user_data["last_name"],
        user_type=user_type or USER,
        metadata_=metadata,
        created_by=user_data["created_by"],
        created_by_id=user_data.get("created_by_id"),
        updated_by=user_data.get("updated_by"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user: User, updates: dict[str, Any]) -> User:
    """Update names, user type, and profile metadata.

    ``created_by`` and ``needs_password_reset`` are protected. The caller has
    already checked visibility and the last-admin rule (holding the admin lock).
    """

    for field in ("first_name", "last_name", "updated_by"):
        if updates.get(field) is not None:
            setattr(user, field, updates[field])
    incoming = updates.get("metadata_")
    if incoming is not None:
        user_type, profile = _split_metadata(incoming)
        if user_type is not None:
            user.user_type = user_type
            if user_type != ADMIN:
                user.is_superuser = False
        merged = dict(user.metadata_ or {})
        merged.update({k: v for k, v in profile.items() if k not in _PROTECTED_METADATA_KEYS})
        user.metadata_ = merged
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user_password(
    db: AsyncSession,
    target_user: User,
    new_password: str,
    *,
    needs_password_reset: bool = False,
    updated_by: str | None = None,
) -> User:
    """Replace the password hash and sign out every existing session of the user.

    Bumping ``token_version`` invalidates all tokens issued before the change.
    """

    target_user.password = _hasher.hash(new_password)
    target_user.token_version = (target_user.token_version or 0) + 1
    if updated_by is not None:
        target_user.updated_by = updated_by
    metadata = dict(target_user.metadata_ or {})
    metadata["needs_password_reset"] = bool(needs_password_reset)
    target_user.metadata_ = metadata
    db.add(target_user)
    await db.commit()
    await db.refresh(target_user)
    return target_user


async def delete_user(db: AsyncSession, target_user: User) -> bool:
    """Delete a user row (users they created keep existing, unowned)."""

    await db.delete(target_user)
    await db.commit()
    return True

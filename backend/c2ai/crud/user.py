"""Persistence for users (Argon2-hashed passwords, JSONB metadata)."""

from __future__ import annotations

import logging
from typing import Any

from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.user import User

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

# The bootstrap account sees every user; other admins see those they created.
_SUPERUSER_IDENTIFIER = "admin"


def _visible_to(query, curr_user_ident: str | None):
    if curr_user_ident is None or curr_user_ident.lower() == _SUPERUSER_IDENTIFIER:
        return query
    return query.where(
        (User.created_by == curr_user_ident) | (User.identifier == curr_user_ident)
    )


async def get_admin_users_count(db: AsyncSession) -> int:
    """Number of users whose ``metadata.user_type`` is Admin (case-insensitive)."""

    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(func.lower(User.metadata_["user_type"].astext) == "admin")
    )
    return int(result.scalar() or 0)


async def get_user_by_identifier(
    db: AsyncSession,
    identifier: str,
    *,
    curr_user_ident: str | None = None,
) -> User | None:
    """Return a user, restricted to what ``curr_user_ident`` may see when given."""

    query = _visible_to(select(User).where(User.identifier == identifier), curr_user_ident)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_users(
    db: AsyncSession,
    *,
    start: int = 0,
    limit: int = 100,
    curr_user_ident: str | None = None,
) -> list[User]:
    """Page of users visible to ``curr_user_ident``, ordered by name."""

    query = (
        select(User)
        .order_by(User.first_name.asc(), User.last_name.asc(), User.identifier.asc())
        .offset(start)
        .limit(limit)
    )
    result = await db.execute(_visible_to(query, curr_user_ident))
    return list(result.scalars().all())


async def create_user(db: AsyncSession, user_data: dict[str, Any]) -> User:
    """Insert a user; new accounts must change their password on first login."""

    metadata = dict(user_data.get("metadata_") or {})
    metadata.setdefault("needs_password_reset", True)
    user = User(
        identifier=user_data["identifier"],
        password=_hasher.hash(user_data["password"]),
        first_name=user_data["first_name"],
        last_name=user_data["last_name"],
        metadata_=metadata,
        created_by=user_data["created_by"],
        updated_by=user_data.get("updated_by"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(
    db: AsyncSession,
    target_user: User,
    updates: dict[str, Any],
    *,
    curr_user_ident: str | None = None,
) -> User | None:
    """Update names/metadata. ``created_by`` and ``needs_password_reset`` are protected."""

    user = await get_user_by_identifier(
        db, target_user.identifier, curr_user_ident=curr_user_ident
    )
    if not user:
        return None
    for field in ("first_name", "last_name", "updated_by"):
        if updates.get(field) is not None:
            setattr(user, field, updates[field])
    incoming = updates.get("metadata_")
    if incoming is not None:
        merged = dict(user.metadata_ or {})
        protected = {"created_by", "needs_password_reset"}
        merged.update({k: v for k, v in dict(incoming).items() if k not in protected})
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
    """Replace the password hash and record whether a reset is still required."""

    target_user.password = _hasher.hash(new_password)
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
    """Delete a user row."""

    await db.delete(target_user)
    await db.commit()
    return True

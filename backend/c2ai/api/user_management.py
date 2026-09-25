"""``/api/users``: the Users page (Amberd Agents' user management).

Admins only. The rules live in ``c2ai.services.users``; this module shapes
them for the page. A temporary password is in a response only when it could
not be emailed - it exists in the clear for that one moment, and the
alternative to showing it is an account nobody can get into.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.cookie import set_auth_cookie
from c2ai.auth.jwt import (
    AthenaTokenUser,
    default_token_ttl_seconds,
    issue_session_token,
    require_admin,
)
from c2ai.config import get_settings
from c2ai.db.session import get_db_session
from c2ai.models.user import ADMIN, User
from c2ai.services import users as people

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


class PersonIn(BaseModel):
    first_name: str = Field("", max_length=200)
    last_name: str = Field("", max_length=200)
    email: str = Field("", max_length=320)
    role: str = Field("user", max_length=16)


class Person(BaseModel):
    user_id: UUID
    email: str
    first_name: str
    last_name: str
    name: str
    role: Literal["admin", "user"]
    role_label: str
    must_change_password: bool
    status: Literal["invited", "active"]
    created_at: datetime | None = None


class IssuedPassword(Person):
    email_sent: bool
    email_error: str = ""
    # Only when the email did not go.
    temporary_password: str = ""


class RoleOption(BaseModel):
    value: str
    label: str


class People(BaseModel):
    users: list[Person]
    roles: list[RoleOption]
    admins: int
    # The page says so, rather than surprising anyone with a password later.
    email_configured: bool


class Removed(BaseModel):
    deleted: UUID
    email: str


def _person(user: User) -> Person:
    waiting = (user.metadata_ or {}).get("needs_password_reset") is True
    return Person(
        user_id=user.id,
        email=user.identifier,
        first_name=user.first_name,
        last_name=user.last_name,
        name=people.display_name(user),
        role=user.user_type,
        role_label=people.ROLE_LABELS.get(user.user_type, user.user_type),
        must_change_password=waiting,
        status="invited" if waiting else "active",
        created_at=user.createdAt,
    )


async def _issued(user: User, password: str, *, reset: bool) -> IssuedPassword:
    why = await people.send_invitation(user, password, reset=reset)
    return IssuedPassword(
        **_person(user).model_dump(),
        email_sent=not why,
        email_error=why,
        temporary_password=password if why else "",
    )


@router.get("", response_model=People)
async def list_users(
    _admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> People:
    everyone = await people.list_people(db)
    return People(
        users=[_person(user) for user in everyone],
        roles=[RoleOption(value=r, label=people.ROLE_LABELS[r]) for r in people.ROLES],
        admins=sum(1 for user in everyone if user.user_type == ADMIN),
        email_configured=get_settings().email_enabled,
    )


@router.post("", response_model=IssuedPassword, status_code=status.HTTP_201_CREATED)
async def add_user(
    body: PersonIn,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> IssuedPassword:
    """Add someone and email them a temporary password."""

    user, password = await people.add_person(
        db,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        role=body.role,
        added_by=admin.identifier,
        added_by_id=admin.user_id,
    )
    return await _issued(user, password, reset=False)


@router.put("/{user_id}", response_model=Person)
async def update_user(
    user_id: UUID,
    body: PersonIn,
    response: Response,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> Person:
    """Change someone's name, address or role (effective at once)."""

    user = await people.update_person(
        db,
        user_id,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        role=body.role,
        updated_by=admin.identifier,
    )
    # A session names the address it was issued for; an Admin who changes
    # their own gets a fresh one rather than being signed out mid-edit.
    if user.id == admin.user_id and user.identifier != admin.identifier:
        ttl_seconds = default_token_ttl_seconds()
        set_auth_cookie(
            response, issue_session_token(user, ttl_seconds=ttl_seconds), max_age=ttl_seconds
        )
    return _person(user)


@router.post("/{user_id}/reset-password", response_model=IssuedPassword)
async def reset_user_password(
    user_id: UUID,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> IssuedPassword:
    """Issue a new temporary password; the current one stops working at once."""

    user, password = await people.reset_person(db, user_id, reset_by=admin.identifier)
    return await _issued(user, password, reset=True)


@router.delete("/{user_id}", response_model=Removed)
async def remove_user(
    user_id: UUID,
    admin: AthenaTokenUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> Removed:
    """Remove someone. Their access ends at once."""

    user = await people.remove_person(
        db, user_id, removed_by=admin.identifier, removed_by_id=admin.user_id
    )
    return Removed(deleted=user.id, email=user.identifier)

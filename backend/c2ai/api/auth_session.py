"""Session endpoints: login, logout, and the current-user lookup the UI polls."""

from __future__ import annotations

import logging
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.cookie import clear_auth_cookie, set_auth_cookie
from c2ai.auth.jwt import (
    AthenaTokenUser,
    clamp_token_ttl,
    create_jwt,
    default_token_ttl_seconds,
    get_current_user_token,
)
from c2ai.core.exceptions import InvalidCredentials
from c2ai.crud.user import get_user_by_identifier
from c2ai.db.session import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

_hasher = PasswordHasher()
# Verified against when the identifier is unknown, so both failure paths cost
# the same Argon2 work and response timing does not reveal valid usernames.
_DUMMY_HASH = _hasher.hash("c2ai-timing-equaliser")


class LoginRequest(BaseModel):
    """Credentials plus optional session knobs (the UI sends neither knob)."""

    identifier: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=1024)
    expires_in_seconds: int | None = Field(default=None, ge=1)
    set_cookie: bool = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    detail: str = "logged_out"


class WhoAmIResponse(BaseModel):
    identifier: str
    service: str | None = None
    metadata: dict = Field(default_factory=dict)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Verify credentials and issue a JWT (also set as an HttpOnly cookie)."""

    identifier = payload.identifier.strip()
    user = await get_user_by_identifier(db, identifier)
    if user is None:
        _verify(_DUMMY_HASH, payload.password)
        logger.info("Login rejected for unknown identifier '%s'.", identifier)
        raise InvalidCredentials()
    if not _verify(user.password, payload.password):
        logger.info("Login rejected for '%s': wrong password.", identifier)
        raise InvalidCredentials()

    ttl_seconds = (
        clamp_token_ttl(payload.expires_in_seconds)
        if payload.expires_in_seconds is not None
        else default_token_ttl_seconds()
    )
    token = create_jwt(
        payload={
            "identifier": user.identifier,
            "service": "athena",
            "email": None,
            "metadata": dict(user.metadata_ or {}),
            "tz_location": "UTC",
        },
        expires_in=timedelta(seconds=ttl_seconds),
    )
    if payload.set_cookie:
        set_auth_cookie(response, token, max_age=ttl_seconds)
    logger.info("Login succeeded for '%s'.", user.identifier)
    return LoginResponse(access_token=token)


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    """Delete the auth cookie."""

    clear_auth_cookie(response)
    return LogoutResponse()


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(user: AthenaTokenUser = Depends(get_current_user_token)) -> WhoAmIResponse:
    """The signed-in user with their current (database) metadata."""

    return WhoAmIResponse(identifier=user.identifier, service=user.service, metadata=user.metadata)

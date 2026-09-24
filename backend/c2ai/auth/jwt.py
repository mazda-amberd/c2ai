"""JWT issuing/validation and the FastAPI authentication dependencies.

Tokens carry the standard ``iat``/``exp`` claims plus Athena's legacy
``created``/``expired`` strings (``DD-MM-YYYY_HH:MM:SS``, UTC) so tokens issued
by older Athena builds keep validating until they expire.

A valid signature is necessary but not sufficient: every authenticated request
re-reads the user from the database, so deleting a user or removing their
admin rights takes effect immediately instead of when the token expires.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import jwt as pyjwt
from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from c2ai.auth.cookie import AUTH_COOKIE_NAME, get_token_from_cookie_or_header
from c2ai.core.exceptions import (
    AdminPrivilegesRequired,
    InvalidToken,
    InvalidTokenExpirationFormat,
    MissingToken,
    TokenExpired,
)
from c2ai.crud.user import get_user_by_identifier
from c2ai.db.session import get_db_session

logger = logging.getLogger(__name__)

ATHENA_DATETIME_FMT = "%d-%m-%Y_%H:%M:%S"
DEFAULT_ALGORITHM = "HS256"

_DEFAULT_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_MIN_TOKEN_TTL_SECONDS = 60


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def default_token_ttl_seconds() -> int:
    """Lifetime used when a login request does not ask for one."""

    return clamp_token_ttl(_env_int("ATHENA_TOKEN_TTL_SECONDS", _DEFAULT_TOKEN_TTL_SECONDS))


def clamp_token_ttl(requested_seconds: int) -> int:
    """Bound a requested lifetime by ``ATHENA_TOKEN_MAX_TTL_SECONDS``."""

    maximum = max(
        _MIN_TOKEN_TTL_SECONDS,
        _env_int("ATHENA_TOKEN_MAX_TTL_SECONDS", _MAX_TOKEN_TTL_SECONDS),
    )
    return max(_MIN_TOKEN_TTL_SECONDS, min(requested_seconds, maximum))


def get_jwt_secret() -> str:
    """Return the signing secret (``ATHENA_AUTH_SECRET``, or legacy ``JWT_SECRET``)."""

    secret = os.environ.get("ATHENA_AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT secret is not configured. Set ATHENA_AUTH_SECRET.")
    return secret


def parse_athena_datetime(value: str) -> datetime:
    """Parse the legacy ``DD-MM-YYYY_HH:MM:SS`` string as a UTC datetime."""

    return datetime.strptime(value, ATHENA_DATETIME_FMT).replace(tzinfo=timezone.utc)


def format_athena_datetime(dt: datetime) -> str:
    """Format a datetime (naive values are treated as UTC) in the legacy format."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ATHENA_DATETIME_FMT)


def create_jwt(
    *,
    payload: Mapping[str, Any],
    expires_in: timedelta,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Sign ``payload`` with standard and legacy issue/expiry claims."""

    now = datetime.now(timezone.utc)
    expires_at = now + expires_in
    claims: dict[str, Any] = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "created": format_athena_datetime(now),
        "expired": format_athena_datetime(expires_at),
    }
    return pyjwt.encode(claims, get_jwt_secret(), algorithm=algorithm)


class AthenaTokenUser(BaseModel):
    """The authenticated caller as seen by route handlers."""

    identifier: str
    service: str | None = None
    email: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tz_location: str | None = None
    created: str | None = None
    expired: str | None = None

    @property
    def is_admin(self) -> bool:
        return metadata_is_admin(self.metadata)


def metadata_is_admin(metadata: Mapping[str, Any] | None) -> bool:
    """Admin rights come from ``user_type`` (or the legacy ``role``) being Admin."""

    meta = metadata or {}
    user_type = str(meta.get("user_type") or "").strip().lower()
    role = str(meta.get("role") or "").strip().lower()
    return user_type == "admin" or role == "admin"


def decode_jwt(token: str, *, algorithms: list[str] | None = None) -> AthenaTokenUser:
    """Verify the signature and expiry of ``token`` and return its claims."""

    if not token:
        raise MissingToken()
    try:
        decoded: dict[str, Any] = pyjwt.decode(
            token,
            get_jwt_secret(),
            algorithms=algorithms or [DEFAULT_ALGORITHM],
            options={"verify_signature": True, "verify_exp": True},
        )
    except pyjwt.ExpiredSignatureError as error:
        raise TokenExpired() from error
    except pyjwt.InvalidTokenError as error:
        raise InvalidToken() from error

    # Tokens from older Athena builds carry only the legacy expiry string.
    if "exp" not in decoded:
        expired = decoded.get("expired")
        if not isinstance(expired, str):
            raise InvalidToken("Token has no expiry")
        try:
            expires_at = parse_athena_datetime(expired)
        except ValueError as error:
            raise InvalidTokenExpirationFormat() from error
        if datetime.now(timezone.utc) >= expires_at:
            raise TokenExpired()

    return AthenaTokenUser.model_validate(decoded)


async def get_access_token(request: Request) -> str:
    """The raw bearer token from the auth cookie or ``Authorization`` header."""

    token = get_token_from_cookie_or_header(request, cookie_name=AUTH_COOKIE_NAME)
    if not token:
        raise MissingToken()
    return token


async def _resolve_current_user(request: Request, db: AsyncSession) -> AthenaTokenUser:
    token = await get_access_token(request)
    claims = decode_jwt(token)
    user = await get_user_by_identifier(db, claims.identifier)
    if user is None:
        raise InvalidToken("The account for this token no longer exists")
    # Authorization decisions use the stored metadata, not the token snapshot.
    return claims.model_copy(update={"metadata": dict(user.metadata_ or {})})


async def get_current_user_token(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AthenaTokenUser:
    """Dependency: any authenticated, still-existing user."""

    return await _resolve_current_user(request, db)


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AthenaTokenUser:
    """Dependency: an authenticated user whose stored account is an Admin."""

    user = await _resolve_current_user(request, db)
    if not user.is_admin:
        raise AdminPrivilegesRequired()
    return user

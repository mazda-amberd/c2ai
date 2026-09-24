# pylint: disable=import-error
"""JWT helpers for Athena.

This module is intentionally small and dependency-light:
- Uses PyJWT (already in pyproject).
- Supports *both* standard JWT expiration via `exp` claim and the
  non-standard string fields you showed: `created` and `expired`.

Expected payload shape (example):
{
  "identifier": "admin",
  "service": "athena",
  "email": null,
  "metadata": {"role": "Executive", ...},
  "tz_location": "Asia/Yerevan",
  "created": "15-01-2026_15:40:42",
  "expired": "14-02-2026_15:40:42"
}
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

import jwt as pyjwt
from fastapi import APIRouter, Response, Depends
from pydantic import BaseModel, Field
from starlette.requests import Request

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.cookie import (
    get_token_from_cookie_or_header,
    set_auth_cookie,
    clear_auth_cookie,
)
from c2ai.crud.user import get_user_by_identifier
from c2ai.db.session import get_db_session as db_session
from c2ai.core.exceptions import (
    AdminPrivilegesRequired,
    InvalidCredentials,
    InvalidToken,
    InvalidTokenExpirationFormat,
    MissingToken,
    TokenExpired,
)

logger = logging.getLogger(__name__)

# Argon2 password hasher for verifying DB-stored hashes
ph = PasswordHasher()

# Matches your example: "15-01-2026_15:40:42"
ATHENA_DATETIME_FMT = "%d-%m-%Y_%H:%M:%S"
DEFAULT_ALGORITHM = "HS256"


def get_jwt_secret() -> str:
    """Return the secret used to sign/verify JWTs.

    Env var choice is kept generic for Athena; adjust as you like.

    Returns:
        str: JWT secret key.

    Raises:
        RuntimeError: If the JWT secret is not configured.
    """

    secret = os.environ.get("ATHENA_AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "JWT secret is not configured. Set ATHENA_AUTH_SECRET (or JWT_SECRET)."
        )
    return secret


def parse_athena_datetime(value: str) -> datetime:
    """Parse Athena's custom datetime string into a timezone-aware datetime (UTC).

    The token fields `created`/`expired` don't include tz offset info.
    We treat them as UTC to make validation deterministic.

    Args:
        value (str): Athena datetime string.

    Returns:
        datetime: Timezone-aware datetime in UTC.

    Raises:
        ValueError: If the input string is not in the expected format.
    """

    dt = datetime.strptime(value, ATHENA_DATETIME_FMT)
    return dt.replace(tzinfo=timezone.utc)


def format_athena_datetime(dt: datetime) -> str:
    """
    Format to Athena's custom datetime string.

    Args:
        dt (datetime): Timezone-aware datetime.

    Returns:
        str: Formatted Athena datetime string.

    Raises:
        ValueError: If the input datetime is naive (no tzinfo).
    """

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime(ATHENA_DATETIME_FMT)


def create_jwt(
    *,
    payload: Mapping[str, Any],
    expires_in: Optional[timedelta] = timedelta(hours=1),
    algorithm: str = DEFAULT_ALGORITHM,
    include_standard_claims: bool = False,
) -> str:
    """Create and sign a JWT in Athena's expected format.

    Your target token format uses custom string timestamps:
      - created: "DD-MM-YYYY_HH:MM:SS"
      - expired: "DD-MM-YYYY_HH:MM:SS"

    By default we DO NOT include standard JWT claims (`iat`, `exp`) so the
    encoded payload matches the format you shared.

    If you need interop with libraries expecting `exp`/`iat`, set
    `include_standard_claims=True`.

    Args:
        payload (Mapping[str, Any]): Payload data to include in the JWT.
        expires_in (Optional[timedelta]): Token lifetime. If None, token does
                                          not expire.
        algorithm (str): Signing algorithm to use.
        include_standard_claims (bool): If True, include standard `iat` and `exp
            claims in addition to Athena's custom fields.

    Returns:
        str: Encoded JWT as a string.
    """
    now = datetime.now(timezone.utc)

    to_encode: Dict[str, Any] = dict(payload)

    if expires_in is not None:
        exp_dt = now + expires_in

        # Non-standard fields required by your format
        to_encode["created"] = to_encode.get("created") or format_athena_datetime(now)
        to_encode["expired"] = to_encode.get("expired") or format_athena_datetime(exp_dt)

        if include_standard_claims:
            to_encode.setdefault("iat", int(now.timestamp()))
            to_encode.setdefault("exp", int(exp_dt.timestamp()))

    secret = get_jwt_secret()
    return pyjwt.encode(to_encode, secret, algorithm=algorithm)


class AthenaTokenUser(BaseModel):
    """
    Minimal user model extracted from JWT, matching existing code usage.

    Attributes:
        identifier (str): Unique identifier for the user.
        service (Optional[str]): Service name (e.g., "athena").
        email (Optional[str]): User's email address.
        metadata (Dict[str, Any]): Additional metadata (role, user_type, etc.).
        tz_location (Optional[str]): Timezone location string.
        created (Optional[str]): Token creation timestamp in Athena format.
        expired (Optional[str]): Token expiration timestamp in Athena format.
    """

    identifier: str
    service: Optional[str] = None
    email: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tz_location: Optional[str] = None
    created: Optional[str] = None
    expired: Optional[str] = None


class LoginRequest(BaseModel):
    """Login request body (for Postman).

    Expected body:
        {"identifier": "admin", "password": "admin"}

    Notes:
        - `expires_in_seconds` and `set_cookie` are optional convenience knobs.
        - Token payload metadata is sourced from the DB record.
    """

    identifier: str
    password: str

    expires_in_seconds: int = 30 * 24 * 60 * 60
    set_cookie: bool = True


class LoginResponse(BaseModel):
    """
    Login response body.

    Attributes:
        access_token (str): The generated JWT access token.
        token_type (str): The type of the token (default
                          is "bearer").
    """
    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    detail: str = "logged_out"


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(db_session),
) -> LoginResponse:
    """Authenticate identifier+password against DB and issue JWT."""

    ident = payload.identifier.strip()
    logger.info("Login request for identifier: '%s'.", ident)

    user = await get_user_by_identifier(db=db, identifier=ident)
    if not user:
        raise InvalidCredentials()

    try:
        ph.verify(user.password, payload.password)
    except VerifyMismatchError as e:
        raise InvalidCredentials() from e

    metadata = dict(user.metadata_ or {})

    token = create_jwt(
        payload={
            "identifier": user.identifier,
            "service": "athena",
            "email": None,
            "metadata": metadata,
            "tz_location": "UTC",
        },
        expires_in=timedelta(seconds=max(1, payload.expires_in_seconds)),
    )

    if payload.set_cookie:
        set_auth_cookie(response, token)

    return LoginResponse(access_token=token)


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    """
    Logout by deleting the auth cookie (`access_token`).
    """
    clear_auth_cookie(response, cookie_name="access_token")
    return LogoutResponse()


def decode_jwt(
    token: str,
    *,
    algorithms: Optional[list[str]] = None,
    verify_exp: bool = True,
) -> AthenaTokenUser:
    """Decode and verify a JWT.

    Verification rules:
    - Signature is always verified.
    - Expiration is validated using:
        1) standard `exp` claim if present AND verify_exp=True
        2) otherwise, Athena's custom `expired` string if present

    Args:
        token (str): JWT token string.
        algorithms (Optional[list[str]]): List of acceptable algorithms.

        verify_exp (bool): Whether to enforce expiration validation.

    Returns:
        AthenaTokenUser: Decoded user information.

    Raises:
        HTTPException: If the token is missing, invalid, or expired.
    """

    if not token:
        raise MissingToken()

    try:
        decoded: Dict[str, Any] = pyjwt.decode(
            token,
            get_jwt_secret(),
            algorithms=algorithms or [DEFAULT_ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": bool(verify_exp),
            },
        )
    except pyjwt.ExpiredSignatureError as e:
        raise TokenExpired() from e
    except pyjwt.InvalidTokenError as e:
        raise InvalidToken() from e

    if verify_exp and "exp" not in decoded and isinstance(decoded.get("expired"), str):
        try:
            expires_at = parse_athena_datetime(decoded["expired"])
        except ValueError as e:
            raise InvalidTokenExpirationFormat() from e

        if datetime.now(timezone.utc) >= expires_at:
            raise TokenExpired()

    return AthenaTokenUser.model_validate(decoded)


def try_get_claim(payload: Mapping[str, Any], key: str) -> Optional[Any]:
    """
    Small helper for optional claim access.

    Args:
        payload (Mapping[str, Any]): Decoded JWT payload.
        key (str): Claim key to retrieve.

    Returns:
        Optional[Any]: Claim value or None if not present.
    """

    return payload.get(key)


def require_admin(request: Request) -> AthenaTokenUser:
    """Require a valid JWT and admin privileges.

    Used as `dependencies=[Depends(require_admin)]`.

    This function intentionally injects `Request` directly to avoid FastAPI
    treating `request` as a query parameter (the issue you were seeing).

    Args:
        request (Request): FastAPI request object.

    Returns:
        AthenaTokenUser: Decoded user information.

    Raises:
        HTTPException: If authentication fails or user is not admin.
    """

    token = get_token_from_cookie_or_header(
        request,
        cookie_name="access_token",
        auto_error=True,
    )
    if not token:
        raise MissingToken()

    user = decode_jwt(token)

    meta = user.metadata or {}
    user_type = str(meta.get("user_type") or "").strip().lower()
    role = str(meta.get("role") or "").strip().lower()

    is_admin = (user_type == "admin") or (role == "admin")

    # Allow a common bootstrap token with empty metadata for the built-in admin
    if not is_admin and (not meta) and user.identifier.strip().lower() == "admin":
        is_admin = True

    if not is_admin:
        raise AdminPrivilegesRequired()

    return user


async def get_access_token(request: Request) -> str:
    token = get_token_from_cookie_or_header(
        request,
        cookie_name="access_token",
        auto_error=True,
    )
    if not token:
        raise MissingToken()
    return token


def get_current_user_token(request: Request) -> "AthenaTokenUser":
    """
    FastAPI dependency that decodes the JWT and returns the parsed AthenaTokenUser.

    Use this instead of ``get_access_token`` when you need the caller's identity
    (e.g. to record ``triggered_by`` on a pipeline run).
    """
    token = get_token_from_cookie_or_header(
        request,
        cookie_name="access_token",
        auto_error=True,
    )
    if not token:
        raise MissingToken()
    return decode_jwt(token)

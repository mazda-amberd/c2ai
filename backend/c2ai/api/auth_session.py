"""Session endpoints: login, logout, forgot password, and the current-user lookup."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.cookie import (
    clear_auth_cookie,
    get_token_from_cookie_or_header,
    set_auth_cookie,
)
from c2ai.auth.jwt import (
    AthenaTokenUser,
    clamp_token_ttl,
    decode_jwt,
    default_token_ttl_seconds,
    get_user_on_any_password,
    issue_session_token,
)
from c2ai.config import get_settings
from c2ai.core.exceptions import AppException, InvalidCredentials, TooManyLoginAttempts
from c2ai.crud import session as session_store
from c2ai.crud.user import get_user_for_sign_in
from c2ai.db.session import get_db_session
from c2ai.jobs import JobStore, get_job_notifier, get_job_store
from c2ai.jobs.handlers.password_reset import (
    KIND as PASSWORD_RESET_JOB,
    RETENTION as PASSWORD_RESET_RETENTION,
)
from c2ai.services.password_reset import ANSWER as FORGOT_PASSWORD_ANSWER

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
    first_name: str | None = None
    last_name: str | None = None
    metadata: dict = Field(default_factory=dict)


class ForgotPasswordRequest(BaseModel):
    email: str = Field("", max_length=320)


class ForgotPasswordResponse(BaseModel):
    ok: bool = True
    detail: str = FORGOT_PASSWORD_ANSWER


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _throttle_keys(identifier: str, request: Request) -> tuple[str, str]:
    client = request.client.host if request.client else "unknown"
    return f"account:{identifier.lower()}", f"client:{client}"


async def _ensure_not_throttled(db: AsyncSession, account_key: str, client_key: str) -> None:
    settings = get_settings()
    window = timedelta(seconds=settings.login_failure_window_seconds)
    counts = await session_store.failures_in_window(db, [account_key, client_key], window)
    if (
        counts.get(account_key, 0) >= settings.login_max_failures_per_account
        or counts.get(client_key, 0) >= settings.login_max_failures_per_client
    ):
        raise TooManyLoginAttempts(settings.login_failure_window_seconds)


async def _reject(db: AsyncSession, account_key: str, client_key: str) -> None:
    window = timedelta(seconds=get_settings().login_failure_window_seconds)
    await session_store.record_failure(db, [account_key, client_key], window)
    await db.commit()
    raise InvalidCredentials()


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Verify credentials and issue a JWT (also set as an HttpOnly cookie).

    Repeated failures pause sign-in for the account and for the client
    address (429 with Retry-After), counted in the database so every replica
    enforces the same limit.
    """

    identifier = payload.identifier.strip()
    account_key, client_key = _throttle_keys(identifier, request)
    await _ensure_not_throttled(db, account_key, client_key)
    user = await get_user_for_sign_in(db, identifier)
    if user is None:
        _verify(_DUMMY_HASH, payload.password)
        logger.info("Login rejected for unknown identifier '%s'.", identifier)
        await _reject(db, account_key, client_key)
    if not _verify(user.password, payload.password):
        logger.info("Login rejected for '%s': wrong password.", identifier)
        await _reject(db, account_key, client_key)
    await session_store.clear_failures(db, [account_key])
    await db.commit()

    ttl_seconds = (
        clamp_token_ttl(payload.expires_in_seconds)
        if payload.expires_in_seconds is not None
        else default_token_ttl_seconds()
    )
    token = issue_session_token(user, ttl_seconds=ttl_seconds)
    if payload.set_cookie:
        set_auth_cookie(response, token, max_age=ttl_seconds)
    logger.info("Login succeeded for '%s'.", user.identifier)
    return LoginResponse(access_token=token)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> LogoutResponse:
    """Sign this session out: revoke its token and delete the auth cookie."""

    token = get_token_from_cookie_or_header(request)
    if token:
        try:
            claims = decode_jwt(token)
        except AppException:
            claims = None  # already invalid or expired: nothing to revoke
        if claims is not None and claims.jti and claims.exp:
            await session_store.revoke_token(
                db, claims.jti, datetime.fromtimestamp(claims.exp, UTC)
            )
            await db.commit()
    clear_auth_cookie(response)
    return LogoutResponse()


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    store: JobStore = Depends(get_job_store),
    notify: Callable[[], object] = Depends(get_job_notifier),
) -> ForgotPasswordResponse:
    """Email a new temporary password to an address that is one of ours.

    **Answers the same way whatever happens.** Whether the address is here,
    inside the cooldown, or the send works - one reply, given before any of
    it is looked at (the ``auth.password_reset`` job does the work). Any
    difference would be a way to ask this deployment which addresses it
    knows, from a form that needs no password.
    """

    address = payload.email.strip()
    if address:
        # One queued reset per address at a time; a second click is a no-op.
        await store.enqueue(
            PASSWORD_RESET_JOB,
            {"address": address},
            dedupe_key=f"password-reset:{address.lower()}",
            retention=PASSWORD_RESET_RETENTION,
        )
        background_tasks.add_task(notify)
    return ForgotPasswordResponse()


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(user: AthenaTokenUser = Depends(get_user_on_any_password)) -> WhoAmIResponse:
    """The signed-in user with their current (database) metadata.

    Answers for somebody still on a temporary password too: the sign-in page
    reads ``metadata.needs_password_reset`` from here to ask for a new one.
    """

    return WhoAmIResponse(
        identifier=user.identifier,
        service=user.service,
        first_name=user.first_name,
        last_name=user.last_name,
        metadata=user.metadata,
    )

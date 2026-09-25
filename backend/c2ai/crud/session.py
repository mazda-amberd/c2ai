"""Token revocation and login-failure counters (shared by every replica)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.session import LoginFailure, RevokedToken
from c2ai.models.user import User


async def is_token_revoked(db: AsyncSession, jti: str | None) -> bool:
    if not jti:
        return False
    result = await db.execute(select(RevokedToken.jti).where(RevokedToken.jti == jti))
    return result.scalar_one_or_none() is not None


async def revoke_token(db: AsyncSession, jti: str, expires_at: datetime) -> None:
    await db.execute(
        insert(RevokedToken)
        .values(jti=jti, expires_at=expires_at)
        .on_conflict_do_nothing(index_elements=[RevokedToken.jti])
    )


async def bump_token_version(db: AsyncSession, user: User) -> int:
    """Invalidate every token issued to ``user`` so far; return the new version."""

    result = await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(token_version=User.token_version + 1)
        .returning(User.token_version)
    )
    user.token_version = result.scalar_one()
    return user.token_version


async def failures_in_window(db: AsyncSession, keys: list[str], window: timedelta) -> dict[str, int]:
    cutoff = datetime.now(UTC) - window
    result = await db.execute(
        select(LoginFailure.key, LoginFailure.failures).where(
            LoginFailure.key.in_(keys), LoginFailure.window_start > cutoff
        )
    )
    return {key: failures for key, failures in result.all()}


async def record_failure(db: AsyncSession, keys: list[str], window: timedelta) -> None:
    """Count one failure for each key; a window older than ``window`` restarts."""

    now = datetime.now(UTC)
    for key in keys:
        statement = insert(LoginFailure).values(key=key, window_start=now, failures=1)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[LoginFailure.key],
                set_={
                    "failures": text(
                        "CASE WHEN login_failures.window_start > :cutoff"
                        " THEN login_failures.failures + 1 ELSE 1 END"
                    ).bindparams(cutoff=now - window),
                    "window_start": text(
                        "CASE WHEN login_failures.window_start > :cutoff2"
                        " THEN login_failures.window_start ELSE :now END"
                    ).bindparams(cutoff2=now - window, now=now),
                },
            )
        )


async def clear_failures(db: AsyncSession, keys: list[str]) -> None:
    await db.execute(delete(LoginFailure).where(LoginFailure.key.in_(keys)))


async def purge_expired(db: AsyncSession, *, failure_window: timedelta) -> int:
    now = datetime.now(UTC)
    tokens = await db.execute(delete(RevokedToken).where(RevokedToken.expires_at <= now))
    failures = await db.execute(
        delete(LoginFailure).where(LoginFailure.window_start <= now - failure_window)
    )
    return (tokens.rowcount or 0) + (failures.rowcount or 0)

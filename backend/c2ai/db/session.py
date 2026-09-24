"""Async SQLAlchemy engine and the per-request session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from c2ai.db.url import database_url

# The engine connects lazily, so importing this module never opens a connection.
engine = create_async_engine(database_url(), echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request."""

    async with AsyncSessionLocal() as session:
        yield session

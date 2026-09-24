"""PostgreSQL-backed tests: a throwaway database per module, fully migrated.

Opt in by pointing ``C2AI_TEST_DATABASE_URL`` at a server where the user may
create databases, e.g. the project-local one from ``scripts/local-db.sh``::

    C2AI_TEST_DATABASE_URL=postgresql://c2ai@127.0.0.1:55432/postgres pytest

Without it these tests are skipped, so the default suite stays database-free.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from c2ai.config import get_settings
from c2ai.db.migrate import run_migrations
from c2ai.db.url import psycopg2_connect_kwargs

_SERVER_URL = os.environ.get("C2AI_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _SERVER_URL, reason="set C2AI_TEST_DATABASE_URL to run PostgreSQL tests"
)


def _admin_connection():
    url = make_url(_SERVER_URL).set(drivername="postgresql+psycopg2")
    conn = psycopg2.connect(**psycopg2_connect_kwargs(url))
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def database_url():
    """Create ``c2ai_test_<random>``, apply every migration, drop it afterwards."""

    if not _SERVER_URL:
        pytest.skip("set C2AI_TEST_DATABASE_URL to run PostgreSQL tests")
    name = f"c2ai_test_{uuid.uuid4().hex[:10]}"
    conn = _admin_connection()
    with conn.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    url = make_url(_SERVER_URL).set(database=name, drivername="postgresql+asyncpg")
    rendered = url.render_as_string(hide_password=False)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = rendered
    get_settings.cache_clear()
    try:
        run_migrations()
        yield rendered
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.close()


@pytest_asyncio.fixture
async def session_factory(database_url):
    # NullPool: the TestClient runs the app on its own event loop, and pooled
    # asyncpg connections cannot cross loops.
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session

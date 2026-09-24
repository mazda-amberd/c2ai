"""Single source of truth for the database connection URL."""

from __future__ import annotations

from sqlalchemy.engine import URL, make_url

from c2ai.config import get_settings


def database_url() -> str:
    """Return the async SQLAlchemy URL used by the API.

    ``DATABASE_URL`` is preferred; ``LOCAL_DATABASE_URL`` is accepted for
    existing Athena environments. A plain ``postgresql://`` URL is upgraded to
    the asyncpg driver the API requires.
    """

    raw = get_settings().database_url.strip()
    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql+asyncpg://c2ai:secret@localhost:5432/c2ai"
        )
    url = make_url(raw)
    if url.drivername in {"postgresql", "postgres", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)


def sync_database_url() -> URL:
    """The same database addressed through psycopg2, for migrations and bootstrap."""

    return make_url(database_url()).set(drivername="postgresql+psycopg2")


def psycopg2_connect_kwargs(url: URL, *, database: str | None = None) -> dict:
    """Translate a SQLAlchemy URL into ``psycopg2.connect`` keyword arguments."""

    kwargs = {
        "dbname": database or url.database,
        "user": url.username,
        "password": url.password,
        "host": url.host or "localhost",
        "port": url.port or 5432,
    }
    return {key: value for key, value in kwargs.items() if value is not None}

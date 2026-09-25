"""Shared test doubles."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock


class EmptyResult:
    """A SQLAlchemy result with no rows (e.g. no open operation-log row)."""

    def scalar_one_or_none(self):
        return None

    def scalar_one(self):
        return 0

    def first(self):
        return None

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return []


def mock_db() -> MagicMock:
    """An AsyncSession stand-in whose queries find nothing."""

    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=EmptyResult())
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@contextmanager
def override_dependency(dependency):
    """Replace a FastAPI dependency; yields the factory, like ``patch(...) as m``.

    Set ``factory.return_value`` (or ``side_effect``) to what the route should
    receive.
    """

    from c2ai.app import app

    factory = MagicMock()
    app.dependency_overrides[dependency] = lambda: factory()
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(dependency, None)

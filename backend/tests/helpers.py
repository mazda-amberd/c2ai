"""Shared test doubles."""

from __future__ import annotations

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

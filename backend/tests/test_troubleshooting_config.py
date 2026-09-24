"""Tests for database-backed troubleshooting runtime configuration."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import ProgrammingError

from c2ai.services.troubleshooting_config import get_troubleshooting_window_hours


async def test_reads_troubleshooting_window_from_database():
    result = MagicMock()
    result.scalar_one_or_none.return_value = 12
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    assert await get_troubleshooting_window_hours(session) == 12
    session.execute.assert_awaited_once()


async def test_defaults_to_four_hours_when_config_row_is_absent():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    assert await get_troubleshooting_window_hours(session) == 4


async def test_defaults_to_four_hours_when_migration_is_not_applied():
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=ProgrammingError(
            "SELECT troubleshooting_config.lookback_hours",
            {},
            Exception("relation does not exist"),
        )
    )
    session.rollback = AsyncMock()

    assert await get_troubleshooting_window_hours(session) == 4
    session.rollback.assert_awaited_once()

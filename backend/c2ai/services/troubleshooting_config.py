"""Database-backed runtime configuration for application troubleshooting."""

from __future__ import annotations

import logging

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.db.session import get_db_session
from c2ai.models.troubleshooting_config import TroubleshootingConfig

DEFAULT_TROUBLESHOOTING_LOOKBACK_HOURS = 4
MAX_TROUBLESHOOTING_LOOKBACK_HOURS = 168
logger = logging.getLogger(__name__)


async def get_troubleshooting_window_hours(
    session: AsyncSession = Depends(get_db_session),
) -> int:
    """Read the singleton lookback window, defaulting when its row is absent."""

    try:
        result = await session.execute(
            select(TroubleshootingConfig.lookback_hours).where(
                TroubleshootingConfig.id == 1
            )
        )
    except ProgrammingError:
        await session.rollback()
        logger.warning(
            "Troubleshooting configuration table is unavailable; using the "
            "%s-hour default. Apply pending database migrations.",
            DEFAULT_TROUBLESHOOTING_LOOKBACK_HOURS,
        )
        return DEFAULT_TROUBLESHOOTING_LOOKBACK_HOURS
    hours = result.scalar_one_or_none()
    if hours is None:
        return DEFAULT_TROUBLESHOOTING_LOOKBACK_HOURS
    return max(1, min(int(hours), MAX_TROUBLESHOOTING_LOOKBACK_HOURS))

"""Tests for application_instances snapshot persistence."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from c2ai.crud.application_instance import replace_application_instances_for_tiers
from c2ai.schemas.grafana import Instance, Status


@pytest.mark.asyncio
async def test_replace_application_instances_per_tier():
    """Each tier in the payload gets a delete then new rows; None clears tier only."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    tiers = {
        "Tier 1": [
            Instance(
                id=1,
                name="deploy-a",
                nodename="ns-a",
                cpu=1.0,
                memory=2.0,
                gpu=0.0,
                status=Status.HEALTHY,
            )
        ],
        "Tier 2": [],
        "Tier 3": None,
    }

    await replace_application_instances_for_tiers(db, tiers)

    assert db.execute.await_count == 3
    assert db.add.call_count == 1
    db.commit.assert_not_awaited()  # the route commits

"""Tests for stored cluster, tier, and application cost aggregation."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from c2ai.crud.financial import get_financial_cost_summary

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _rows_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_cluster_summary_returns_all_tiers_including_zero_cost():
    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(Decimal("14.75")),
        _rows_result([(1, Decimal("10.00")), (3, Decimal("4.75"))]),
    ]

    summary = await get_financial_cost_summary(
        db,
        period_start=START,
        period_end=END,
        cost_type=None,
    )

    assert summary.total_cost == Decimal("14.75000000")
    assert summary.tier_costs == {
        1: Decimal("10.00000000"),
        2: Decimal("0E-8"),
        3: Decimal("4.75000000"),
        4: Decimal("0E-8"),
    }
    assert summary.application_costs == ()
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_tier_summary_groups_applications_and_public_filter():
    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(Decimal("6.25")),
        _rows_result(
            [
                ("app-a", 2, Decimal("4.00")),
                ("app-b", 2, Decimal("2.25")),
            ]
        ),
    ]

    summary = await get_financial_cost_summary(
        db,
        period_start=START,
        period_end=END,
        cost_type="public_api",
        tier=2,
    )

    assert summary.tier_costs == {2: Decimal("6.25000000")}
    assert [item.application_key for item in summary.application_costs] == [
        "app-a",
        "app-b",
    ]
    assert [item.cost for item in summary.application_costs] == [
        Decimal("4.00000000"),
        Decimal("2.25000000"),
    ]


@pytest.mark.asyncio
async def test_public_summary_is_zero_when_no_records_exist():
    db = AsyncMock()
    db.execute.side_effect = [_scalar_result(0), _rows_result([])]

    summary = await get_financial_cost_summary(
        db,
        period_start=START,
        period_end=END,
        cost_type="public_api",
    )

    assert summary.total_cost == Decimal("0E-8")
    assert all(cost == 0 for cost in summary.tier_costs.values())

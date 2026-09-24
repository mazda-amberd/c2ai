"""Tests for financial rates, cost calculation, and historical persistence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from c2ai.crud.financial import (
    FinancialRateNotFoundError,
    configure_private_llm_rate,
    configure_public_api_rate,
    get_private_llm_rate_for_period,
    get_public_api_rate_for_period,
    record_private_llm_cost,
    record_public_api_cost,
)
from c2ai.models.financial import FinancialCostRecord, FinancialRate
from c2ai.services.financial import calculate_private_llm_cost, calculate_public_api_cost

START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_calculate_private_llm_cost_uses_decimal_precision():
    assert calculate_private_llm_cost(gpu_hours="1.25", hourly_rate="2.40") == Decimal(
        "3.00000000"
    )


@pytest.mark.parametrize("field,value", [("gpu_hours", "-1"), ("hourly_rate", "NaN")])
def test_calculate_private_llm_cost_rejects_invalid_values(field, value):
    values = {"gpu_hours": "1", "hourly_rate": "2"}
    values[field] = value

    with pytest.raises(ValueError, match=field):
        calculate_private_llm_cost(**values)


@pytest.mark.asyncio
async def test_configure_private_rate_closes_previous_rate():
    previous = FinancialRate(
        id=1,
        cost_type="private_llm",
        resource_type="A100",
        gpu_hourly_rate=Decimal("2.00000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(previous)

    new_rate = await configure_private_llm_rate(
        db,
        resource_type=" A100 ",
        hourly_rate="3.5",
        effective_from=START,
    )

    assert previous.effective_to == START
    assert new_rate.resource_type == "A100"
    assert new_rate.gpu_hourly_rate == Decimal("3.50000000")
    assert db.add.call_args_list == [call(previous), call(new_rate)]
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(new_rate)


@pytest.mark.asyncio
async def test_rate_lookup_requires_one_rate_to_cover_complete_period():
    db = AsyncMock()
    db.execute.return_value = _result(None)

    with pytest.raises(FinancialRateNotFoundError, match="covers the requested period"):
        await get_private_llm_rate_for_period(
            db,
            resource_type="A100",
            period_start=START,
            period_end=END,
        )


@pytest.mark.asyncio
async def test_record_private_cost_snapshots_rate_and_amount():
    rate = FinancialRate(
        id=7,
        cost_type="private_llm",
        resource_type="A100",
        gpu_hourly_rate=Decimal("2.40000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_result(None), _result(rate)]

    record = await record_private_llm_cost(
        db,
        application_key="customer-app",
        tier=2,
        resource_type="A100",
        period_start=START,
        period_end=END,
        gpu_hours="1.25",
        source_event_id="grafana:customer-app:2026-08-01T00",
    )

    assert isinstance(record, FinancialCostRecord)
    assert record.rate_id == 7
    assert record.gpu_hours == Decimal("1.25000000")
    assert record.gpu_hourly_rate == Decimal("2.40000000")
    assert record.calculated_cost == Decimal("3.00000000")
    assert record.tier == 2
    db.add.assert_called_once_with(record)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(record)


@pytest.mark.asyncio
async def test_record_private_cost_can_join_checkpoint_transaction():
    rate = FinancialRate(
        id=7,
        cost_type="private_llm",
        resource_type="A100",
        gpu_hourly_rate=Decimal("2.40000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_result(None), _result(rate)]

    record = await record_private_llm_cost(
        db,
        application_key="customer-app",
        tier=2,
        resource_type="A100",
        period_start=START,
        period_end=END,
        gpu_hours="1.25",
        source_event_id="gateway:interval",
        commit=False,
    )

    assert record.calculated_cost == Decimal("3.00000000")
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_private_cost_returns_existing_event_without_duplicate():
    existing = FinancialCostRecord(
        id=11,
        application_key="customer-app",
        tier=2,
        cost_type="private_llm",
        period_start=START,
        period_end=END,
        rate_id=7,
        resource_type="A100",
        gpu_hours=Decimal("1.00000000"),
        gpu_hourly_rate=Decimal("2.00000000"),
        calculated_cost=Decimal("2.00000000"),
        currency="USD",
        source_event_id="event-11",
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(existing)

    returned = await record_private_llm_cost(
        db,
        application_key="customer-app",
        tier=2,
        resource_type="A100",
        period_start=START,
        period_end=END,
        gpu_hours="1",
        source_event_id="event-11",
    )

    assert returned is existing
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def test_calculate_public_api_cost_prices_input_and_output_separately():
    # 1M input at $2.50/M plus 500K output at $10.00/M — gpt-4o pricing.
    assert calculate_public_api_cost(
        input_tokens=1_000_000,
        output_tokens=500_000,
        input_rate_per_million_tokens="2.50",
        output_rate_per_million_tokens="10.00",
    ) == Decimal("7.50000000")


def test_calculate_public_api_cost_keeps_sub_cent_token_spend():
    # 200K input at $3.00/M plus 100K output at $15.00/M — claude-sonnet-4-6.
    assert calculate_public_api_cost(
        input_tokens=200_000,
        output_tokens=100_000,
        input_rate_per_million_tokens="3.00",
        output_rate_per_million_tokens="15.00",
    ) == Decimal("2.10000000")
    assert calculate_public_api_cost(
        input_tokens=1,
        output_tokens=0,
        input_rate_per_million_tokens="3.00",
        output_rate_per_million_tokens="15.00",
    ) == Decimal("0.00000300")


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", "-1"),
        ("output_rate_per_million_tokens", "NaN"),
    ],
)
def test_calculate_public_api_cost_rejects_invalid_values(field, value):
    values = {
        "input_tokens": "1",
        "output_tokens": "1",
        "input_rate_per_million_tokens": "2",
        "output_rate_per_million_tokens": "3",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        calculate_public_api_cost(**values)


@pytest.mark.asyncio
async def test_configure_public_rate_closes_previous_model_rate():
    previous = FinancialRate(
        id=7,
        cost_type="public_api",
        provider="openai",
        model_name="gpt-4o",
        input_rate_per_million_tokens=Decimal("5.00000000"),
        output_rate_per_million_tokens=Decimal("15.00000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(previous)

    new_rate = await configure_public_api_rate(
        db,
        provider=" openai ",
        model_name=" gpt-4o ",
        input_rate_per_million_tokens="2.5",
        output_rate_per_million_tokens="10",
        effective_from=START,
    )

    assert previous.effective_to == START
    assert new_rate.provider == "openai"
    assert new_rate.model_name == "gpt-4o"
    assert new_rate.input_rate_per_million_tokens == Decimal("2.50000000")
    assert new_rate.output_rate_per_million_tokens == Decimal("10.00000000")
    assert new_rate.effective_to is None
    assert db.add.call_args_list == [call(previous), call(new_rate)]


@pytest.mark.asyncio
async def test_public_rate_lookup_requires_full_period_coverage():
    db = AsyncMock()
    db.execute.return_value = _result(None)

    with pytest.raises(FinancialRateNotFoundError, match="anthropic/claude-sonnet-4-6"):
        await get_public_api_rate_for_period(
            db,
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            period_start=START,
            period_end=END,
        )


@pytest.mark.asyncio
async def test_public_rate_lookup_falls_back_to_the_model_rate():
    model_rate = FinancialRate(
        id=9,
        cost_type="public_api",
        provider="gemini",
        model_name="gemini-2.5-flash",
        input_rate_per_million_tokens=Decimal("0.30000000"),
        output_rate_per_million_tokens=Decimal("2.50000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    # No row for the gateway's own provider label, then the model's own rate.
    db.execute.side_effect = [_result(None), _result(model_rate)]

    rate = await get_public_api_rate_for_period(
        db,
        provider="google",
        model_name="gemini-2.5-flash",
        period_start=START,
        period_end=END,
    )

    assert rate is model_rate
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_record_public_api_cost_snapshots_tokens_and_model_rate():
    rate = FinancialRate(
        id=11,
        cost_type="public_api",
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        input_rate_per_million_tokens=Decimal("3.00000000"),
        output_rate_per_million_tokens=Decimal("15.00000000"),
        currency="USD",
        effective_from=START - timedelta(days=1),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_result(None), _result(rate)]

    record = await record_public_api_cost(
        db,
        application_key="app-one",
        tier=2,
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        period_start=START,
        period_end=END,
        input_tokens=200_000,
        output_tokens=100_000,
        source_event_id="gateway-public:abc",
    )

    assert record.cost_type == "public_api"
    assert record.rate_id == 11
    assert record.input_tokens == 200_000
    assert record.output_tokens == 100_000
    assert record.input_rate_per_million_tokens == Decimal("3.00000000")
    assert record.output_rate_per_million_tokens == Decimal("15.00000000")
    assert record.calculated_cost == Decimal("2.10000000")
    assert record.gpu_hours is None
    assert record.currency == "USD"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_public_api_cost_returns_the_existing_event():
    existing = FinancialCostRecord(id=3, cost_type="public_api")
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(existing)

    returned = await record_public_api_cost(
        db,
        application_key="app-one",
        tier=1,
        provider="openai",
        model_name="gpt-4o",
        period_start=START,
        period_end=END,
        input_tokens=10,
        output_tokens=5,
        source_event_id="gateway-public:abc",
    )

    assert returned is existing
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_public_api_cost_rejects_fractional_token_counts():
    db = AsyncMock()

    with pytest.raises(ValueError, match="input_tokens"):
        await record_public_api_cost(
            db,
            application_key="app-one",
            tier=1,
            provider="openai",
            model_name="gpt-4o",
            period_start=START,
            period_end=END,
            input_tokens=1.5,
            output_tokens=5,
        )

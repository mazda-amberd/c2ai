"""Tests for Grafana source-namespace gateway cost pricing."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from c2ai.models.financial import FinancialCostRecord, LlmGatewayTokenCheckpoint
from c2ai.schemas.grafana import (
    GrafanaField,
    GrafanaFieldLabels,
    GrafanaFrame,
    GrafanaFrameData,
    GrafanaFrameSchema,
    GrafanaQueryResult,
    GrafanaResponse,
)
from c2ai.crud.financial import FinancialRateNotFoundError
from c2ai.services.gateway_cost_ingestion import (
    PublicModelUsageKey,
    combine_public_token_usage,
    duration_seconds_to_gpu_hours,
    ingest_gateway_costs,
    parse_namespace_duration_totals,
    parse_namespace_model_token_totals,
    parse_namespace_tier_mappings,
    tokens_to_whole_count,
)

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _frame(
    *,
    labels: GrafanaFieldLabels,
    timestamps: list[int] | None = None,
    values: list[float] | None = None,
) -> GrafanaFrame:
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId="A",
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(name="Value", type="number", labels=labels),
            ],
        ),
        data=GrafanaFrameData(values=[timestamps or [0], values or [1.0]]),
    )


def _response(*frames: GrafanaFrame) -> GrafanaResponse:
    return GrafanaResponse(
        results={"A": GrafanaQueryResult(status=200, frames=list(frames))}
    )


def _grafana(
    *,
    duration: GrafanaResponse | None = None,
    tiers: GrafanaResponse | None = None,
    public_input: GrafanaResponse | None = None,
    public_output: GrafanaResponse | None = None,
) -> AsyncMock:
    """A Grafana client returning an empty result for every unstated query."""
    grafana = AsyncMock()
    grafana.fetch_llm_gateway_request_duration_by_namespace.return_value = (
        duration if duration is not None else _response()
    )
    grafana.fetch_llm_gateway_namespace_tiers.return_value = (
        tiers if tiers is not None else _response()
    )
    grafana.fetch_llm_gateway_public_input_tokens_by_model.return_value = (
        public_input if public_input is not None else _response()
    )
    grafana.fetch_llm_gateway_public_output_tokens_by_model.return_value = (
        public_output if public_output is not None else _response()
    )
    return grafana


def test_namespace_duration_parser_sums_positive_increases():
    response = _response(
        _frame(
            labels=GrafanaFieldLabels(source_namespace="app-one"),
            values=[100.5],
        ),
        _frame(
            labels=GrafanaFieldLabels(source_namespace="app-one"),
            values=[20.25],
        ),
        _frame(
            labels=GrafanaFieldLabels(source_namespace="app-two"),
            values=[0.0],
        ),
    )

    assert parse_namespace_duration_totals(response) == {
        "app-one": Decimal("120.75")
    }


def test_namespace_tier_mapping_ignores_ambiguous_namespaces():
    response = _response(
        _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier2")),
        _frame(labels=GrafanaFieldLabels(namespace="app-two", label_tier="prod")),
        _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier1")),
    )

    assert parse_namespace_tier_mappings(response) == {"app-two": 3}


def test_duration_seconds_convert_to_gpu_hours():
    assert duration_seconds_to_gpu_hours(Decimal("3600")) == Decimal("1.00000000")
    assert duration_seconds_to_gpu_hours(Decimal("1800")) == Decimal("0.50000000")
    assert duration_seconds_to_gpu_hours(Decimal("0.00001")) == Decimal("0.00000000")


@pytest.mark.asyncio
async def test_first_poll_stores_grafana_baseline_without_querying_history():
    grafana = _grafana()
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(None)

    result = await ingest_gateway_costs(db, grafana, observed_at=START)

    assert result.baseline_created is True
    assert result.records == ()
    checkpoint = db.add.call_args.args[0]
    assert isinstance(checkpoint, LlmGatewayTokenCheckpoint)
    assert checkpoint.counters["group_by"] == "source_namespace"
    assert checkpoint.counters["metric"] == "llm_duration_seconds_sum"
    db.commit.assert_awaited_once()
    grafana.fetch_llm_gateway_request_duration_by_namespace.assert_not_awaited()
    grafana.fetch_llm_gateway_public_input_tokens_by_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_scrape_window_keeps_checkpoint_for_next_poll():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1,
        observed_at=START,
        counters={"source": "grafana"},
    )
    grafana = _grafana()
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)

    result = await ingest_gateway_costs(
        db,
        grafana,
        observed_at=START + timedelta(minutes=20),
    )

    assert result.records == ()
    assert result.observed_duration_seconds == Decimal("0")
    assert checkpoint.observed_at == START
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    grafana.fetch_llm_gateway_namespace_tiers.assert_not_awaited()


@pytest.mark.asyncio
async def test_window_below_minimum_defers_without_querying_grafana():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1,
        observed_at=START,
        counters={"source": "grafana"},
    )
    grafana = _grafana()
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)

    result = await ingest_gateway_costs(
        db,
        grafana,
        observed_at=START + timedelta(seconds=120),
        min_window_seconds=900,
    )

    assert result.records == ()
    assert result.baseline_created is False
    assert checkpoint.observed_at == START
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    grafana.fetch_llm_gateway_request_duration_by_namespace.assert_not_awaited()
    grafana.fetch_llm_gateway_public_output_tokens_by_model.assert_not_awaited()
    grafana.fetch_llm_gateway_namespace_tiers.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_prices_request_duration_per_namespace():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1,
        observed_at=START,
        counters={"legacy": "counter snapshot"},
    )
    grafana = _grafana(
        duration=_response(
            _frame(
                labels=GrafanaFieldLabels(source_namespace="app-one"),
                values=[3600.0],
            ),
            _frame(
                labels=GrafanaFieldLabels(source_namespace="app-two"),
                values=[1800.0],
            ),
            _frame(
                labels=GrafanaFieldLabels(source_namespace="missing-tier"),
                values=[100.0],
            ),
        ),
        tiers=_response(
            _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier2")),
            _frame(labels=GrafanaFieldLabels(namespace="app-two", label_tier="tier2")),
        ),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)
    stored = [FinancialCostRecord(id=1), FinancialCostRecord(id=2)]

    with patch(
        "c2ai.services.gateway_cost_ingestion.record_private_llm_cost",
        new_callable=AsyncMock,
        side_effect=stored,
    ) as record_cost:
        result = await ingest_gateway_costs(db, grafana, observed_at=END)

    assert result.records == tuple(stored)
    assert result.observed_duration_seconds == Decimal("5500")
    assert result.attributed_duration_seconds == Decimal("5400")
    assert result.unmapped_namespaces == ("missing-tier",)
    assert record_cost.await_count == 2
    assert record_cost.await_args_list[0].kwargs["application_key"] == "app-one"
    assert record_cost.await_args_list[0].kwargs["gpu_hours"] == Decimal("1.00000000")
    assert record_cost.await_args_list[1].kwargs["application_key"] == "app-two"
    assert record_cost.await_args_list[1].kwargs["gpu_hours"] == Decimal("0.50000000")
    assert record_cost.await_args_list[0].kwargs["commit"] is False
    assert db.add.call_args_list == [call(checkpoint)]
    assert checkpoint.observed_at == END
    assert checkpoint.counters["metric"] == "llm_duration_seconds_sum"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sub_quantum_duration_advances_checkpoint_without_record():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1, observed_at=START, counters={"source": "grafana"}
    )
    grafana = _grafana(
        duration=_response(
            _frame(
                labels=GrafanaFieldLabels(source_namespace="app-one"),
                values=[0.00001],
            ),
        ),
        tiers=_response(
            _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier2")),
        ),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)

    with patch(
        "c2ai.services.gateway_cost_ingestion.record_private_llm_cost",
        new_callable=AsyncMock,
    ) as record_cost:
        result = await ingest_gateway_costs(db, grafana, observed_at=END)

    assert result.records == ()
    assert result.attributed_duration_seconds == Decimal("0.00001")
    assert checkpoint.observed_at == END
    record_cost.assert_not_awaited()
    db.commit.assert_awaited_once()


def _token_frame(namespace: str, provider: str, model: str, value: float):
    return _frame(
        labels=GrafanaFieldLabels(
            source_namespace=namespace,
            provider=provider,
            model=model,
        ),
        values=[value],
    )


def test_public_token_parser_groups_by_provider_and_model():
    response = _response(
        _token_frame("app-one", "openai", "gpt-4o", 1000.0),
        _token_frame("app-one", "openai", "gpt-4o", 500.0),
        _token_frame("app-one", "anthropic", "claude-sonnet-4-6", 250.0),
        _token_frame("app-two", "openai", "gpt-4o", 0.0),
        # A series that lost its model label cannot be priced.
        _frame(
            labels=GrafanaFieldLabels(source_namespace="app-two", provider="openai"),
            values=[900.0],
        ),
    )

    assert parse_namespace_model_token_totals(response) == {
        PublicModelUsageKey("app-one", "openai", "gpt-4o"): Decimal("1500"),
        PublicModelUsageKey(
            "app-one", "anthropic", "claude-sonnet-4-6"
        ): Decimal("250"),
    }


def test_combine_public_token_usage_defaults_the_missing_side_to_zero():
    key_in_only = PublicModelUsageKey("app-one", "openai", "gpt-4o")
    key_out_only = PublicModelUsageKey("app-two", "anthropic", "claude-sonnet-4-6")

    assert combine_public_token_usage(
        {key_in_only: Decimal("100")},
        {key_out_only: Decimal("40")},
    ) == {
        key_in_only: (Decimal("100"), Decimal("0")),
        key_out_only: (Decimal("0"), Decimal("40")),
    }


def test_token_increases_round_to_whole_counts():
    assert tokens_to_whole_count(Decimal("1200.4")) == 1200
    assert tokens_to_whole_count(Decimal("1200.5")) == 1201
    assert tokens_to_whole_count(Decimal("0.2")) == 0


@pytest.mark.asyncio
async def test_poll_prices_public_tokens_per_model_alongside_gpu_time():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1, observed_at=START, counters={"source": "grafana"}
    )
    grafana = _grafana(
        duration=_response(
            _frame(
                labels=GrafanaFieldLabels(source_namespace="app-one"),
                values=[3600.0],
            ),
        ),
        public_input=_response(
            _token_frame("app-one", "openai", "gpt-4o", 1_000_000.0),
            _token_frame("app-one", "anthropic", "claude-sonnet-4-6", 200_000.0),
            _token_frame("missing-tier", "openai", "gpt-4o", 5_000.0),
        ),
        public_output=_response(
            _token_frame("app-one", "openai", "gpt-4o", 500_000.4),
            _token_frame("app-one", "anthropic", "claude-sonnet-4-6", 100_000.0),
        ),
        tiers=_response(
            _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier2")),
        ),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)
    public_stored = [FinancialCostRecord(id=10), FinancialCostRecord(id=11)]

    with patch(
        "c2ai.services.gateway_cost_ingestion.record_private_llm_cost",
        new_callable=AsyncMock,
        return_value=FinancialCostRecord(id=1),
    ), patch(
        "c2ai.services.gateway_cost_ingestion.record_public_api_cost",
        new_callable=AsyncMock,
        side_effect=public_stored,
    ) as record_public:
        result = await ingest_gateway_costs(db, grafana, observed_at=END)

    assert result.public_records == tuple(public_stored)
    assert result.unmapped_namespaces == ("missing-tier",)
    assert result.observed_public_tokens == Decimal("1805000.4")
    assert result.attributed_public_tokens == Decimal("1800000.4")
    assert record_public.await_count == 2

    anthropic_call = record_public.await_args_list[0].kwargs
    assert anthropic_call["provider"] == "anthropic"
    assert anthropic_call["model_name"] == "claude-sonnet-4-6"
    assert anthropic_call["tier"] == 2
    assert anthropic_call["input_tokens"] == 200_000
    assert anthropic_call["output_tokens"] == 100_000
    assert anthropic_call["commit"] is False

    openai_call = record_public.await_args_list[1].kwargs
    assert openai_call["model_name"] == "gpt-4o"
    assert openai_call["input_tokens"] == 1_000_000
    assert openai_call["output_tokens"] == 500_000
    assert openai_call["source_event_id"] != anthropic_call["source_event_id"]

    assert checkpoint.observed_at == END
    assert checkpoint.counters["public_group_by"] == "source_namespace,provider,model"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_without_a_configured_rate_does_not_block_the_others():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1, observed_at=START, counters={"source": "grafana"}
    )
    grafana = _grafana(
        public_input=_response(
            _token_frame("app-one", "openai", "gpt-4o", 1_000.0),
            _token_frame("app-one", "openai", "gpt-5-unpriced", 2_000.0),
        ),
        tiers=_response(
            _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier1")),
        ),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)
    priced = FinancialCostRecord(id=21)

    with patch(
        "c2ai.services.gateway_cost_ingestion.record_public_api_cost",
        new_callable=AsyncMock,
        side_effect=[priced, FinancialRateNotFoundError("no rate")],
    ):
        result = await ingest_gateway_costs(db, grafana, observed_at=END)

    assert result.public_records == (priced,)
    assert result.unpriced_models == ("openai/gpt-5-unpriced",)
    assert checkpoint.observed_at == END
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sub_token_public_usage_advances_checkpoint_without_record():
    checkpoint = LlmGatewayTokenCheckpoint(
        id=1, observed_at=START, counters={"source": "grafana"}
    )
    grafana = _grafana(
        public_input=_response(_token_frame("app-one", "openai", "gpt-4o", 0.2)),
        tiers=_response(
            _frame(labels=GrafanaFieldLabels(namespace="app-one", label_tier="tier1")),
        ),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result(checkpoint)

    with patch(
        "c2ai.services.gateway_cost_ingestion.record_public_api_cost",
        new_callable=AsyncMock,
    ) as record_public:
        result = await ingest_gateway_costs(db, grafana, observed_at=END)

    assert result.public_records == ()
    assert result.attributed_public_tokens == Decimal("0.2")
    assert checkpoint.observed_at == END
    record_public.assert_not_awaited()
    db.commit.assert_awaited_once()

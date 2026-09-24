"""Schema-level tests for the financial tracking data foundation."""

from pathlib import Path

from c2ai.models.financial import (
    FinancialCostRecord,
    FinancialRate,
    LlmGatewayTokenCheckpoint,
)


def _constraint_names(model) -> set[str]:
    return {
        constraint.name for constraint in model.__table__.constraints if constraint.name
    }


def _index_names(model) -> set[str]:
    return {index.name for index in model.__table__.indexes}


def test_financial_rate_schema_preserves_rate_history():
    columns = FinancialRate.__table__.columns

    assert columns["effective_from"].nullable is False
    assert columns["effective_to"].nullable is True
    assert {
        "ck_financial_rates_cost_type",
        "ck_financial_rates_effective_period",
        "ck_financial_rates_shape",
    } <= _constraint_names(FinancialRate)
    assert {
        "uq_financial_rates_current_public",
        "uq_financial_rates_current_private",
    } <= _index_names(FinancialRate)


def test_cost_record_snapshots_usage_rate_and_calculated_cost():
    columns = FinancialCostRecord.__table__.columns

    for required_column in (
        "application_key",
        "tier",
        "period_start",
        "period_end",
        "rate_id",
        "input_tokens",
        "output_tokens",
        "gpu_hours",
        "input_rate_per_million_tokens",
        "output_rate_per_million_tokens",
        "gpu_hourly_rate",
        "calculated_cost",
    ):
        assert required_column in columns

    assert columns["calculated_cost"].nullable is False
    assert "ck_financial_cost_records_shape" in _constraint_names(FinancialCostRecord)
    assert "uq_financial_cost_records_source_event" in _index_names(FinancialCostRecord)


def test_gateway_checkpoint_is_a_single_historical_snapshot():
    columns = LlmGatewayTokenCheckpoint.__table__.columns

    assert columns["observed_at"].nullable is False
    assert columns["counters"].nullable is False
    assert "ck_llm_gateway_token_checkpoint_singleton" in _constraint_names(
        LlmGatewayTokenCheckpoint
    )


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def test_public_api_rates_are_seeded_for_the_gateway_models():
    """The models the gateway serves must be priced or their usage is skipped."""
    seed_sql = (MIGRATIONS_DIR / "0017_public_api_token_rates.sql").read_text()
    normalized = " ".join(seed_sql.split())

    assert "'public_api'" in normalized
    for seeded_row in (
        "('openai', 'gpt-4o', 2.50000000, 10.00000000)",
        "('azure-openai', 'gpt-4o', 2.50000000, 10.00000000)",
        "('anthropic', 'claude-sonnet-4-6', 3.00000000, 15.00000000)",
    ):
        assert seeded_row in normalized

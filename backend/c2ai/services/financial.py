"""Pure financial calculations shared by ingestion and API layers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DecimalInput = Decimal | int | float | str

# Matches NUMERIC(20, 8) in the financial tables.
FINANCIAL_QUANTUM = Decimal("0.00000001")

# Public providers publish pricing per million tokens.
TOKENS_PER_MILLION = Decimal("1000000")


def financial_decimal(value: DecimalInput, *, field_name: str) -> Decimal:
    """Convert a numeric input to a non-negative, eight-decimal value."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid number") from exc

    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if decimal_value < 0:
        raise ValueError(f"{field_name} must be non-negative")

    return decimal_value.quantize(FINANCIAL_QUANTUM, rounding=ROUND_HALF_UP)


def calculate_private_llm_cost(
    *,
    gpu_hours: DecimalInput,
    hourly_rate: DecimalInput,
) -> Decimal:
    """Calculate a private-LLM cost using GPU-hours times the hourly rate."""
    normalized_hours = financial_decimal(gpu_hours, field_name="gpu_hours")
    normalized_rate = financial_decimal(hourly_rate, field_name="hourly_rate")
    return (normalized_hours * normalized_rate).quantize(
        FINANCIAL_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def calculate_public_api_cost(
    *,
    input_tokens: DecimalInput,
    output_tokens: DecimalInput,
    input_rate_per_million_tokens: DecimalInput,
    output_rate_per_million_tokens: DecimalInput,
) -> Decimal:
    """Calculate a public-API cost from token counts and per-million pricing.

    Input and output tokens bill at different published rates, so each side is
    priced separately and the two amounts are summed before rounding once.
    """
    normalized_input_tokens = financial_decimal(
        input_tokens, field_name="input_tokens"
    )
    normalized_output_tokens = financial_decimal(
        output_tokens, field_name="output_tokens"
    )
    normalized_input_rate = financial_decimal(
        input_rate_per_million_tokens,
        field_name="input_rate_per_million_tokens",
    )
    normalized_output_rate = financial_decimal(
        output_rate_per_million_tokens,
        field_name="output_rate_per_million_tokens",
    )
    total = (
        normalized_input_tokens * normalized_input_rate
        + normalized_output_tokens * normalized_output_rate
    ) / TOKENS_PER_MILLION
    return total.quantize(FINANCIAL_QUANTUM, rounding=ROUND_HALF_UP)

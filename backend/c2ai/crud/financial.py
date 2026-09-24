# pylint: disable=import-error
"""Rate management and historical persistence for both financial cost sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.financial import FinancialCostRecord, FinancialRate
from c2ai.services.financial import (
    DecimalInput,
    calculate_private_llm_cost,
    calculate_public_api_cost,
    financial_decimal,
)

PRIVATE_LLM_COST_TYPE = "private_llm"
PUBLIC_API_COST_TYPE = "public_api"
USD_CURRENCY = "USD"


@dataclass(frozen=True)
class ApplicationCostTotal:
    """Aggregated cost for one application and its historical tier."""

    application_key: str
    tier: int
    cost: Decimal


@dataclass(frozen=True)
class FinancialCostSummary:
    """Database aggregation result used by the financial API."""

    total_cost: Decimal
    tier_costs: dict[int, Decimal]
    application_costs: tuple[ApplicationCostTotal, ...]


class FinancialRateNotFoundError(LookupError):
    """Raised when no one rate covers the complete requested usage interval."""


def _require_aware_datetime(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_token_count(value: int, *, field_name: str) -> int:
    """Validate a whole, non-negative token count for BIGINT storage."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a whole number of tokens")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    return currency


async def configure_private_llm_rate(
    db: AsyncSession,
    *,
    resource_type: str,
    hourly_rate: DecimalInput,
    effective_from: datetime | None = None,
    currency: str = "USD",
) -> FinancialRate:
    """Close the current resource rate and insert a new effective-dated value."""
    resource = _normalize_required_text(resource_type, field_name="resource_type")
    normalized_rate = financial_decimal(hourly_rate, field_name="hourly_rate")
    normalized_currency = _normalize_currency(currency)
    starts_at = effective_from or datetime.now(tz=timezone.utc)
    _require_aware_datetime(starts_at, field_name="effective_from")

    result = await db.execute(
        select(FinancialRate)
        .where(FinancialRate.cost_type == PRIVATE_LLM_COST_TYPE)
        .where(FinancialRate.resource_type == resource)
        .where(FinancialRate.effective_to.is_(None))
        .with_for_update()
    )
    current_rate = result.scalar_one_or_none()

    if current_rate is not None:
        if starts_at <= current_rate.effective_from:
            raise ValueError("effective_from must be later than the current rate start")
        current_rate.effective_to = starts_at
        db.add(current_rate)

    new_rate = FinancialRate(
        cost_type=PRIVATE_LLM_COST_TYPE,
        resource_type=resource,
        gpu_hourly_rate=normalized_rate,
        currency=normalized_currency,
        effective_from=starts_at,
    )
    db.add(new_rate)
    await db.commit()
    await db.refresh(new_rate)
    return new_rate


async def get_private_llm_rate_for_period(
    db: AsyncSession,
    *,
    resource_type: str,
    period_start: datetime,
    period_end: datetime,
) -> FinancialRate:
    """Return the single rate covering the entire half-open usage period."""
    resource = _normalize_required_text(resource_type, field_name="resource_type")
    _require_aware_datetime(period_start, field_name="period_start")
    _require_aware_datetime(period_end, field_name="period_end")
    if period_end <= period_start:
        raise ValueError("period_end must be later than period_start")

    result = await db.execute(
        select(FinancialRate)
        .where(FinancialRate.cost_type == PRIVATE_LLM_COST_TYPE)
        .where(FinancialRate.resource_type == resource)
        .where(FinancialRate.effective_from <= period_start)
        .where(
            or_(
                FinancialRate.effective_to.is_(None),
                FinancialRate.effective_to >= period_end,
            )
        )
        .order_by(FinancialRate.effective_from.desc())
        .limit(1)
    )
    rate = result.scalar_one_or_none()
    if rate is None:
        raise FinancialRateNotFoundError(
            f"No private LLM rate for resource '{resource}' covers the requested period"
        )
    return rate


async def configure_public_api_rate(
    db: AsyncSession,
    *,
    provider: str,
    model_name: str,
    input_rate_per_million_tokens: DecimalInput,
    output_rate_per_million_tokens: DecimalInput,
    effective_from: datetime | None = None,
    currency: str = "USD",
) -> FinancialRate:
    """Close the current model rate and insert a new effective-dated value."""
    normalized_provider = _normalize_required_text(provider, field_name="provider")
    normalized_model = _normalize_required_text(model_name, field_name="model_name")
    normalized_input_rate = financial_decimal(
        input_rate_per_million_tokens,
        field_name="input_rate_per_million_tokens",
    )
    normalized_output_rate = financial_decimal(
        output_rate_per_million_tokens,
        field_name="output_rate_per_million_tokens",
    )
    normalized_currency = _normalize_currency(currency)
    starts_at = effective_from or datetime.now(tz=timezone.utc)
    _require_aware_datetime(starts_at, field_name="effective_from")

    result = await db.execute(
        select(FinancialRate)
        .where(FinancialRate.cost_type == PUBLIC_API_COST_TYPE)
        .where(FinancialRate.provider == normalized_provider)
        .where(FinancialRate.model_name == normalized_model)
        .where(FinancialRate.effective_to.is_(None))
        .with_for_update()
    )
    current_rate = result.scalar_one_or_none()

    if current_rate is not None:
        if starts_at <= current_rate.effective_from:
            raise ValueError("effective_from must be later than the current rate start")
        current_rate.effective_to = starts_at
        db.add(current_rate)

    new_rate = FinancialRate(
        cost_type=PUBLIC_API_COST_TYPE,
        provider=normalized_provider,
        model_name=normalized_model,
        input_rate_per_million_tokens=normalized_input_rate,
        output_rate_per_million_tokens=normalized_output_rate,
        currency=normalized_currency,
        effective_from=starts_at,
    )
    db.add(new_rate)
    await db.commit()
    await db.refresh(new_rate)
    return new_rate


async def get_public_api_rate_for_period(
    db: AsyncSession,
    *,
    provider: str,
    model_name: str,
    period_start: datetime,
    period_end: datetime,
) -> FinancialRate:
    """Return the single model rate covering the entire half-open usage period."""
    normalized_provider = _normalize_required_text(provider, field_name="provider")
    normalized_model = _normalize_required_text(model_name, field_name="model_name")
    _require_aware_datetime(period_start, field_name="period_start")
    _require_aware_datetime(period_end, field_name="period_end")
    if period_end <= period_start:
        raise ValueError("period_end must be later than period_start")

    covering_rate = (
        select(FinancialRate)
        .where(FinancialRate.cost_type == PUBLIC_API_COST_TYPE)
        .where(FinancialRate.model_name == normalized_model)
        .where(FinancialRate.effective_from <= period_start)
        .where(
            or_(
                FinancialRate.effective_to.is_(None),
                FinancialRate.effective_to >= period_end,
            )
        )
        .order_by(FinancialRate.effective_from.desc())
        .limit(1)
    )

    result = await db.execute(
        covering_rate.where(FinancialRate.provider == normalized_provider)
    )
    rate = result.scalar_one_or_none()
    if rate is None:
        # A model is priced by name: the provider label only records the route
        # the gateway took, and the same model costs the same either way. Fall
        # back to the model's rate so a new routing label still bills.
        result = await db.execute(covering_rate)
        rate = result.scalar_one_or_none()
    if rate is None:
        raise FinancialRateNotFoundError(
            f"No public API rate for model '{normalized_provider}/{normalized_model}' "
            "covers the requested period"
        )
    return rate


async def get_cost_record_by_source_event(
    db: AsyncSession,
    *,
    source_event_id: str,
    cost_type: str = PRIVATE_LLM_COST_TYPE,
) -> FinancialCostRecord | None:
    """Find a previously ingested cost event of one cost type.

    Source event IDs are only unique per cost type, matching the partial
    unique index on ``(cost_type, source_event_id)``.
    """
    event_id = _normalize_required_text(source_event_id, field_name="source_event_id")
    result = await db.execute(
        select(FinancialCostRecord)
        .where(FinancialCostRecord.cost_type == cost_type)
        .where(FinancialCostRecord.source_event_id == event_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def record_private_llm_cost(
    db: AsyncSession,
    *,
    application_key: str,
    tier: int,
    resource_type: str,
    period_start: datetime,
    period_end: datetime,
    gpu_hours: DecimalInput,
    source_event_id: str | None = None,
    commit: bool = True,
) -> FinancialCostRecord:
    """Calculate and persist one immutable, idempotent private-LLM cost record."""
    application = _normalize_required_text(
        application_key, field_name="application_key"
    )
    resource = _normalize_required_text(resource_type, field_name="resource_type")
    if tier not in range(1, 5):
        raise ValueError("tier must be between 1 and 4")

    event_id = None
    if source_event_id is not None:
        event_id = _normalize_required_text(
            source_event_id, field_name="source_event_id"
        )
        existing = await get_cost_record_by_source_event(db, source_event_id=event_id)
        if existing is not None:
            return existing

    normalized_hours = financial_decimal(gpu_hours, field_name="gpu_hours")
    rate = await get_private_llm_rate_for_period(
        db,
        resource_type=resource,
        period_start=period_start,
        period_end=period_end,
    )
    hourly_rate = Decimal(rate.gpu_hourly_rate)
    calculated_cost = calculate_private_llm_cost(
        gpu_hours=normalized_hours,
        hourly_rate=hourly_rate,
    )

    record = FinancialCostRecord(
        application_key=application,
        tier=tier,
        cost_type=PRIVATE_LLM_COST_TYPE,
        period_start=period_start,
        period_end=period_end,
        rate_id=rate.id,
        resource_type=resource,
        gpu_hours=normalized_hours,
        gpu_hourly_rate=hourly_rate,
        calculated_cost=calculated_cost,
        currency=rate.currency,
        source_event_id=event_id,
    )
    db.add(record)

    if not commit:
        await db.flush()
        return record

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if event_id is not None:
            existing = await get_cost_record_by_source_event(
                db, source_event_id=event_id
            )
            if existing is not None:
                return existing
        raise

    await db.refresh(record)
    return record


async def record_public_api_cost(
    db: AsyncSession,
    *,
    application_key: str,
    tier: int,
    provider: str,
    model_name: str,
    period_start: datetime,
    period_end: datetime,
    input_tokens: int,
    output_tokens: int,
    source_event_id: str | None = None,
    commit: bool = True,
) -> FinancialCostRecord:
    """Calculate and persist one immutable, idempotent public-API cost record.

    The cost is the exact token spend for the interval: input and output tokens
    priced with the provider's published per-million rates, with no estimation.
    """
    application = _normalize_required_text(
        application_key, field_name="application_key"
    )
    normalized_provider = _normalize_required_text(provider, field_name="provider")
    normalized_model = _normalize_required_text(model_name, field_name="model_name")
    if tier not in range(1, 5):
        raise ValueError("tier must be between 1 and 4")
    normalized_input_tokens = _normalize_token_count(
        input_tokens, field_name="input_tokens"
    )
    normalized_output_tokens = _normalize_token_count(
        output_tokens, field_name="output_tokens"
    )

    event_id = None
    if source_event_id is not None:
        event_id = _normalize_required_text(
            source_event_id, field_name="source_event_id"
        )
        existing = await get_cost_record_by_source_event(
            db,
            source_event_id=event_id,
            cost_type=PUBLIC_API_COST_TYPE,
        )
        if existing is not None:
            return existing

    rate = await get_public_api_rate_for_period(
        db,
        provider=normalized_provider,
        model_name=normalized_model,
        period_start=period_start,
        period_end=period_end,
    )
    input_rate = Decimal(rate.input_rate_per_million_tokens)
    output_rate = Decimal(rate.output_rate_per_million_tokens)
    calculated_cost = calculate_public_api_cost(
        input_tokens=normalized_input_tokens,
        output_tokens=normalized_output_tokens,
        input_rate_per_million_tokens=input_rate,
        output_rate_per_million_tokens=output_rate,
    )

    record = FinancialCostRecord(
        application_key=application,
        tier=tier,
        cost_type=PUBLIC_API_COST_TYPE,
        period_start=period_start,
        period_end=period_end,
        rate_id=rate.id,
        provider=normalized_provider,
        model_name=normalized_model,
        input_tokens=normalized_input_tokens,
        output_tokens=normalized_output_tokens,
        input_rate_per_million_tokens=input_rate,
        output_rate_per_million_tokens=output_rate,
        calculated_cost=calculated_cost,
        currency=rate.currency,
        source_event_id=event_id,
    )
    db.add(record)

    if not commit:
        await db.flush()
        return record

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if event_id is not None:
            existing = await get_cost_record_by_source_event(
                db,
                source_event_id=event_id,
                cost_type=PUBLIC_API_COST_TYPE,
            )
            if existing is not None:
                return existing
        raise

    await db.refresh(record)
    return record


def _cost_decimal(value: object) -> Decimal:
    """Normalize database aggregate values to the financial storage precision."""
    return financial_decimal(value or 0, field_name="cost")


async def get_financial_cost_summary(
    db: AsyncSession,
    *,
    period_start: datetime,
    period_end: datetime,
    cost_type: str | None,
    tier: int | None = None,
    source_namespace: str | None = None,
) -> FinancialCostSummary:
    """Aggregate stored USD costs for a cluster or one tier using a half-open period."""
    _require_aware_datetime(period_start, field_name="period_start")
    _require_aware_datetime(period_end, field_name="period_end")
    if period_end <= period_start:
        raise ValueError("period_end must be later than period_start")
    if tier is not None and tier not in range(1, 5):
        raise ValueError("tier must be between 1 and 4")
    if cost_type not in (None, "public_api", PRIVATE_LLM_COST_TYPE):
        raise ValueError("cost_type must be public_api, private_llm, or None")
    namespace = None
    if source_namespace is not None:
        namespace = _normalize_required_text(
            source_namespace,
            field_name="source_namespace",
        )

    conditions = [
        FinancialCostRecord.period_start >= period_start,
        FinancialCostRecord.period_start < period_end,
        FinancialCostRecord.currency == USD_CURRENCY,
    ]
    if cost_type is not None:
        conditions.append(FinancialCostRecord.cost_type == cost_type)
    if tier is not None:
        conditions.append(FinancialCostRecord.tier == tier)
    if namespace is not None:
        conditions.append(FinancialCostRecord.application_key == namespace)

    total_result = await db.execute(
        select(func.coalesce(func.sum(FinancialCostRecord.calculated_cost), 0)).where(
            *conditions
        )
    )
    total_cost = _cost_decimal(total_result.scalar_one())

    if tier is None:
        tier_result = await db.execute(
            select(
                FinancialCostRecord.tier,
                func.sum(FinancialCostRecord.calculated_cost),
            )
            .where(*conditions)
            .group_by(FinancialCostRecord.tier)
            .order_by(FinancialCostRecord.tier)
        )
        found_tiers = {
            int(row_tier): _cost_decimal(cost) for row_tier, cost in tier_result.all()
        }
        tier_costs = {
            tier_number: found_tiers.get(tier_number, _cost_decimal(0))
            for tier_number in range(1, 5)
        }
        application_costs: tuple[ApplicationCostTotal, ...] = ()
    else:
        tier_costs = {tier: total_cost}
        application_result = await db.execute(
            select(
                FinancialCostRecord.application_key,
                FinancialCostRecord.tier,
                func.sum(FinancialCostRecord.calculated_cost),
            )
            .where(*conditions)
            .group_by(
                FinancialCostRecord.application_key,
                FinancialCostRecord.tier,
            )
            .order_by(FinancialCostRecord.application_key)
        )
        application_costs = tuple(
            ApplicationCostTotal(
                application_key=str(application_key),
                tier=int(application_tier),
                cost=_cost_decimal(cost),
            )
            for application_key, application_tier, cost in application_result.all()
        )

    return FinancialCostSummary(
        total_cost=total_cost,
        tier_costs=tier_costs,
        application_costs=application_costs,
    )

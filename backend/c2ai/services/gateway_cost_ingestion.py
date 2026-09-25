"""Price LLM gateway usage from Grafana counters by source namespace.

One poll queries the counter increase over the interval since the last
checkpoint and stores immutable cost records for both cost sources:

* Private LLM. Each namespace's callers accumulate request-duration seconds in
  the gateway's ``llm_duration_seconds`` histogram for the vLLM provider. Those
  seconds are billable GPU time (duration-hours) multiplied by the configured
  private GPU hourly rate.
* Public API. Every other provider reports the tokens it billed in
  ``llm_input_tokens_total`` and ``llm_output_tokens_total``, labelled with the
  provider and model. Those counts are priced exactly with the stored published
  per-million rate for that model, with no estimation involved.

Both sources share one checkpoint so an interval is priced exactly once, and a
request never lands in both: the two provider selectors are complementary.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.crud.financial import (
    FinancialRateNotFoundError,
    record_private_llm_cost,
    record_public_api_cost,
)
from c2ai.metrics.gateway import GatewayMetrics
from c2ai.models.financial import FinancialCostRecord, LlmGatewayTokenCheckpoint
from c2ai.schemas.grafana import (
    FRAME_VALUE_FIELD_INDEX,
    GrafanaFieldLabels,
    GrafanaFrame,
    GrafanaResponse,
)
from c2ai.services.financial import FINANCIAL_QUANTUM

logger = logging.getLogger(__name__)

DEFAULT_GPU_RESOURCE_TYPE = "default_gpu"
SECONDS_PER_HOUR = Decimal("3600")

# The query window (now - checkpoint) is driven by how often the scheduler
# polls. ``increase(llm_duration_seconds_sum[window])`` needs at least two
# counter samples, so a window shorter than roughly twice the Prometheus
# scrape interval returns a frame with no usable value. Ingesting such a
# window would advance the checkpoint and silently discard that usage, so we
# defer until the accumulated window is comfortably wider than the scrape
# interval. Override with ``ATHENA_FINANCIAL_MIN_WINDOW_SECONDS`` to match the
# actual scrape interval (the 900s default assumes a 5-minute scrape).


def _min_ingestion_window_seconds() -> int:
    """Minimum accumulated window before a poll queries and advances."""
    return get_settings().financial_min_window_seconds


GPU_TIER_LABEL_TO_NUMBER = {
    "tier1": 1,
    "tier2": 2,
    "tier3": 3,
    "prod": 3,
}


@dataclass(frozen=True)
class PublicModelUsageKey:
    """One namespace's usage of a single public provider and model."""

    application_key: str
    provider: str
    model_name: str


@dataclass(frozen=True)
class GatewayCostIngestionResult:
    """Outcome of one Grafana source-namespace interval query."""

    period_start: datetime | None
    period_end: datetime
    baseline_created: bool
    records: tuple[FinancialCostRecord, ...] = ()
    public_records: tuple[FinancialCostRecord, ...] = ()
    unmapped_namespaces: tuple[str, ...] = ()
    unpriced_models: tuple[str, ...] = ()
    observed_duration_seconds: Decimal = Decimal("0")
    attributed_duration_seconds: Decimal = Decimal("0")
    observed_public_tokens: Decimal = Decimal("0")
    attributed_public_tokens: Decimal = Decimal("0")


def _require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _frame_labels_and_value(
    frame: GrafanaFrame,
) -> tuple[GrafanaFieldLabels, Decimal] | None:
    """Return one frame's value-field labels and its latest usable value.

    Frames without a value field, without labels, or carrying only missing or
    negative samples contribute nothing to a counter increase.
    """
    if len(frame.schema_.fields) <= FRAME_VALUE_FIELD_INDEX:
        return None
    labels = frame.schema_.fields[FRAME_VALUE_FIELD_INDEX].labels
    if labels is None or len(frame.data.values) <= FRAME_VALUE_FIELD_INDEX:
        return None
    values = frame.data.values[FRAME_VALUE_FIELD_INDEX]
    numeric_value = next(
        (
            value
            for value in reversed(values)
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        ),
        None,
    )
    if numeric_value is None:
        return None
    return labels, Decimal(str(numeric_value))


def _response_has_frames(response: GrafanaResponse) -> bool:
    """Whether a datasource response carries any series at all."""
    result = response.results.get("A")
    return result is not None and bool(result.frames)


def parse_namespace_duration_totals(response: GrafanaResponse) -> dict[str, Decimal]:
    """Parse an instant duration ``increase`` result grouped by source namespace.

    Values are cumulative request-duration seconds. Frames sharing a namespace
    are summed, and only finite, non-negative values are kept.
    """
    totals: dict[str, Decimal] = {}
    result = response.results.get("A")
    if result is None:
        return {}

    for frame in result.frames:
        parsed = _frame_labels_and_value(frame)
        if parsed is None:
            continue
        labels, seconds = parsed
        namespace = (labels.source_namespace or "").strip()
        if namespace and seconds > 0:
            totals[namespace] = totals.get(namespace, Decimal("0")) + seconds
    return totals


def parse_namespace_model_token_totals(
    response: GrafanaResponse,
) -> dict[PublicModelUsageKey, Decimal]:
    """Parse a token ``increase`` result grouped by namespace, provider and model.

    Public-API pricing is per model, so a namespace calling two models produces
    two independently priced entries. Frames sharing a key are summed.
    """
    totals: dict[PublicModelUsageKey, Decimal] = {}
    result = response.results.get("A")
    if result is None:
        return {}

    for frame in result.frames:
        parsed = _frame_labels_and_value(frame)
        if parsed is None:
            continue
        labels, tokens = parsed
        namespace = (labels.source_namespace or "").strip()
        provider = (labels.provider or "").strip()
        model_name = (labels.model or "").strip()
        if not namespace or not provider or not model_name:
            # A charge cannot be priced without knowing which model billed it.
            continue
        if tokens <= 0:
            continue
        key = PublicModelUsageKey(
            application_key=namespace,
            provider=provider,
            model_name=model_name,
        )
        totals[key] = totals.get(key, Decimal("0")) + tokens
    return totals


def combine_public_token_usage(
    input_totals: dict[PublicModelUsageKey, Decimal],
    output_totals: dict[PublicModelUsageKey, Decimal],
) -> dict[PublicModelUsageKey, tuple[Decimal, Decimal]]:
    """Pair the separately queried input and output counters per model.

    A model that only produced one of the two counters in the interval still
    bills for the side it did produce, so the missing side becomes zero.
    """
    zero = Decimal("0")
    return {
        key: (input_totals.get(key, zero), output_totals.get(key, zero))
        for key in sorted(
            set(input_totals) | set(output_totals),
            key=lambda item: (item.application_key, item.provider, item.model_name),
        )
    }


def tokens_to_whole_count(tokens: Decimal) -> int:
    """Round a counter increase to the whole tokens stored on a cost record.

    ``increase()`` extrapolates over the window and returns a float, while
    token counts are whole numbers; the fractional part is far below one
    token's worth of cost either way.
    """
    return int(tokens.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_namespace_tier_mappings(response: GrafanaResponse) -> dict[str, int]:
    """Parse only namespaces with one unambiguous Kubernetes tier."""
    candidates: dict[str, set[int]] = {}
    result = response.results.get("A")
    if result is None:
        return {}

    for frame in result.frames:
        if len(frame.schema_.fields) <= FRAME_VALUE_FIELD_INDEX:
            continue
        labels = frame.schema_.fields[FRAME_VALUE_FIELD_INDEX].labels
        if labels is None:
            continue
        namespace = (labels.namespace or "").strip()
        tier = GPU_TIER_LABEL_TO_NUMBER.get((labels.label_tier or "").strip().lower())
        if namespace and tier is not None:
            candidates.setdefault(namespace, set()).add(tier)
    return {
        namespace: next(iter(tiers))
        for namespace, tiers in candidates.items()
        if len(tiers) == 1
    }


def duration_seconds_to_gpu_hours(duration_seconds: Decimal) -> Decimal:
    """Convert cumulative request-duration seconds to billable GPU-hours."""
    return (duration_seconds / SECONDS_PER_HOUR).quantize(FINANCIAL_QUANTUM)


def _source_event_id(prefix: str, parts: tuple[str, ...]) -> str:
    """Hash the identity of one charge so a replayed interval is stored once."""
    identity = "|".join(parts)
    return f"{prefix}:{hashlib.sha256(identity.encode()).hexdigest()}"


def build_gateway_cost_source_event_id(
    *,
    application_key: str,
    tier: int,
    period_start: datetime,
    period_end: datetime,
) -> str:
    """Create a deterministic identifier for a checkpoint interval allocation."""
    return _source_event_id(
        "gateway-gpu",
        (
            application_key,
            str(tier),
            period_start.astimezone(UTC).isoformat(),
            period_end.astimezone(UTC).isoformat(),
        ),
    )


def build_gateway_public_cost_source_event_id(
    *,
    application_key: str,
    tier: int,
    provider: str,
    model_name: str,
    period_start: datetime,
    period_end: datetime,
) -> str:
    """Create a deterministic identifier for one model's interval token charge.

    The model is part of the identity because a namespace can call several
    public models in the same interval, each priced as its own record.
    """
    return _source_event_id(
        "gateway-public",
        (
            application_key,
            str(tier),
            provider,
            model_name,
            period_start.astimezone(UTC).isoformat(),
            period_end.astimezone(UTC).isoformat(),
        ),
    )


async def _load_checkpoint_for_update(
    db: AsyncSession,
) -> LlmGatewayTokenCheckpoint | None:
    result = await db.execute(
        select(LlmGatewayTokenCheckpoint)
        .where(LlmGatewayTokenCheckpoint.id == 1)
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _checkpoint_counters() -> dict[str, str]:
    return {
        "source": "grafana",
        "metric": "llm_duration_seconds_sum",
        "group_by": "source_namespace",
        "public_metrics": "llm_input_tokens_total,llm_output_tokens_total",
        "public_group_by": "source_namespace,provider,model",
    }


async def _record_private_llm_costs(
    db: AsyncSession,
    *,
    duration_by_namespace: dict[str, Decimal],
    namespace_tiers: dict[str, int],
    resource_type: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[list[FinancialCostRecord], Decimal]:
    """Price each mapped namespace's GPU time for the interval."""
    records: list[FinancialCostRecord] = []
    attributed_duration = Decimal("0")

    for namespace, duration_seconds in sorted(duration_by_namespace.items()):
        tier = namespace_tiers.get(namespace)
        if tier is None:
            continue
        attributed_duration += duration_seconds
        gpu_hours = duration_seconds_to_gpu_hours(duration_seconds)
        if gpu_hours <= 0:
            # Sub-quantum usage (< 36µs of request time) rounds to zero
            # GPU-hours; there is nothing billable to record.
            continue
        records.append(
            await record_private_llm_cost(
                db,
                application_key=namespace,
                tier=tier,
                resource_type=resource_type,
                period_start=period_start,
                period_end=period_end,
                gpu_hours=gpu_hours,
                source_event_id=build_gateway_cost_source_event_id(
                    application_key=namespace,
                    tier=tier,
                    period_start=period_start,
                    period_end=period_end,
                ),
                commit=False,
            )
        )
    return records, attributed_duration


async def _record_public_api_costs(
    db: AsyncSession,
    *,
    public_usage: dict[PublicModelUsageKey, tuple[Decimal, Decimal]],
    namespace_tiers: dict[str, int],
    period_start: datetime,
    period_end: datetime,
) -> tuple[list[FinancialCostRecord], Decimal, tuple[str, ...]]:
    """Price each mapped namespace's public token usage per model."""
    records: list[FinancialCostRecord] = []
    attributed_tokens = Decimal("0")
    unpriced_models: set[str] = set()

    for key, (input_total, output_total) in public_usage.items():
        tier = namespace_tiers.get(key.application_key)
        if tier is None:
            continue
        attributed_tokens += input_total + output_total
        input_tokens = tokens_to_whole_count(input_total)
        output_tokens = tokens_to_whole_count(output_total)
        if input_tokens <= 0 and output_tokens <= 0:
            continue
        try:
            record = await record_public_api_cost(
                db,
                application_key=key.application_key,
                tier=tier,
                provider=key.provider,
                model_name=key.model_name,
                period_start=period_start,
                period_end=period_end,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                source_event_id=build_gateway_public_cost_source_event_id(
                    application_key=key.application_key,
                    tier=tier,
                    provider=key.provider,
                    model_name=key.model_name,
                    period_start=period_start,
                    period_end=period_end,
                ),
                commit=False,
            )
        except FinancialRateNotFoundError:
            # The rate lookup reads without writing, so the surrounding
            # transaction stays usable and the remaining models are still
            # priced. Configure the missing rate to start charging this model.
            unpriced_models.add(f"{key.provider}/{key.model_name}")
            continue
        records.append(record)

    return records, attributed_tokens, tuple(sorted(unpriced_models))


async def ingest_gateway_costs(
    db: AsyncSession,
    gateway: GatewayMetrics,
    *,
    observed_at: datetime | None = None,
    resource_type: str = DEFAULT_GPU_RESOURCE_TYPE,
    min_window_seconds: int | None = None,
) -> GatewayCostIngestionResult:
    """Query one Grafana interval and persist its private and public costs."""
    period_end = _require_aware(
        observed_at or datetime.now(tz=UTC),
        field_name="observed_at",
    )
    checkpoint = await _load_checkpoint_for_update(db)

    if checkpoint is None:
        db.add(
            LlmGatewayTokenCheckpoint(
                id=1,
                observed_at=period_end,
                counters=_checkpoint_counters(),
            )
        )
        await db.commit()
        return GatewayCostIngestionResult(
            period_start=None,
            period_end=period_end,
            baseline_created=True,
        )

    period_start = _require_aware(
        checkpoint.observed_at, field_name="checkpoint.observed_at"
    )
    if period_end <= period_start:
        raise ValueError("observed_at must be later than the stored checkpoint")

    minimum_window = (
        min_window_seconds
        if min_window_seconds is not None
        else _min_ingestion_window_seconds()
    )
    window_seconds = (period_end - period_start).total_seconds()
    if window_seconds < minimum_window:
        # Polling faster than the metric scrape interval yields a window with
        # fewer than two counter samples. ``increase`` then returns a frame
        # with no usable value, which would be ingested as zero usage and
        # advance the checkpoint, permanently discarding this slice. Keep the
        # checkpoint so a later poll queries the accumulated wider window once.
        logger.info(
            "Financial ingestion deferred: window %ss is below the %ss minimum; "
            "checkpoint remains at %s",
            int(window_seconds),
            minimum_window,
            period_start.isoformat(),
        )
        await db.rollback()
        return GatewayCostIngestionResult(
            period_start=period_start,
            period_end=period_end,
            baseline_created=False,
        )

    duration_response = (
        await gateway.fetch_llm_gateway_request_duration_by_namespace(
            period_start=period_start,
            period_end=period_end,
        )
    )
    input_token_response = (
        await gateway.fetch_llm_gateway_public_input_tokens_by_model(
            period_start=period_start,
            period_end=period_end,
        )
    )
    output_token_response = (
        await gateway.fetch_llm_gateway_public_output_tokens_by_model(
            period_start=period_start,
            period_end=period_end,
        )
    )

    if not any(
        _response_has_frames(response)
        for response in (duration_response, input_token_response, output_token_response)
    ):
        # A range shorter than the Prometheus scrape interval has fewer than
        # two counter samples, so ``increase`` returns no series. Do not move
        # the checkpoint: the next poll will retry from the same start time
        # with a larger range and will include the usage exactly once.
        logger.info(
            "Financial ingestion deferred: no complete counter samples "
            "for %s..%s; checkpoint remains at %s",
            period_start.isoformat(),
            period_end.isoformat(),
            period_start.isoformat(),
        )
        await db.rollback()
        return GatewayCostIngestionResult(
            period_start=period_start,
            period_end=period_end,
            baseline_created=False,
        )

    duration_by_namespace = parse_namespace_duration_totals(duration_response)
    public_usage = combine_public_token_usage(
        parse_namespace_model_token_totals(input_token_response),
        parse_namespace_model_token_totals(output_token_response),
    )

    records: list[FinancialCostRecord] = []
    public_records: list[FinancialCostRecord] = []
    unmapped_namespaces: tuple[str, ...] = ()
    unpriced_models: tuple[str, ...] = ()
    attributed_duration = Decimal("0")
    attributed_tokens = Decimal("0")

    if duration_by_namespace or public_usage:
        mapping_response = await gateway.fetch_llm_gateway_namespace_tiers(
            period_start=period_start,
            period_end=period_end,
        )
        namespace_tiers = parse_namespace_tier_mappings(mapping_response)
        used_namespaces = set(duration_by_namespace) | {
            key.application_key for key in public_usage
        }
        unmapped_namespaces = tuple(sorted(used_namespaces - set(namespace_tiers)))
        if unmapped_namespaces:
            logger.warning(
                "Ignoring gateway usage for namespaces without one tier: %s",
                ", ".join(unmapped_namespaces),
            )

        records, attributed_duration = await _record_private_llm_costs(
            db,
            duration_by_namespace=duration_by_namespace,
            namespace_tiers=namespace_tiers,
            resource_type=resource_type,
            period_start=period_start,
            period_end=period_end,
        )
        (
            public_records,
            attributed_tokens,
            unpriced_models,
        ) = await _record_public_api_costs(
            db,
            public_usage=public_usage,
            namespace_tiers=namespace_tiers,
            period_start=period_start,
            period_end=period_end,
        )
        if unpriced_models:
            logger.warning(
                "Skipping public API usage for models without a configured rate: %s",
                ", ".join(unpriced_models),
            )

    checkpoint.observed_at = period_end
    checkpoint.counters = _checkpoint_counters()
    db.add(checkpoint)
    await db.commit()
    return GatewayCostIngestionResult(
        period_start=period_start,
        period_end=period_end,
        baseline_created=False,
        records=tuple(records),
        public_records=tuple(public_records),
        unmapped_namespaces=unmapped_namespaces,
        unpriced_models=unpriced_models,
        observed_duration_seconds=sum(
            duration_by_namespace.values(), Decimal("0")
        ),
        attributed_duration_seconds=attributed_duration,
        observed_public_tokens=sum(
            (
                input_total + output_total
                for input_total, output_total in public_usage.values()
            ),
            Decimal("0"),
        ),
        attributed_public_tokens=attributed_tokens,
    )

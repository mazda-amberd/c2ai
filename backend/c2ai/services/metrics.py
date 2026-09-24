"""Builds the ``/api/v2/metrics`` payload from the Story 1.2-1.4 dashboard queries.

All queries for a level go out in one Grafana request with a refId each. Grafana reports
status per refId, so a single failing metric degrades that metric only while the rest of
the payload is returned with ``degraded=true``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from c2ai.clients.grafana import GrafanaClient
from c2ai.config import get_settings
from c2ai.constants import metrics_queries as mq
from c2ai.constants.prometheus import (
    excluded_deployment_names,
    get_grafana_prometheus_datasource,
)
from c2ai.constants.time_ranges import Window
from c2ai.schemas.metrics import (
    MetricLevel,
    MetricSeries,
    MetricsError,
    MetricsResponse,
    MetricUnit,
    MetricValue,
    Scope,
    WindowOut,
)
from c2ai.services.instance_metadata import InstanceMetadata
from c2ai.utils.host_labels import split_workflow_host_label

logger = logging.getLogger(__name__)

# The dashboard refreshes once a minute, matching the Prometheus scrape interval.
REFRESH_SECONDS = 60
_CACHE_TTL_SECONDS = 60
_GRAFANA_ERROR = "Grafana request failed"

# Tier namespaces as defined by the Story 1.3 dashboard's `tier` variable.
TIER_NAMESPACES = ("tier1", "tier2", "tier3", "tier4")

MetadataLoader = Callable[[list[str]], Awaitable[dict[str, InstanceMetadata]]]

_cache: dict[tuple, tuple[float, MetricsResponse]] = {}


class MetricsUnavailable(RuntimeError):
    """The Grafana request for the requested level failed outright."""


@dataclass(frozen=True)
class _Query:
    """One PromQL expression and where its result belongs in the response."""

    ref: str
    metric: str
    unit: MetricUnit
    expr: str
    scope_id: str | None = None  # None means the scope comes from result labels


def _cache_key(level: MetricLevel, window: Window, tier: int | None) -> tuple:
    bounds = window.preset or (window.start.isoformat(), window.end.isoformat())
    return (level.value, bounds, None if level is MetricLevel.CLUSTER else tier)


def _store(key: tuple, response: MetricsResponse) -> None:
    """Cache a payload, dropping expired entries so custom windows cannot grow it forever."""
    now = time.monotonic()
    for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
        del _cache[stale]
    _cache[key] = (now + _CACHE_TTL_SECONDS, response)


def _scrape_interval() -> int:
    """Prometheus scrape interval in seconds. Override with ATHENA_SCRAPE_INTERVAL_SECONDS."""
    return get_settings().scrape_interval_seconds


def _macros(window: Window) -> tuple[str, str]:
    """Grafana's ``$__range`` and ``$__rate_interval`` for this window.

    ``$__rate_interval`` is ``max(step + scrape, 4 * scrape)`` — Grafana's own formula.
    A shorter lookback holds too few samples to be stable and drifts from the panel.
    """
    span = max(60, int((window.end - window.start).total_seconds()))
    scrape = _scrape_interval()
    rate = max(window.step_seconds + scrape, 4 * scrape)
    return f"{span}s", f"{rate}s"


def _tier_indexes(tier: int | None) -> list[int]:
    if tier is None:
        return list(range(1, len(TIER_NAMESPACES) + 1))
    return [tier] if 1 <= tier <= len(TIER_NAMESPACES) else []


def _build_queries(level: MetricLevel, window: Window, tier: int | None) -> list[_Query]:
    """Every query for this level, each with a unique refId."""
    rng, rate = _macros(window)
    queries: list[_Query] = []

    def add(catalogue: dict[str, tuple[str, MetricUnit]], scope_id: str | None) -> None:
        for metric, (expr, unit) in catalogue.items():
            queries.append(_Query(f"q{len(queries)}", metric, unit, expr, scope_id))

    if level is MetricLevel.CLUSTER:
        add(mq.cluster_queries(rng, rate), "cluster")
    elif level is MetricLevel.TIER:
        for index in _tier_indexes(tier):
            add(mq.tier_queries(TIER_NAMESPACES[index - 1], rng, rate), f"tier-{index}")
    else:
        add(mq.application_queries(rng, rate), None)
    return queries



async def _fetch(client: GrafanaClient, window: Window, queries: list[_Query]) -> dict[str, Any]:
    """Run every query in one Grafana request, evaluated at the window's end."""
    body = {
        "queries": [
            {
                "refId": q.ref,
                "datasource": get_grafana_prometheus_datasource(),
                "expr": q.expr,
                "instant": True,
            }
            for q in queries
        ],
        "from": str(int(window.start.timestamp() * 1000)),
        "to": str(int(window.end.timestamp() * 1000)),
    }
    return await client.fetch_grafana_query_raw(body)


def _frames(payload: dict[str, Any], ref: str) -> tuple[list[dict], str | None]:
    """Frames for one refId plus its error, if Grafana reported one."""
    block = (payload.get("results") or {}).get(ref)
    if not isinstance(block, dict):
        return [], "no result"
    error = block.get("error")
    if error:
        return [], str(error)
    if isinstance(block.get("status"), int) and block["status"] >= 400:
        return [], f"status {block['status']}"
    return [f for f in (block.get("frames") or []) if isinstance(f, dict)], None


def _labels_and_value(frame: dict) -> tuple[dict[str, str], float | None]:
    """Series labels and the single instant value carried by a frame."""
    fields = (frame.get("schema") or {}).get("fields") or []
    labels = {}
    for field in fields:
        if isinstance(field.get("labels"), dict):
            labels.update({str(k): str(v) for k, v in field["labels"].items() if v is not None})
    values = (frame.get("data") or {}).get("values") or []
    cell = values[1][0] if len(values) > 1 and values[1] else None
    return labels, cell if isinstance(cell, (int, float)) else None


def _round(value: float) -> float:
    return round(value * 1000) / 1000


def _scalar(frames: list[dict]) -> float | None:
    """Single value for a scope-level metric; None when Grafana returned nothing."""
    total = 0.0
    seen = False
    for frame in frames:
        _, value = _labels_and_value(frame)
        if value is not None:
            total += value
            seen = True
    return _round(total) if seen else None


def build_series(
    payload: dict[str, Any],
    queries: list[_Query],
    metadata: dict[str, InstanceMetadata],
) -> tuple[list[MetricSeries], list[MetricsError]]:
    """Turn one Grafana payload into the response series plus any per-metric errors."""
    errors: list[MetricsError] = []
    fixed: dict[str, dict[str, MetricValue]] = {}
    dynamic: dict[tuple[str, str], dict[str, MetricValue]] = {}
    excluded = excluded_deployment_names()

    for query in queries:
        frames, error = _frames(payload, query.ref)
        if error:
            logger.warning("Grafana metric %s failed: %s", query.metric, error)
            errors.append(MetricsError(metric=query.metric, message=_GRAFANA_ERROR))

        if query.scope_id is not None:
            bucket = fixed.setdefault(query.scope_id, {})
            value = None if error else _scalar(frames)
            bucket[query.metric] = MetricValue(
                value=value, unit=query.unit, available=value is not None
            )
            continue

        for frame in frames:
            labels, value = _labels_and_value(frame)
            namespace, deployment = labels.get("namespace"), labels.get("deployment")
            if not namespace or not deployment or deployment.lower() in excluded:
                continue
            dynamic.setdefault((namespace, deployment), {})[query.metric] = MetricValue(
                value=_round(value) if value is not None else None,
                unit=query.unit,
                available=value is not None,
            )

    series = [
        MetricSeries(scope=_fixed_scope(scope_id), metrics=metrics)
        for scope_id, metrics in fixed.items()
    ]
    # Every scope carries every key for its level: an app that emits no LLM or HTTP
    # metrics reports them unavailable rather than omitting them.
    expected = {q.metric: q.unit for q in queries if q.scope_id is None}
    for metrics in dynamic.values():
        for metric, unit in expected.items():
            metrics.setdefault(metric, MetricValue(value=None, unit=unit, available=False))

    for (namespace, deployment), metrics in sorted(dynamic.items()):
        meta = metadata.get(namespace)
        client_name, instance_name = (
            (meta.client_name, meta.instance_name)
            if meta
            else split_workflow_host_label(namespace)
        )
        series.append(
            MetricSeries(
                scope=Scope(
                    kind="application",
                    id=f"{namespace}/{deployment}",
                    name=deployment,
                    subdomain=namespace,
                    client_name=client_name,
                    instance_name=instance_name,
                ),
                metrics=metrics,
            )
        )
    return series, errors


def _fixed_scope(scope_id: str) -> Scope:
    """Scope for cluster and tier, whose identity is known before the query runs."""
    if scope_id == "cluster":
        return Scope(kind="cluster", id="cluster", name="Cluster")
    index = int(scope_id.split("-")[1])
    return Scope(kind="tier", id=scope_id, name=f"Tier {index}", tier=index)


async def build_metrics(
    client: GrafanaClient,
    level: MetricLevel,
    window: Window,
    tier: int | None = None,
    metadata_loader: MetadataLoader | None = None,
) -> MetricsResponse:
    """Fetch, normalise, and cache one level's metrics for the given window."""
    key = _cache_key(level, window, tier)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    queries = _build_queries(level, window, None if level is MetricLevel.CLUSTER else tier)
    if not queries:
        raise MetricsUnavailable("No metrics defined for the requested scope")

    try:
        payload = await _fetch(client, window, queries)
    except Exception as exc:
        logger.warning("Grafana request failed for level %s: %s", level.value, exc)
        raise MetricsUnavailable(_GRAFANA_ERROR) from exc

    metadata = {}
    if level is MetricLevel.APPLICATION and metadata_loader:
        namespaces = set()
        for query in queries:
            for frame in _frames(payload, query.ref)[0]:
                namespace = _labels_and_value(frame)[0].get("namespace")
                if namespace:
                    namespaces.add(namespace)
        metadata = await metadata_loader(sorted(namespaces))

    series, errors = build_series(payload, queries, metadata)

    response = MetricsResponse(
        level=level,
        window=WindowOut(
            start=window.start,
            end=window.end,
            preset=window.preset,
            step_seconds=window.step_seconds,
        ),
        generated_at=datetime.now(UTC),
        refresh_after_seconds=REFRESH_SECONDS,
        series=series,
        degraded=bool(errors),
        errors=errors,
    )
    _store(key, response)
    return response


def clear_cache() -> None:
    """Drop cached payloads — used by tests."""
    _cache.clear()

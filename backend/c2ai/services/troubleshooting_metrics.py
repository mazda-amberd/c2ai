"""Configured PromQL catalog loading and Grafana metric retrieval."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any

from c2ai.schemas.grafana import GrafanaResponse
from c2ai.clients.grafana import GrafanaClient
from c2ai.constants.prometheus import get_grafana_prometheus_datasource
from c2ai.schemas.troubleshooting import (
    TroubleshootingMetric,
    TroubleshootingMetricPoint,
    TroubleshootingMetricQuery,
)

_METRIC_QUERY_CATALOG_PATH = (
    Path(__file__).parents[1] / "constants" / "troubleshooting_metric_queries.json"
)
_RAY_CLUSTER_BY_TIER = {
    1: "qwen-5254d",
    2: "qwen-pq9sc",
    3: "qwen-l8dnl",
}
_BRACED_VARIABLE_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]*)?\}")
_PLAIN_VARIABLE_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")
_LEGACY_VARIABLE_RE = re.compile(r"\[\[([a-zA-Z_][a-zA-Z0-9_]*)\]\]")
_UNRESOLVED_VARIABLE_RE = re.compile(
    r"\$\{?[a-zA-Z_][a-zA-Z0-9_]*(?::[^}]*)?\}?|"
    r"\[\[[a-zA-Z_][a-zA-Z0-9_]*\]\]"
)


def _escape_promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _metric_variables(
    *,
    namespace: str,
    deployment: str,
    tier: int | None,
    start: datetime,
    end: datetime,
) -> dict[str, str]:
    window_seconds = max(1, int((end - start).total_seconds()))
    step_seconds = max(15, ceil(window_seconds / 119))
    ray_cluster = _RAY_CLUSTER_BY_TIER.get(
        tier,
        "|".join(_RAY_CLUSTER_BY_TIER.values()),
    )
    variables = {
        "namespace": namespace,
        "app": deployment,
        "deployment": deployment,
        "tier": f"tier{tier}" if tier is not None else "tier1|tier2|tier3",
        "ray_cluster": ray_cluster,
        "datasource": get_grafana_prometheus_datasource()["uid"],
        "__interval": _duration(step_seconds),
        "__interval_ms": str(step_seconds * 1000),
        "__rate_interval": _duration(max(60, step_seconds * 4)),
        "__range": _duration(window_seconds),
        "__range_s": str(window_seconds),
        "__range_ms": str(window_seconds * 1000),
    }
    return {
        name: _escape_promql_string(value)
        for name, value in variables.items()
    }


def _resolve_metric_variables(expr: str, variables: dict[str, str]) -> str | None:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return variables.get(name, match.group(0))

    resolved = _BRACED_VARIABLE_RE.sub(replace, expr)
    resolved = _LEGACY_VARIABLE_RE.sub(replace, resolved)
    resolved = _PLAIN_VARIABLE_RE.sub(replace, resolved)
    if _UNRESOLVED_VARIABLE_RE.search(resolved):
        return None
    return resolved


def _catalog_expression(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        return "\n".join(line.rstrip() for line in value).strip()
    raise ValueError("A troubleshooting metric query has an invalid expression.")


def load_configured_metric_queries(
    *,
    namespace: str,
    deployment: str,
    tier: int | None,
    start: datetime,
    end: datetime,
    catalog_path: Path = _METRIC_QUERY_CATALOG_PATH,
) -> list[TroubleshootingMetricQuery]:
    """Load the trusted JSON catalog and resolve it for one application."""

    try:
        payload: Any = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not load the troubleshooting metric catalog.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The troubleshooting metric catalog is invalid.")
    entries = payload.get("metrics")
    if not isinstance(entries, list):
        raise ValueError("The troubleshooting metric catalog has no metric list.")

    variables = _metric_variables(
        namespace=namespace,
        deployment=deployment,
        tier=tier,
        start=start,
        end=end,
    )
    queries: list[TroubleshootingMetricQuery] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("The troubleshooting metric catalog entry is invalid.")
        query_id = entry.get("id")
        label = entry.get("label")
        unit = entry.get("unit", "")
        value_divisor = entry.get("value_divisor", 1.0)
        if not all(
            isinstance(value, str) and value.strip()
            for value in (query_id, label)
        ):
            raise ValueError("A troubleshooting metric catalog identity is invalid.")
        if not isinstance(unit, str):
            raise ValueError("A troubleshooting metric catalog unit is invalid.")
        if (
            not isinstance(value_divisor, (int, float))
            or isinstance(value_divisor, bool)
            or value_divisor <= 0
        ):
            raise ValueError("A troubleshooting metric value divisor is invalid.")
        expr = _resolve_metric_variables(
            _catalog_expression(entry.get("expr")),
            variables,
        )
        if expr is None:
            raise ValueError(f"Metric query {query_id!r} has unresolved variables.")
        if query_id in seen_ids:
            continue
        seen_ids.add(query_id)
        queries.append(
            TroubleshootingMetricQuery(
                id=query_id,
                label=label,
                expr=expr,
                unit=unit,
                value_divisor=float(value_divisor),
            )
        )
    return queries


def select_available_metric_queries(
    query_ids: list[str],
    available_queries: list[TroubleshootingMetricQuery],
) -> list[TroubleshootingMetricQuery]:
    """Resolve at most four model-selected IDs from the trusted query catalog."""

    available = {query.id: query for query in available_queries}
    selected: list[TroubleshootingMetricQuery] = []
    seen: set[str] = set()
    for raw in query_ids:
        query_id = raw.strip()
        query = available.get(query_id)
        if query is not None and query_id not in seen:
            selected.append(query)
            seen.add(query_id)
        if len(selected) == 4:
            break
    return selected


def unavailable_metrics(
    queries: list[TroubleshootingMetricQuery],
) -> list[TroubleshootingMetric]:
    return [
        TroubleshootingMetric(
            name=query.id,
            label=query.label,
            unit=query.unit,
        )
        for query in queries[:4]
    ]


def _point_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = (
            float(value) / 1000 if abs(float(value)) > 100_000_000_000 else float(value)
        )
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _metric_points(
    response: GrafanaResponse,
    ref_id: str,
    start: datetime,
    end: datetime,
) -> list[TroubleshootingMetricPoint]:
    result = response.results.get(ref_id)
    if result is None:
        return []
    points_by_timestamp: dict[datetime, float] = {}
    for frame in result.frames:
        if len(frame.data.values) < 2:
            continue
        timestamps = frame.data.values[0]
        values = frame.data.values[1]
        for raw_timestamp, raw_value in zip(timestamps, values):
            timestamp = _point_timestamp(raw_timestamp)
            if (
                timestamp is not None
                and start <= timestamp <= end
                and isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
            ):
                points_by_timestamp[timestamp] = float(raw_value)
    return [
        TroubleshootingMetricPoint(timestamp=timestamp, value=value)
        for timestamp, value in sorted(points_by_timestamp.items())[-120:]
    ]


async def query_application_metrics(
    client: GrafanaClient,
    *,
    queries: list[TroubleshootingMetricQuery],
    start: datetime,
    end: datetime,
) -> list[TroubleshootingMetric]:
    """Execute four LLM-selected expressions from the trusted query catalog."""

    selected = queries[:4]
    if not selected:
        return []
    datasource = get_grafana_prometheus_datasource()
    ref_ids = [chr(ord("A") + index) for index in range(len(selected))]
    window_seconds = max(1, (end - start).total_seconds())
    step_seconds = max(15, ceil(window_seconds / 119))
    body = {
        "queries": [
            {
                "refId": ref_id,
                "datasource": datasource,
                "expr": query.expr,
                "instant": False,
                "range": True,
                "intervalMs": step_seconds * 1000,
                "maxDataPoints": 120,
            }
            for ref_id, query in zip(ref_ids, selected)
        ],
        "from": str(int(start.timestamp() * 1000)),
        "to": str(int(end.timestamp() * 1000)),
    }
    response = await client.fetch_grafana_query(body)
    metrics: list[TroubleshootingMetric] = []
    for ref_id, query in zip(ref_ids, selected):
        points = [
            point.model_copy(update={"value": point.value / query.value_divisor})
            for point in _metric_points(response, ref_id, start, end)
        ]
        metrics.append(
            TroubleshootingMetric(
                name=query.id,
                label=query.label,
                value=points[-1].value if points else None,
                unit=query.unit,
                points=points,
            )
        )
    return metrics

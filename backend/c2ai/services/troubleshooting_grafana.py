"""Grafana Loki provider for live troubleshooting evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from c2ai.clients.grafana import GrafanaClient
from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingMetric,
    TroubleshootingMetricQuery,
)
from c2ai.services.loki_logs import (
    escape_logql_label_value,
    iter_loki_rows_from_ds_query_payload,
    loki_row_to_deployment_entry,
)
from c2ai.services.troubleshooting_metrics import (
    load_configured_metric_queries,
    query_application_metrics,
)

logger = logging.getLogger(__name__)

_DASHBOARD_UID = "adwq7wr"
_SOURCE_WEIGHTS = (70, 20, 5, 5)
_EVENT_JOB_MATCHER = ".*(kubernetes.*event|eventhandler).*"
_CLUSTER_KINDS = "Node|PersistentVolume|VolumeAttachment|CSINode"
_EVENT_SEPARATOR_RE = re.compile(r"\s+\|\s+")


def is_grafana_troubleshooting_configured() -> bool:
    return bool(
        os.getenv("GRAFANA_API_URL", "").strip()
        and os.getenv("GRAFANA_LOKI_DATASOURCE_UID", "").strip()
    )


def build_application_logs_query(namespace: str, deployment: str) -> str:
    ns = escape_logql_label_value(namespace)
    app = escape_logql_label_value(deployment)
    return f'{{namespace="{ns}", app="{app}"}}'


def build_namespace_events_query(namespace: str) -> str:
    ns = escape_logql_label_value(namespace)
    return (
        f'{{job=~"{_EVENT_JOB_MATCHER}", namespace="{ns}"}} '
        '| json | line_format "{{.type}} | {{.reason}} | '
        '{{.kind}}/{{.name}} | {{.msg}}"'
    )


def build_cluster_events_query() -> str:
    return (
        f'{{job=~"{_EVENT_JOB_MATCHER}"}} | json '
        f'| kind=~"{_CLUSTER_KINDS}" '
        '| line_format "{{.type}} | {{.reason}} | {{.kind}}/{{.name}} '
        '| ns={{.namespace}} | {{.msg}}"'
    )


def build_system_events_query() -> str:
    return (
        f'{{job=~"{_EVENT_JOB_MATCHER}", namespace="kube-system"}} '
        '| json | type="Warning" | line_format "{{.type}} | {{.reason}} '
        '| {{.kind}}/{{.name}} | ns=kube-system | {{.msg}}"'
    )


def _allocate_limits(limit: int) -> tuple[int, int, int, int]:
    if limit <= 0:
        return (0, 0, 0, 0)
    limits = [limit * weight // 100 for weight in _SOURCE_WEIGHTS]
    for index in range(limit - sum(limits)):
        limits[index % len(limits)] += 1
    return tuple(limits)  # type: ignore[return-value]


def _safe_attributes(labels: dict[str, str]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for key, value in sorted(labels.items()):
        if key == "_grafana_line_id" or len(attributes) >= 30:
            continue
        attributes[str(key)[:100]] = str(value)[:500]
    attributes["grafana_dashboard_uid"] = _DASHBOARD_UID
    return attributes


def _event_id(timestamp_ns: int, line: str, labels: dict[str, str]) -> str:
    stable_labels = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{timestamp_ns}\x00{line}\x00{stable_labels}".encode()
    ).hexdigest()[:24]
    return f"k8s-{digest}"


def _timestamp_from_ns(timestamp_ns: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=UTC)


def _parse_json_event(line: str) -> dict[str, str] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return {
        str(key): str(raw)
        for key, raw in value.items()
        if raw is not None
        and key in {"type", "reason", "kind", "name", "msg", "message", "namespace"}
    }


def _application_message(line: str, fallback: str) -> str:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return fallback
    if not isinstance(value, dict):
        return fallback
    for key in ("message", "msg", "error", "event"):
        message = value.get(key)
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


def _parse_formatted_event(
    line: str,
    labels: dict[str, str],
) -> tuple[str, str | None, str, str, dict[str, str]]:
    parsed_json = _parse_json_event(line)
    if parsed_json is not None:
        event_type = parsed_json.get("type", "Event")
        reason = parsed_json.get("reason")
        kind = parsed_json.get("kind", "Resource")
        name = parsed_json.get("name", "unknown")
        resource = f"{kind}/{name}"
        message = parsed_json.get("msg") or parsed_json.get("message") or line
        attributes = dict(labels)
        if parsed_json.get("namespace"):
            attributes["namespace"] = parsed_json["namespace"]
        return event_type, reason, resource, message, attributes

    parts = _EVENT_SEPARATOR_RE.split(line.strip())
    event_type = parts[0].strip() if parts else "Event"
    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    resource = parts[2].strip() if len(parts) > 2 else "Resource/unknown"
    attributes = dict(labels)
    message_index = 3
    if len(parts) > 4 and parts[3].startswith("ns="):
        attributes["namespace"] = parts[3][3:].strip()
        message_index = 4
    message = " | ".join(parts[message_index:]).strip() or line.strip()
    return event_type, reason, resource, message, attributes


def _application_events(
    payload: dict[str, Any],
    deployment: str,
) -> list[TroubleshootingEvent]:
    events: list[TroubleshootingEvent] = []
    for timestamp_ns, line, labels in iter_loki_rows_from_ds_query_payload(payload):
        entry = loki_row_to_deployment_entry(
            timestamp_ns,
            line,
            labels,
            deployment,
        )
        resource = labels.get("pod") or labels.get("container") or deployment
        events.append(
            TroubleshootingEvent(
                id=f"app-{entry.id}",
                timestamp=_timestamp_from_ns(timestamp_ns),
                source="application",
                severity=entry.level,
                resource=resource,
                event_type="application-log",
                reason=labels.get("reason"),
                message=_application_message(line, entry.message)[:4_000],
                attributes=_safe_attributes(labels),
            )
        )
    return events


def _kubernetes_events(payload: dict[str, Any]) -> list[TroubleshootingEvent]:
    events: list[TroubleshootingEvent] = []
    for timestamp_ns, line, labels in iter_loki_rows_from_ds_query_payload(payload):
        event_type, reason, resource, message, attributes = _parse_formatted_event(
            line,
            labels,
        )
        severity = "warning" if event_type.lower() == "warning" else "info"
        events.append(
            TroubleshootingEvent(
                id=_event_id(timestamp_ns, line, labels),
                timestamp=_timestamp_from_ns(timestamp_ns),
                source="kubernetes",
                severity=severity,
                resource=resource,
                event_type=event_type,
                reason=reason,
                message=message[:4_000],
                attributes=_safe_attributes(attributes),
            )
        )
    return events


class GrafanaTroubleshootingDataProvider:
    """Collect the four evidence pools defined by Hayk's Grafana dashboard."""

    def __init__(self, client: GrafanaClient | None = None):
        self._client = client

    def _get_client(self) -> GrafanaClient:
        if self._client is None:
            self._client = GrafanaClient()
        return self._client

    async def collect_events(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[TroubleshootingEvent]:
        _ = tier
        limits = _allocate_limits(limit)
        queries = (
            build_application_logs_query(subdomain, deployment),
            build_namespace_events_query(subdomain),
            build_cluster_events_query(),
            build_system_events_query(),
        )
        start_ms = int(start.timestamp() * 1_000)
        end_ms = int(end.timestamp() * 1_000)
        client = self._get_client()

        async def query(logql: str, source_limit: int) -> dict[str, Any]:
            if source_limit <= 0:
                return {"results": {}}
            return await client.query_loki_range(
                logql,
                start_ms,
                end_ms,
                max_lines=source_limit,
                direction="backward",
            )

        results = await asyncio.gather(
            *(
                query(logql, source_limit)
                for logql, source_limit in zip(queries, limits)
            ),
            return_exceptions=True,
        )
        successful_payloads = [result for result in results if isinstance(result, dict)]
        if not successful_payloads:
            first_error = next(
                (result for result in results if isinstance(result, BaseException)),
                None,
            )
            logger.error("All Grafana troubleshooting queries failed: %s", first_error)
            raise ServiceUnavailableError(
                "Could not retrieve troubleshooting evidence from Grafana.",
                code="TroubleshootingDataUnavailable",
            )
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Grafana troubleshooting query failed source_index=%s: %s",
                    index,
                    result,
                )

        events: list[TroubleshootingEvent] = []
        if isinstance(results[0], dict):
            events.extend(_application_events(results[0], deployment))
        for result in results[1:]:
            if isinstance(result, dict):
                events.extend(_kubernetes_events(result))

        unique_events = {event.id: event for event in events}
        newest = sorted(
            unique_events.values(),
            key=lambda event: (event.timestamp, event.id),
            reverse=True,
        )[:limit]
        return sorted(newest, key=lambda event: (event.timestamp, event.id))

    async def collect_metrics(
        self,
        *,
        queries: list[TroubleshootingMetricQuery],
        subdomain: str,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetric]:
        """Execute the AI-selected trusted PromQL expressions."""

        _ = subdomain
        return await query_application_metrics(
            self._get_client(),
            queries=queries,
            start=start,
            end=end,
        )

    async def list_metric_queries(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetricQuery]:
        """Read and resolve every PromQL entry from the trusted JSON catalog."""

        return load_configured_metric_queries(
            namespace=subdomain,
            deployment=deployment,
            tier=tier,
            start=start,
            end=end,
        )


_grafana_provider = GrafanaTroubleshootingDataProvider()


def get_grafana_troubleshooting_data_provider() -> GrafanaTroubleshootingDataProvider:
    return _grafana_provider

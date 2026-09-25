"""Build grounded troubleshooting reports (shared by /report and the job worker)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from c2ai.core.exceptions import AppException, ServiceUnavailableError
from c2ai.llm.provider import get_qa_llm
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingJobStatus,
    TroubleshootingReportRequest,
    TroubleshootingReportResponse,
    TroubleshootingWindow,
)
from c2ai.services.troubleshooting import (
    analyse_events,
    select_troubleshooting_events,
)
from c2ai.services.troubleshooting_data import (
    TroubleshootingDataProvider,
    get_troubleshooting_data_provider,
)
from c2ai.services.troubleshooting_metrics import (
    select_available_metric_queries,
    unavailable_metrics,
)

logger = logging.getLogger(__name__)

_LOG_LINE_LIMIT = 100
_CLUSTER_EVENT_LIMIT = 10
_SEVERITY_RANK = {
    "debug": 0,
    "trace": 1,
    "info": 2,
    "warning": 3,
    "error": 4,
    "fatal": 5,
    "critical": 6,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def troubleshooting_llm_provider() -> Callable[[], Any]:
    """Return a lazy provider so empty-log reports do not initialize the model."""

    return get_qa_llm


def troubleshooting_data_provider() -> TroubleshootingDataProvider:
    """Where report evidence comes from (the API dependency and the job worker)."""

    return get_troubleshooting_data_provider()


def _events_for_ids(
    ids: list[str],
    events_by_id: dict[str, TroubleshootingEvent],
) -> list[TroubleshootingEvent]:
    seen: set[str] = set()
    selected: list[TroubleshootingEvent] = []
    for event_id in ids:
        event = events_by_id.get(event_id)
        if event is not None and event_id not in seen:
            seen.add(event_id)
            selected.append(event)
    return sorted(selected, key=lambda event: (event.timestamp, event.id))


def _fallback_critical_event(
    events: list[TroubleshootingEvent],
) -> TroubleshootingEvent | None:
    if not events:
        return None
    return max(
        events,
        key=lambda event: (
            _SEVERITY_RANK[event.severity],
            event.timestamp,
            event.id,
        ),
    )


def _select_cluster_events(
    model_cluster_ids: list[str],
    model_relevant_ids: list[str],
    events_by_id: dict[str, TroubleshootingEvent],
) -> list[TroubleshootingEvent]:
    """Hydrate ten model-ranked Kubernetes events with a safe fallback."""

    selected: list[TroubleshootingEvent] = []
    selected_ids: set[str] = set()
    for event_id in [*model_cluster_ids, *model_relevant_ids]:
        event = events_by_id.get(event_id)
        if (
            event is not None
            and event.source == "kubernetes"
            and event.id not in selected_ids
        ):
            selected.append(event)
            selected_ids.add(event.id)
        if len(selected) == _CLUSTER_EVENT_LIMIT:
            break

    if len(selected) < _CLUSTER_EVENT_LIMIT:
        remaining = sorted(
            (
                event
                for event in events_by_id.values()
                if event.source == "kubernetes" and event.id not in selected_ids
            ),
            key=lambda event: (
                _SEVERITY_RANK[event.severity],
                event.timestamp,
                event.id,
            ),
            reverse=True,
        )
        selected.extend(remaining[: _CLUSTER_EVENT_LIMIT - len(selected)])

    return sorted(selected, key=lambda event: (event.timestamp, event.id))


async def build_troubleshooting_report(
    request: TroubleshootingReportRequest,
    data_provider: TroubleshootingDataProvider,
    llm_provider: Callable[[], Any],
    window_hours: int,
    on_phase: Callable[[TroubleshootingJobStatus], Awaitable[None]] | None = None,
) -> TroubleshootingReportResponse:
    """Analyse the configured evidence window and return one grounded report."""

    generated_at = _utcnow()
    window_start = generated_at - timedelta(hours=window_hours)
    window = TroubleshootingWindow(
        start=window_start,
        end=generated_at,
        hours=window_hours,
    )

    try:
        collected_events = await data_provider.collect_events(
            subdomain=request.subdomain,
            deployment=request.deployment,
            tier=request.tier,
            start=window_start,
            end=generated_at,
            limit=_LOG_LINE_LIMIT,
        )
    except AppException:
        raise
    except Exception as exc:
        logger.exception("Troubleshooting data collection failed: %s", exc)
        raise ServiceUnavailableError(
            "Could not collect data for the troubleshooting report.",
            code="TroubleshootingDataUnavailable",
        ) from exc

    events = select_troubleshooting_events(
        collected_events,
        limit=_LOG_LINE_LIMIT,
    )
    if on_phase is not None:
        await on_phase("analyzing_data")

    if not events:
        if on_phase is not None:
            await on_phase("generating_recommendations")
        return TroubleshootingReportResponse(
            generated_at=generated_at,
            window=window,
            severity="warning",
            summary=(
                "No troubleshooting evidence was found in the latest "
                f"{window_hours}-hour window."
            ),
            most_likely_root_cause=(
                "There is not enough log evidence to determine a root cause."
            ),
            recommended_actions=[
                "Verify that the application is running and that operational data collection is configured for this deployment.",
                "Confirm that Grafana log collection includes the application namespace and deployment labels.",
                "Inspect the latest application pods and Kubernetes events directly for collection gaps.",
                "Retry the analysis after new application or Kubernetes activity produces evidence.",
            ],
            analyzed_log_lines=0,
        )

    try:
        available_metric_queries = await data_provider.list_metric_queries(
            subdomain=request.subdomain,
            deployment=request.deployment,
            tier=request.tier,
            start=window_start,
            end=generated_at,
        )
    except Exception as exc:
        logger.warning(
            "Configured PromQL catalog loading failed; continuing without metrics: %s",
            exc,
        )
        available_metric_queries = []

    try:
        model_output = await analyse_events(
            llm_provider(),
            subdomain=request.subdomain,
            deployment=request.deployment,
            events=events,
            available_metric_queries=available_metric_queries,
        )
    except Exception as exc:
        logger.exception("Troubleshooting model analysis failed: %s", exc)
        raise ServiceUnavailableError(
            "The troubleshooting analysis could not be generated.",
            code="TroubleshootingAnalysisUnavailable",
        ) from exc

    if on_phase is not None:
        await on_phase("generating_recommendations")
    metric_queries = select_available_metric_queries(
        model_output.recommended_metric_query_ids,
        available_metric_queries,
    )
    try:
        application_metrics = await data_provider.collect_metrics(
            queries=metric_queries,
            subdomain=request.subdomain,
            start=window_start,
            end=generated_at,
        )
    except Exception as exc:
        logger.warning(
            "Troubleshooting metric collection failed; returning unavailable values: %s",
            exc,
        )
        application_metrics = unavailable_metrics(metric_queries)

    events_by_id = {event.id: event for event in events}
    relevant_events = _events_for_ids(
        model_output.relevant_event_ids,
        events_by_id,
    )
    cluster_events = _select_cluster_events(
        model_output.relevant_cluster_event_ids,
        model_output.relevant_event_ids,
        events_by_id,
    )
    critical_event = events_by_id.get(model_output.most_critical_event_id or "")
    if critical_event is None:
        critical_event = _fallback_critical_event(relevant_events or events)
    if critical_event is not None and all(
        event.id != critical_event.id for event in relevant_events
    ):
        relevant_events = sorted(
            [*relevant_events, critical_event],
            key=lambda event: (event.timestamp, event.id),
        )

    issue_event = events_by_id.get(model_output.issue_started_event_id or "")
    return TroubleshootingReportResponse(
        generated_at=generated_at,
        window=window,
        severity=model_output.severity,
        summary=model_output.summary,
        most_likely_root_cause=model_output.most_likely_root_cause,
        issue_started=issue_event.timestamp if issue_event is not None else None,
        most_critical_event=critical_event,
        recommended_actions=model_output.recommended_actions,
        relevant_events=relevant_events,
        cluster_events=cluster_events,
        application_metrics=application_metrics,
        analyzed_log_lines=len(events),
        data_sources=sorted({event.source for event in events}),
    )

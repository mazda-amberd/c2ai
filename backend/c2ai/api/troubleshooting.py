"""Batch AI troubleshooting report endpoint."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.core.exceptions import (
    AppException,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from c2ai.llm.provider import get_qa_llm
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingJobError,
    TroubleshootingJobResponse,
    TroubleshootingJobStatus,
    TroubleshootingReportRequest,
    TroubleshootingReportResponse,
    TroubleshootingWindow,
)
from c2ai.services.troubleshooting import (
    analyse_events,
    select_troubleshooting_events,
)
from c2ai.services.troubleshooting_config import get_troubleshooting_window_hours
from c2ai.services.troubleshooting_data import (
    TroubleshootingDataProvider,
    get_troubleshooting_data_provider,
)
from c2ai.services.troubleshooting_jobs import (
    InMemoryTroubleshootingJobStore,
    TroubleshootingJobRecord,
)
from c2ai.services.troubleshooting_metric_plot import with_metric_plots
from c2ai.services.troubleshooting_metrics import (
    select_available_metric_queries,
    unavailable_metrics,
)
from c2ai.services.troubleshooting_pdf import (
    build_troubleshooting_pdf,
    troubleshooting_pdf_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Troubleshooting"])

_LOG_LINE_LIMIT = 100
_CLUSTER_EVENT_LIMIT = 10
_job_store = InMemoryTroubleshootingJobStore()
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
    return datetime.now(timezone.utc)


def get_troubleshooting_llm_provider() -> Callable[[], Any]:
    """Return a lazy provider so empty-log reports do not initialize the model."""

    return get_qa_llm


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


async def _build_troubleshooting_report(
    request: TroubleshootingReportRequest,
    data_provider: TroubleshootingDataProvider,
    llm_provider: Callable[[], Any],
    window_hours: int,
    on_phase: Callable[[TroubleshootingJobStatus], None] | None = None,
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
        on_phase("analyzing_data")

    if not events:
        if on_phase is not None:
            on_phase("generating_recommendations")
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
        on_phase("generating_recommendations")
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
    application_metrics = with_metric_plots(application_metrics)

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


@router.post("/report", response_model=TroubleshootingReportResponse)
async def generate_troubleshooting_report(
    request: TroubleshootingReportRequest,
    _user: AthenaTokenUser = Depends(get_current_user_token),
    data_provider: TroubleshootingDataProvider = Depends(
        get_troubleshooting_data_provider
    ),
    llm_provider: Callable[[], Any] = Depends(get_troubleshooting_llm_provider),
    window_hours: int = Depends(get_troubleshooting_window_hours),
) -> TroubleshootingReportResponse:
    """Generate a report directly; retained for non-polling API clients."""

    return await _build_troubleshooting_report(
        request,
        data_provider,
        llm_provider,
        window_hours,
    )


def _job_response(record: TroubleshootingJobRecord) -> TroubleshootingJobResponse:
    error = None
    if record.error_code and record.error_message:
        error = TroubleshootingJobError(
            code=record.error_code,
            message=record.error_message,
        )
    return TroubleshootingJobResponse(
        job_id=record.job_id,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        report=record.report,
        error=error,
    )


async def _run_troubleshooting_job(
    *,
    job_id: str,
    request: TroubleshootingReportRequest,
    data_provider: TroubleshootingDataProvider,
    llm_provider: Callable[[], Any],
    window_hours: int,
) -> None:
    def update_phase(phase: TroubleshootingJobStatus) -> None:
        _job_store.set_status(job_id, phase, now=_utcnow())

    try:
        report = await _build_troubleshooting_report(
            request,
            data_provider,
            llm_provider,
            window_hours,
            on_phase=update_phase,
        )
    except AppException as exc:
        _job_store.fail(
            job_id,
            code=exc.code,
            message=str(exc.detail),
            now=_utcnow(),
        )
    except Exception:
        logger.exception("Unexpected troubleshooting job failure job_id=%s", job_id)
        _job_store.fail(
            job_id,
            code="TroubleshootingJobFailed",
            message="The troubleshooting job failed unexpectedly.",
            now=_utcnow(),
        )
    else:
        _job_store.complete(job_id, report, now=_utcnow())


@router.post(
    "/jobs",
    response_model=TroubleshootingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_troubleshooting_job(
    request: TroubleshootingReportRequest,
    background_tasks: BackgroundTasks,
    user: AthenaTokenUser = Depends(get_current_user_token),
    data_provider: TroubleshootingDataProvider = Depends(
        get_troubleshooting_data_provider
    ),
    llm_provider: Callable[[], Any] = Depends(get_troubleshooting_llm_provider),
    window_hours: int = Depends(get_troubleshooting_window_hours),
) -> TroubleshootingJobResponse:
    """Start a short-lived job and return immediately for status polling."""

    record = _job_store.create(
        owner_id=user.identifier,
        request=request,
        now=_utcnow(),
    )
    background_tasks.add_task(
        _run_troubleshooting_job,
        job_id=record.job_id,
        request=request,
        data_provider=data_provider,
        llm_provider=llm_provider,
        window_hours=window_hours,
    )
    return _job_response(record)


@router.get("/jobs/{job_id}", response_model=TroubleshootingJobResponse)
async def get_troubleshooting_job(
    job_id: str,
    user: AthenaTokenUser = Depends(get_current_user_token),
) -> TroubleshootingJobResponse:
    """Return the current phase or completed batch report for the job owner."""

    record = _job_store.get_for_owner(
        job_id=job_id,
        owner_id=user.identifier,
        now=_utcnow(),
    )
    if record is None:
        raise NotFoundError(
            "Troubleshooting job not found.",
            code="TroubleshootingJobNotFound",
        )
    return _job_response(record)


@router.get(
    "/jobs/{job_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_troubleshooting_pdf(
    job_id: str,
    user: AthenaTokenUser = Depends(get_current_user_token),
) -> Response:
    """Render the completed owner-scoped troubleshooting report as a PDF."""

    record = _job_store.get_for_owner(
        job_id=job_id,
        owner_id=user.identifier,
        now=_utcnow(),
    )
    if record is None:
        raise NotFoundError(
            "Troubleshooting job not found.",
            code="TroubleshootingJobNotFound",
        )
    if record.status != "completed" or record.report is None:
        raise ConflictError(
            "The troubleshooting report is not ready for download.",
            code="TroubleshootingReportNotReady",
        )
    pdf = build_troubleshooting_pdf(record.report, record.request)
    filename = troubleshooting_pdf_filename(record.report.generated_at)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )

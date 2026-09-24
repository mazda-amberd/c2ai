"""AI troubleshooting reports: a synchronous endpoint and durable polled jobs.

``POST /jobs`` queues a ``troubleshooting.report`` job (see
``c2ai.jobs.handlers.troubleshooting``); any API replica can answer
``GET /jobs/{id}`` because the job lives in the database.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.core.exceptions import ConflictError, NotFoundError
from c2ai.jobs import JobRecord, JobStore, get_job_notifier, get_job_store
from c2ai.jobs.handlers.troubleshooting import KIND as REPORT_JOB, RETENTION
from c2ai.jobs.store import FAILED, SUCCEEDED
from c2ai.schemas.troubleshooting import (
    TroubleshootingJobError,
    TroubleshootingJobResponse,
    TroubleshootingReportRequest,
    TroubleshootingReportResponse,
)
from c2ai.services.troubleshooting_config import get_troubleshooting_window_hours
from c2ai.services.troubleshooting_data import TroubleshootingDataProvider
from c2ai.services.troubleshooting_pdf import (
    build_troubleshooting_pdf,
    troubleshooting_pdf_filename,
)
from c2ai.services.troubleshooting_report import (
    build_troubleshooting_report,
    troubleshooting_data_provider,
    troubleshooting_llm_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Troubleshooting"])

_FIRST_PHASE = "gathering_data"


@router.post("/report", response_model=TroubleshootingReportResponse)
async def generate_troubleshooting_report(
    request: TroubleshootingReportRequest,
    _user: AthenaTokenUser = Depends(get_current_user_token),
    data_provider: TroubleshootingDataProvider = Depends(troubleshooting_data_provider),
    llm_provider: Callable[[], Any] = Depends(troubleshooting_llm_provider),
    window_hours: int = Depends(get_troubleshooting_window_hours),
) -> TroubleshootingReportResponse:
    """Generate a report directly; retained for non-polling API clients."""

    return await build_troubleshooting_report(request, data_provider, llm_provider, window_hours)


def _job_response(job: JobRecord) -> TroubleshootingJobResponse:
    if job.status == SUCCEEDED:
        phase = "completed"
    elif job.status == FAILED:
        phase = "failed"
    else:
        phase = job.stage or _FIRST_PHASE
    error = None
    if job.status == FAILED and job.error_code:
        error = TroubleshootingJobError(
            code=job.error_code, message=job.error_message or "The job failed."
        )
    report = (
        TroubleshootingReportResponse.model_validate(job.result)
        if job.status == SUCCEEDED and job.result
        else None
    )
    return TroubleshootingJobResponse(
        job_id=str(job.id),
        status=phase,
        created_at=job.created_at,
        updated_at=job.updated_at,
        report=report,
        error=error,
    )


async def _owned_job(store: JobStore, job_id: str, user: AthenaTokenUser) -> JobRecord:
    job = await store.get(job_id)
    if job is None or job.kind != REPORT_JOB or job.owner != user.identifier:
        raise NotFoundError("Troubleshooting job not found.", code="TroubleshootingJobNotFound")
    return job


@router.post(
    "/jobs",
    response_model=TroubleshootingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_troubleshooting_job(
    request: TroubleshootingReportRequest,
    background_tasks: BackgroundTasks,
    user: AthenaTokenUser = Depends(get_current_user_token),
    window_hours: int = Depends(get_troubleshooting_window_hours),
    store: JobStore = Depends(get_job_store),
    notify: Callable[[], object] = Depends(get_job_notifier),
) -> TroubleshootingJobResponse:
    """Queue a report job and return immediately for status polling."""

    job = await store.enqueue(
        REPORT_JOB,
        {"request": request.model_dump(mode="json"), "window_hours": window_hours},
        owner=user.identifier,
        retention=RETENTION,
    )
    background_tasks.add_task(notify)
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=TroubleshootingJobResponse)
async def get_troubleshooting_job(
    job_id: str,
    user: AthenaTokenUser = Depends(get_current_user_token),
    store: JobStore = Depends(get_job_store),
) -> TroubleshootingJobResponse:
    """Return the current phase or completed report for the job owner."""

    return _job_response(await _owned_job(store, job_id, user))


@router.get(
    "/jobs/{job_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_troubleshooting_pdf(
    job_id: str,
    user: AthenaTokenUser = Depends(get_current_user_token),
    store: JobStore = Depends(get_job_store),
) -> Response:
    """Render the completed owner-scoped troubleshooting report as a PDF."""

    job = await _owned_job(store, job_id, user)
    if job.status != SUCCEEDED or not job.result:
        raise ConflictError(
            "The troubleshooting report is not ready for download.",
            code="TroubleshootingReportNotReady",
        )
    report = TroubleshootingReportResponse.model_validate(job.result)
    request = TroubleshootingReportRequest.model_validate(job.payload["request"])
    pdf = build_troubleshooting_pdf(report, request)
    filename = troubleshooting_pdf_filename(report.generated_at)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )

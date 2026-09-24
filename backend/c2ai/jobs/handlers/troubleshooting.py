"""``troubleshooting.report``: build one owner-scoped AI troubleshooting report."""

from __future__ import annotations

import logging
from datetime import timedelta

from c2ai.core.exceptions import AppException
from c2ai.jobs.worker import JobContext, JobFailed, job_handler
from c2ai.schemas.troubleshooting import TroubleshootingReportRequest
from c2ai.services import troubleshooting_report as reports

logger = logging.getLogger(__name__)

KIND = "troubleshooting.report"
# Finished reports stay downloadable this long (PRD: short-lived, owner-only).
RETENTION = timedelta(minutes=15)


@job_handler(KIND, lease=timedelta(minutes=2))
async def build_report(ctx: JobContext) -> dict:
    request = TroubleshootingReportRequest.model_validate(ctx.payload["request"])
    try:
        report = await reports.build_troubleshooting_report(
            request,
            reports.troubleshooting_data_provider(),
            reports.troubleshooting_llm_provider(),
            int(ctx.payload["window_hours"]),
            on_phase=ctx.set_stage,
        )
    except AppException as error:
        raise JobFailed(error.code, str(error.detail)) from error
    return report.model_dump(mode="json")

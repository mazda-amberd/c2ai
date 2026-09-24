"""``financial.ingest``: the periodic LLM gateway cost poll."""

from __future__ import annotations

import logging
from datetime import timedelta

from c2ai.config import get_settings
from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule
from c2ai.services.financial_ingestion_runner import run_gateway_cost_ingestion_once

logger = logging.getLogger(__name__)

KIND = "financial.ingest"


@job_handler(KIND, lease=timedelta(minutes=5))
async def ingest(_ctx: JobContext) -> dict:
    # The advisory lock inside still guarantees one ingestion at a time, even
    # if a lease expired while a slow poll was finishing.
    result = await run_gateway_cost_ingestion_once()
    if not result.lock_acquired:
        logger.info("Financial ingestion skipped: another worker holds the lock")
    else:
        logger.info(
            "Financial ingestion: private_records=%s public_records=%s period=%s..%s",
            result.records_processed,
            result.public_records_processed,
            result.period_start,
            result.period_end,
        )
    return {
        "lock_acquired": result.lock_acquired,
        "baseline_created": result.baseline_created,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "records_processed": result.records_processed,
        "public_records_processed": result.public_records_processed,
        "unmapped_namespaces": list(result.unmapped_namespaces),
        "unpriced_models": list(result.unpriced_models),
    }


register_schedule(
    Schedule(
        kind=KIND,
        every=lambda: timedelta(seconds=get_settings().financial_poll_seconds),
        enabled=lambda: get_settings().financial_ingestion_enabled,
    )
)

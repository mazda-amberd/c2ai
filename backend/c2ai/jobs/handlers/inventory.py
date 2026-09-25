"""``inventory.refresh``: keep the cluster inventory current.

The ``application_instances`` snapshot (what Grafana sees running, per
tier) backs the ADA "already running" check and the adoption of instances
that were never deployed through C2AI. It used to be rewritten only when
someone opened the tier page, by every page view at once; now one job
refreshes it on a schedule, whether or not anyone is looking.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from c2ai.clients.grafana import GrafanaClient
from c2ai.config import get_settings
from c2ai.crud.application_instance import replace_application_instances_for_tiers
from c2ai.jobs.worker import JobContext, Schedule, job_handler, register_schedule
from c2ai.metrics.tiers import TierMetrics

logger = logging.getLogger(__name__)

KIND = "inventory.refresh"


@job_handler(KIND, lease=timedelta(minutes=2))
async def refresh(ctx: JobContext) -> dict:
    tiers, _gpu_totals = await TierMetrics(GrafanaClient()).get_all_metrics()
    async with ctx.session_factory() as db:
        await replace_application_instances_for_tiers(db, tiers)
        await db.commit()
    counts = {tier: len(instances or []) for tier, instances in tiers.items()}
    logger.debug("Inventory refreshed: %s", counts)
    return {"instances": counts}


register_schedule(
    Schedule(
        kind=KIND,
        every=lambda: timedelta(seconds=get_settings().inventory_refresh_seconds),
        enabled=lambda: bool(get_settings().grafana_api_url.strip()),
    )
)

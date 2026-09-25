"""Tier dashboard metrics (``GET /api/metrics``) and the health probe."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.grafana import GrafanaClient, grafana_client
from c2ai.clients.version import enrich_tiers_with_versions
from c2ai.constants.prometheus import TIER_CONFIG
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.db.session import get_db_session
from c2ai.metrics.tiers import TierMetrics
from c2ai.schemas.grafana import TiersResponse
from c2ai.services.instance_metadata import enrich_tiers_with_instance_metadata

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Metrics"])


def tier_metrics(client: GrafanaClient | None = Depends(grafana_client)) -> TierMetrics | None:
    """FastAPI dependency: the tier dashboard view over the Grafana client."""

    return TierMetrics(client) if client is not None else None


@router.get("/api/metrics", response_model=TiersResponse)
async def get_metrics(
    tier: int | None = Query(
        None, ge=1, le=len(TIER_CONFIG), description=f"Filter by tier number (1-{len(TIER_CONFIG)})"
    ),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
    tiers_view: TierMetrics | None = Depends(tier_metrics),
) -> TiersResponse:
    """CPU / memory / GPU per application instance for every tier.

    Read-only: the ``application_instances`` inventory the deployment guards
    consult is refreshed by the ``inventory.refresh`` job, not by page views.
    """

    if tiers_view is None:
        raise GrafanaFetchError("Grafana is not configured (set GRAFANA_API_URL).")
    try:
        tiers, gpu_totals = await tiers_view.get_all_metrics(tier=tier)
    except GrafanaFetchError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Failed to fetch metrics from Grafana: %s", exc)
        raise GrafanaFetchError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error fetching metrics from Grafana")
        raise GrafanaFetchError(str(exc)) from exc

    tiers = await enrich_tiers_with_versions(tiers)
    tiers = await enrich_tiers_with_instance_metadata(db, tiers)
    return TiersResponse(tiers=tiers, tier_gpu_totals=gpu_totals)

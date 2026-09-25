"""Tier dashboard metrics (``GET /api/metrics``) and the health probe."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.grafana import GrafanaClient, grafana_client, require_grafana
from c2ai.clients.version import enrich_tiers_with_versions
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.db.session import get_db_session
from c2ai.schemas.grafana import TiersResponse
from c2ai.services.instance_metadata import enrich_tiers_with_instance_metadata

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Metrics"])


@router.get("/api/metrics", response_model=TiersResponse)
async def get_metrics(
    tier: int | None = Query(None, ge=1, le=3, description="Filter by tier number (1-3)"),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
    client: GrafanaClient | None = Depends(grafana_client),
) -> TiersResponse:
    """CPU / memory / GPU per application instance for every tier.

    Read-only: the ``application_instances`` inventory the deployment guards
    consult is refreshed by the ``inventory.refresh`` job, not by page views.
    """

    try:
        tiers, gpu_totals = await require_grafana(client).get_all_metrics(tier=tier)
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


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe (no authentication, no dependencies)."""

    return {"status": "ok"}

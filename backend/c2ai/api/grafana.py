"""Tier dashboard metrics (``GET /api/metrics``) and the health probe."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.grafana import GrafanaClient
from c2ai.clients.version import enrich_tiers_with_versions
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.crud.application_instance import replace_application_instances_for_tiers
from c2ai.db.session import get_db_session
from c2ai.schemas.grafana import TiersResponse
from c2ai.services.instance_metadata import enrich_tiers_with_instance_metadata

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Metrics"])

_grafana_client: GrafanaClient | None = None


def get_grafana_client() -> GrafanaClient:
    """Lazily build the shared Grafana client (raises if Grafana is not configured)."""

    global _grafana_client
    if _grafana_client is None:
        _grafana_client = GrafanaClient()
    return _grafana_client


@router.get("/api/metrics", response_model=TiersResponse)
async def get_metrics(
    tier: int | None = Query(None, ge=1, le=3, description="Filter by tier number (1-3)"),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
) -> TiersResponse:
    """CPU / memory / GPU per application instance for every tier.

    Each successful call also refreshes the ``application_instances`` snapshot
    that the legacy deploy/update/terminate guards consult.
    """

    try:
        client = get_grafana_client()
        tiers, gpu_totals = await client.get_all_metrics(tier=tier)
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
    try:
        await replace_application_instances_for_tiers(db, tiers)
        await db.commit()
    except SQLAlchemyError as exc:
        # Concurrent pollers can race on the snapshot; the response is still valid.
        await db.rollback()
        logger.warning("Could not persist application_instances snapshot: %s", exc)
    return TiersResponse(tiers=tiers, tier_gpu_totals=gpu_totals)


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe (no authentication, no dependencies)."""

    return {"status": "ok"}

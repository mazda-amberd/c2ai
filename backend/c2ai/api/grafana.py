"""
API endpoints for fetching metrics from Grafana.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.schemas.grafana import TiersResponse
from c2ai.clients.grafana import GrafanaClient
from c2ai.clients.version import enrich_tiers_with_versions
from c2ai.crud.application_instance import replace_application_instances_for_tiers
from c2ai.db.session import get_db_session
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.services.instance_metadata import enrich_tiers_with_instance_metadata

logger = logging.getLogger(__name__)

router = APIRouter()

_grafana_client: Optional[GrafanaClient] = None


def get_grafana_client() -> GrafanaClient:
    """
    Get or create the Grafana client instance.

    Returns:
        GrafanaClient: The singleton Grafana client instance.

    Raises:
        ValueError: If Grafana client configuration is invalid.
    """
    global _grafana_client
    if _grafana_client is None:
        _grafana_client = GrafanaClient()
    return _grafana_client


@router.get("/api/metrics", response_model=TiersResponse)
async def get_metrics(
    tier: Optional[int] = Query(None, ge=1, le=3, description="Filter by tier number (1-3)"),
    db: AsyncSession = Depends(get_db_session),
) -> TiersResponse:
    """
    Fetch all metrics from Grafana and return combined data.

    Fetches CPU, memory, and GPU metrics for all tiers (or a specific tier)
    and combines them by groupname.

    Args:
        tier: Optional tier number (1-3) to filter results.
        db: Async database session used to persist the latest snapshot.

    Returns:
        TiersResponse: Metrics for all tiers or the requested tier.

    Raises:
        GrafanaFetchError: If there is an error fetching metrics from Grafana.
    """
    try:
        client = get_grafana_client()
        tiers, gpu_totals = await client.get_all_metrics(tier=tier)
        tiers = await enrich_tiers_with_versions(tiers)
        tiers = await enrich_tiers_with_instance_metadata(db, tiers)
        try:
            await replace_application_instances_for_tiers(db, tiers)
        except SQLAlchemyError as persist_exc:
            logger.warning(
                "Could not persist application_instances snapshot: %s",
                persist_exc,
                exc_info=True,
            )
        return TiersResponse(tiers=tiers, tier_gpu_totals=gpu_totals)
    except GrafanaFetchError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Failed to fetch metrics from Grafana: %s", str(exc))
        raise GrafanaFetchError(str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to fetch metrics from Grafana: %s", str(exc))
        raise GrafanaFetchError(str(exc)) from exc


@router.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    Returns:
        dict: Dictionary with service status information.
    """
    return {"status": "ok"}

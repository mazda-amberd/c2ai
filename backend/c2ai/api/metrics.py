"""Level-based metrics API (ATHENAV2607-2). Leaves the legacy ``/api/metrics`` untouched."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.grafana import GrafanaClient, grafana_client, require_grafana
from c2ai.constants.time_ranges import (
    DEFAULT_RANGE,
    RANGE_PRESETS,
    InvalidWindow,
    resolve_window,
)
from c2ai.core.exceptions import ServiceUnavailableError, ValidationFailed
from c2ai.db.session import get_db_session
from c2ai.schemas.metrics import MetricLevel, MetricsResponse
from c2ai.services.instance_metadata import load_instance_metadata_map
from c2ai.services.metrics import TIER_NAMESPACES, MetricsUnavailable, build_metrics

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Metrics"])

@router.get(
    "/api/v2/metrics",
    response_model=MetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Cluster, tier, or application metrics over a time window",
    responses={
        401: {"description": "Missing or invalid token"},
        503: {"description": "Every Grafana request failed"},
    },
)
async def get_metrics_v2(
    level: MetricLevel = Query(..., description="Aggregation level."),
    range_: str | None = Query(
        None,
        alias="range",
        description=f"Preset window ({', '.join(RANGE_PRESETS)}). Defaults to {DEFAULT_RANGE}.",
    ),
    from_: datetime | None = Query(None, alias="from", description="Custom window start (ISO8601)."),
    to: datetime | None = Query(None, description="Custom window end (ISO8601)."),
    tier: int | None = Query(
        None, ge=1, le=len(TIER_NAMESPACES), description="Restrict to one tier."
    ),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
    client: GrafanaClient | None = Depends(grafana_client),
) -> MetricsResponse:
    """Metrics for one level, averaged over the selected window.

    Responds 200 with ``degraded=true`` and a populated ``errors`` list when some
    metrics are unavailable, and 503 only when Grafana returns nothing at all.
    """
    try:
        window = resolve_window(range_, from_, to)
    except InvalidWindow as exc:
        raise ValidationFailed(str(exc)) from exc

    async def load_metadata(subdomains: list[str]) -> dict:
        """Client / instance names for the deployments Grafana returned."""
        try:
            return await load_instance_metadata_map(db, subdomains)
        except Exception as exc:
            logger.warning("Could not load instance metadata: %s", exc)
            return {}

    try:
        return await build_metrics(
            client=require_grafana(client),
            level=level,
            window=window,
            tier=tier,
            metadata_loader=load_metadata,
        )
    except MetricsUnavailable as exc:
        raise ServiceUnavailableError(
            detail={"error": "Metrics unavailable", "message": str(exc)},
            code="MetricsUnavailable",
        ) from exc


@router.get(
    "/api/v2/metrics/application",
    response_model=MetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Metrics for a specific application",
)
async def get_application_metrics_v2(
    application: str = Query(
        ...,
        min_length=3,
        max_length=317,
        description="Application scope ID (namespace/deployment).",
    ),
    range_: str | None = Query(None, alias="range"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    _user: AthenaTokenUser = Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db_session),
    client: GrafanaClient | None = Depends(grafana_client),
) -> MetricsResponse:
    """Return the application metrics envelope narrowed to one scope ID."""
    response = await get_metrics_v2(
        level=MetricLevel.APPLICATION,
        range_=range_,
        from_=from_,
        to=to,
        tier=None,
        _user=_user,
        db=db,
        client=client,
    )
    return response.model_copy(
        update={"series": [item for item in response.series if item.scope.id == application]}
    )

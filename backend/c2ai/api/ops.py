"""Operational endpoints: liveness, readiness, and Prometheus metrics.

* ``GET /health`` — the process is up (no dependencies checked).
* ``GET /ready`` — the database answers and its schema matches this build;
  503 otherwise, so a load balancer holds traffic until migrations ran.
* ``GET /metrics`` — Prometheus text format; requires
  ``Authorization: Bearer <C2AI_METRICS_TOKEN>`` when that token is set.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.core.exceptions import UnauthorizedError
from c2ai.core.observability import JOB_OLDEST_READY_SECONDS, JOBS, OPEN_OPERATIONS, REGISTRY
from c2ai.db.migrate import schema_status
from c2ai.db.session import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Operations"])


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe (no authentication, no dependencies)."""

    return {"status": "ok"}


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        status = await schema_status(db)
        checks["schema"] = (
            status.latest_applied or "empty"
            if status.current
            else f"pending: {', '.join(status.pending)}"
        )
        ready = status.current
    except Exception as error:  # the database is unreachable
        logger.warning("Readiness check failed: %s", error)
        checks["database"] = f"unavailable: {type(error).__name__}"
        ready = False
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


async def _refresh_database_gauges(db: AsyncSession) -> None:
    jobs = await db.execute(text("SELECT kind, status, count(*) FROM jobs GROUP BY kind, status"))
    JOBS.clear()
    for kind, status, count in jobs.all():
        JOBS.labels(kind, status).set(count)
    oldest = await db.execute(
        text(
            "SELECT kind, extract(epoch FROM now() - min(run_after)) FROM jobs"
            " WHERE status = 'queued' AND run_after <= now() GROUP BY kind"
        )
    )
    JOB_OLDEST_READY_SECONDS.clear()
    for kind, age in oldest.all():
        JOB_OLDEST_READY_SECONDS.labels(kind).set(float(age or 0))
    operations = await db.execute(
        text(
            "SELECT coalesce(kind, operation), dispatch_state, count(*) FROM pipeline_runs"
            " WHERE ended_at IS NULL GROUP BY 1, 2"
        )
    )
    OPEN_OPERATIONS.clear()
    for kind, state, count in operations.all():
        OPEN_OPERATIONS.labels(kind, state).set(count)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, db: AsyncSession = Depends(get_db_session)) -> Response:
    expected = get_settings().metrics_token
    if expected:
        presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented, expected):
            raise UnauthorizedError("A valid metrics token is required.", code="MetricsTokenRequired")
    try:
        await _refresh_database_gauges(db)
    except Exception as error:  # still serve the in-process metrics
        logger.warning("Could not read queue/operation gauges: %s", error)
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

"""Deployment logs API — Grafana Loki via unified /api/ds/query."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token
from c2ai.clients.grafana import GrafanaClient
from c2ai.core.exceptions import GrafanaFetchError, ServiceUnavailableError
from c2ai.schemas.deployment import DEPLOYMENT_LOG_NAME_RE, SUBDOMAIN_RE
from c2ai.schemas.logs import DeploymentLogEntryOut, DeploymentLogsResponseOut
from c2ai.services.loki_logs import (
    build_full_loki_logql,
    decode_logs_cursor_v1,
    encode_logs_cursor_v1,
    iter_loki_rows_from_ds_query_payload,
    loki_row_to_deployment_entry,
    ms_exclusive_after_cursor,
    ms_upper_bound_before_cursor,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Logs"])

_SUBDOMAIN_QUERY = Annotated[
    str,
    Query(
        ...,
        pattern=SUBDOMAIN_RE.pattern,
        max_length=63,
        description="Instance identifier (matches metrics / workflow host label).",
    ),
]

_DEPLOYMENT_QUERY = Annotated[
    str,
    Query(
        ...,
        min_length=1,
        max_length=253,
        pattern=DEPLOYMENT_LOG_NAME_RE.pattern,
        description="Application / workload name (same as in the deployments list).",
    ),
]

_grafana_client: GrafanaClient | None = None


def get_logs_grafana_client() -> GrafanaClient:
    global _grafana_client
    if _grafana_client is None:
        _grafana_client = GrafanaClient()
    return _grafana_client


def _parse_iso8601(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _default_range() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(minutes=15), now


def _raise_for_grafana_logs_http(exc: httpx.HTTPStatusError) -> None:
    """Map Grafana /api/ds/query failures to actionable API errors."""
    status = exc.response.status_code
    body_text = ""
    try:
        parsed = exc.response.json()
        if isinstance(parsed, dict):
            body_text = str(parsed.get("message", "") or "")
    except Exception:
        body_text = (exc.response.text or "")[:300]
    lowered = body_text.lower()
    if status == 404 and "data source" in lowered and "not found" in lowered:
        raise ServiceUnavailableError(
            "Grafana has no Loki datasource with this UID (HTTP 404). "
            "Set GRAFANA_LOKI_DATASOURCE_UID to the UID from Grafana: "
            "Connections → Data sources → open your Loki source → copy the UID field."
        ) from exc
    logger.error(
        "Grafana Loki query HTTP error status=%s message=%s",
        status,
        body_text[:500] or exc,
    )
    raise GrafanaFetchError(
        f"Grafana Loki query failed (HTTP {status}): {body_text or exc!s}",
    ) from exc


def _paging_enabled(
    limit: int | None,
    cursor: str | None,
    search: str | None,
    client_time_bounds: bool,
    tail: bool,
) -> bool:
    """
    Forward paging (``tail=false``) requires client ``from``/``to`` when using ``limit``/``cursor``.

    ``tail=true`` uses backward Loki queries (newest chunk first) and allows ``limit``/``cursor``
    without client bounds (server rolling window) for live tail infinite scroll.
    """
    if tail:
        if limit is not None or cursor is not None:
            return True
        return bool((search or "").strip())

    if cursor and not client_time_bounds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from and to are required when cursor is set.",
        )
    if limit is not None and not client_time_bounds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from and to are required when limit is set.",
        )
    if limit is not None or cursor:
        return True
    return bool((search or "").strip() and client_time_bounds)


def _clamp_limit(raw: int | None, default: int) -> int:
    cap = raw if raw is not None else default
    return max(1, min(2000, cap))


def _rows_to_sorted_entries(
    payload: dict,
    deployment: str,
) -> list[tuple[int, DeploymentLogEntryOut]]:
    enriched: list[tuple[int, DeploymentLogEntryOut]] = []
    for ts_ns, line, labels in iter_loki_rows_from_ds_query_payload(payload):
        enriched.append(
            (ts_ns, loki_row_to_deployment_entry(ts_ns, line, labels, deployment)),
        )
    enriched.sort(key=lambda x: (x[0], x[1].id))
    return enriched


@router.get("/api/logs/deployment", response_model=DeploymentLogsResponseOut)
async def get_deployment_logs(
    subdomain: _SUBDOMAIN_QUERY,
    deployment: _DEPLOYMENT_QUERY,
    tier: Annotated[
        int | None,
        Query(
            ge=1,
            le=4,
            description="1-based tier index from Athena routing; optional Loki filter when GRAFANA_LOKI_TIER_LABEL is set.",
        ),
    ] = None,
    from_ts: Annotated[
        str | None,
        Query(alias="from", description="ISO8601 inclusive lower time bound"),
    ] = None,
    to_ts: Annotated[
        str | None,
        Query(alias="to", description="ISO8601 inclusive upper time bound"),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            max_length=400,
            description='Server-side filter: free text (tokenised ``|=``) plus ``level:info``-style tokens (LogQL ``|~``).',
        ),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=2000,
            description="Max log lines for this response (enables forward paging with ``cursor``).",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            max_length=2048,
            description="Opaque continuation token from the previous ``next_cursor``.",
        ),
    ] = None,
    tail: Annotated[
        bool,
        Query(
            description=(
                "When true, page with Loki **backward** queries (newest lines first). "
                "``limit``/``cursor`` work without client ``from``/``to`` (rolling server window). "
                "``next_cursor`` references the **oldest** line in the page for loading older rows."
            ),
        ),
    ] = False,
    _user: AthenaTokenUser = Depends(get_current_user_token),
) -> DeploymentLogsResponseOut:
    client_time_bounds = bool(from_ts and to_ts)
    if not client_time_bounds:
        from_dt, to_dt = _default_range()
    else:
        try:
            from_dt = _ensure_utc(_parse_iso8601(from_ts))
            to_dt = _ensure_utc(_parse_iso8601(to_ts))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid from or to datetime (use ISO8601).",
            ) from exc
        if from_dt > to_dt:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="from must be before or equal to to.",
            )

    logql = build_full_loki_logql(subdomain, deployment, tier, search)
    start_ms = int(from_dt.timestamp() * 1000)
    end_ms = int(to_dt.timestamp() * 1000)

    client = get_logs_grafana_client()
    paging = _paging_enabled(limit, cursor, search, client_time_bounds, tail)

    page_limit = _clamp_limit(limit, 500) if paging else 0
    query_start_ms = start_ms
    query_end_ms = end_ms
    if paging:
        if cursor:
            try:
                ts_ns, _eid = decode_logs_cursor_v1(cursor)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid cursor.",
                ) from exc
            if tail:
                query_end_ms = min(end_ms, ms_upper_bound_before_cursor(ts_ns))
            else:
                query_start_ms = max(start_ms, ms_exclusive_after_cursor(ts_ns))
        if query_start_ms > query_end_ms:
            return DeploymentLogsResponseOut(
                entries=[],
                has_more=False,
                next_cursor=None,
            )

    try:
        if paging:
            payload = await client.query_loki_range(
                logql,
                query_start_ms,
                query_end_ms,
                max_lines=page_limit,
                direction="backward" if tail else "forward",
            )
        else:
            payload = await client.query_loki_range(logql, start_ms, end_ms)
    except ValueError as exc:
        raise ServiceUnavailableError(
            "Logs are not configured: set GRAFANA_LOKI_DATASOURCE_UID on the server.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        _raise_for_grafana_logs_http(exc)
    except Exception as exc:
        logger.error("Loki query failed: %s", exc, exc_info=True)
        raise GrafanaFetchError(str(exc)) from exc

    enriched = _rows_to_sorted_entries(payload, deployment)
    entries = [e for _, e in enriched]

    if not paging:
        return DeploymentLogsResponseOut(entries=entries)

    next_cursor: str | None = None
    if tail:
        has_more = len(entries) == page_limit
        if has_more and enriched:
            oldest_ts, oldest_e = enriched[0]
            next_cursor = encode_logs_cursor_v1(oldest_ts, oldest_e.id)
    else:
        has_more = len(entries) == page_limit
        if has_more and enriched:
            last_ts, last_e = enriched[-1]
            next_cursor = encode_logs_cursor_v1(last_ts, last_e.id)

    return DeploymentLogsResponseOut(
        entries=entries,
        has_more=has_more,
        next_cursor=next_cursor,
    )

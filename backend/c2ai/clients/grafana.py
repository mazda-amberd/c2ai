"""Grafana transport: run Prometheus/Loki queries through Grafana's datasource API.

The tier dashboard view lives in ``c2ai.metrics.tiers``, the LLM gateway
counters in ``c2ai.metrics.gateway``, windowed metrics in
``c2ai.services.metrics``; all of them query through this client.
"""

from __future__ import annotations

import logging

import httpx

from c2ai.clients.http import http_client
from c2ai.config import get_settings
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.schemas.grafana import (
    GrafanaResponse,
)

logger = logging.getLogger(__name__)


class GrafanaClient:
    """Posts query bodies to Grafana's ``/api/ds/query`` and returns frames."""

    def __init__(
        self,
        api_url: str | None = None,
        api_token: str | None = None,
    ):
        """
        Initialise the Grafana client.

        Args:
            api_url: Grafana API URL. Defaults to GRAFANA_API_URL env var.
            api_token: Grafana API token. Defaults to GRAFANA_API_TOKEN env var.

        Raises:
            ValueError: If GRAFANA_API_URL is not set.
        """
        self.api_url = api_url or get_settings().grafana_api_url
        if not self.api_url:
            raise ValueError("GRAFANA_API_URL environment variable is required")
        self.api_token = api_token or get_settings().grafana_api_token

    async def fetch_grafana_query(self, body: dict) -> GrafanaResponse:
        """
        Post an arbitrary query body to the Grafana datasource API and return
        Shared by ``fetch_grafana_data``, ``fetch_gpu_per_app_data``, and
        ``fetch_gpu_tier_totals``.
        """
        try:
            async with http_client() as client:
                response = await client.post(
                    self.api_url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_token}",
                    },
                    timeout=30.0,
                )
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and status_code >= 400:
                    logger.error(
                        "Grafana query failed: status=%s body=%s",
                        status_code,
                        getattr(response, "text", "")[:300] or "",
                    )
                response.raise_for_status()
                return GrafanaResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            logger.error(
                "Grafana query failed: url=%s status=%s",
                e.request.url,
                e.response.status_code,
            )
            raise httpx.HTTPStatusError(
                f"Grafana query failed: {e!s}",
                request=e.request,
                response=e.response,
            ) from e

    async def fetch_grafana_query_raw(self, body: dict) -> dict:
        """Post a query body and return the raw JSON.

        ``GrafanaResponse`` drops the per-refId ``error`` field, which the level-based
        metrics API needs to tell a failed query apart from one that returned no data.
        """
        async with http_client() as client:
            response = await client.post(
                self.api_url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_token}",
                },
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json()

    async def query_loki_range(
        self,
        logql: str,
        start_ms: int,
        end_ms: int,
        max_lines: int | None = None,
        direction: str | None = None,
    ) -> dict:
        """
        Run a Loki **range** query via Grafana's unified ``/api/ds/query`` endpoint.

        Returns the raw JSON dict (not validated as ``GrafanaResponse``) because
        Loki frames use different field shapes than Prometheus instant queries.

        Args:
            logql: Full LogQL including stream selector (e.g. ``{namespace="x"}``).
            start_ms: Range start (Unix epoch milliseconds).
            end_ms: Range end (Unix epoch milliseconds).
            max_lines: Cap on log lines (default: ``GRAFANA_LOKI_MAX_LINES`` or 2000).
            direction: Optional Loki range direction (``forward`` or ``backward``).

        Raises:
            ValueError: If ``GRAFANA_LOKI_DATASOURCE_UID`` is unset.
            httpx.HTTPStatusError: On non-success HTTP from Grafana.
        """
        loki_uid = get_settings().grafana_loki_datasource_uid.strip()
        if not loki_uid:
            raise ValueError(
                "GRAFANA_LOKI_DATASOURCE_UID environment variable is required for logs"
            )
        cap = max_lines
        if cap is None:
            cap = get_settings().grafana_loki_max_lines
        datasource = {"type": "loki", "uid": loki_uid}
        q: dict = {
            "refId": "A",
            "datasource": datasource,
            "expr": logql,
            "queryType": "range",
            "maxLines": cap,
        }
        if direction:
            q["direction"] = direction
        body = {
            "queries": [q],
            "from": str(start_ms),
            "to": str(end_ms),
        }
        try:
            async with http_client() as client:
                response = await client.post(
                    self.api_url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_token}",
                    },
                    timeout=60.0,
                )
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and status_code >= 400:
                    logger.error(
                        "Grafana Loki query failed status=%s body=%s",
                        status_code,
                        getattr(response, "text", "")[:500] or "",
                    )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Grafana Loki query failed url=%s status=%s",
                e.request.url,
                e.response.status_code,
            )
            raise httpx.HTTPStatusError(
                f"Grafana Loki query failed: {e!s}",
                request=e.request,
                response=e.response,
            ) from e


def grafana_client() -> GrafanaClient | None:
    """FastAPI dependency: a Grafana client for the current settings, or None.

    Cheap to build per request (connections come from the shared pool);
    tests replace it through ``app.dependency_overrides``. It never raises —
    FastAPI resolves dependencies before reporting request validation errors —
    so routes call ``require_grafana`` once their inputs are valid.
    """

    if not get_settings().grafana_api_url.strip():
        return None
    return GrafanaClient()


def require_grafana(client: GrafanaClient | None) -> GrafanaClient:
    if client is None:
        raise GrafanaFetchError("Grafana is not configured (set GRAFANA_API_URL).")
    return client

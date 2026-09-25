"""
Grafana HTTP client for fetching Prometheus-backed metrics.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime

import httpx

from c2ai.clients.http import http_client
from c2ai.config import get_settings
from c2ai.constants.prometheus import (
    METRIC_QUERIES,
    REF_IDS,
    TIER_CONFIG,
    TIER_DISPLAY_NAME_TO_GPU_PROMQL_LABEL,
    TIER_MAP,
    TIER_NAMES,
    _cpu_cores_cap,
    _memory_gb_cap,
    excluded_deployment_names,
    get_gpu_per_app_query,
    get_grafana_prometheus_datasource,
)
from c2ai.core.exceptions import GrafanaFetchError
from c2ai.schemas.grafana import (
    GrafanaField,
    GrafanaFrame,
    GrafanaResponse,
    Instance,
    MetricType,
    Status,
)

logger = logging.getLogger(__name__)

# The gateway labels every request with the upstream it routed to. vLLM serves
# our own GPU-hosted models, so its usage is priced from request duration;
# every other provider is a public API priced from its token counters. Keeping
# the two selectors complementary means one request is never charged twice.
PRIVATE_LLM_PROVIDER = "vllm"
PRIVATE_LLM_PROVIDER_SELECTOR = f'{{provider="{PRIVATE_LLM_PROVIDER}"}}'
PUBLIC_API_PROVIDER_SELECTOR = f'{{provider!="{PRIVATE_LLM_PROVIDER}"}}'

# Public-API pricing depends on which model served the tokens, so those
# counters keep the gateway's provider and model labels through the join.
PUBLIC_API_GROUP_BY = ("source_namespace", "provider", "model")


def _build_grafana_query_body(metric_type: MetricType) -> dict:
    """
    Build the JSON query body for a Grafana datasource API request.

    Args:
        metric_type: The metric to query (CPU, MEMORY, GPU, …).

    Returns:
        Dictionary ready to be posted to the Grafana query endpoint.
    """
    query_func = METRIC_QUERIES[metric_type]
    datasource = get_grafana_prometheus_datasource()

    queries = [
        {
            "refId": ref_id,
            "datasource": datasource,
            "expr": query_func(tier),
            "instant": True,
        }
        for ref_id, tier in zip(REF_IDS, TIER_NAMES)
    ]

    return {
        "queries": queries,
        "from": "now-1m",
        "to": "now",
    }


class GrafanaClient:
    """Client for interacting with the Grafana API to fetch Prometheus metrics."""

    # Grafana response structure indices:
    # - Index 0: Time field (timestamp)
    # - Index 1: Value field (metric value with labels)
    VALUE_FIELD_INDEX = 1

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

    async def fetch_grafana_data(self, metric_type: MetricType) -> GrafanaResponse:
        """
        Fetch data from Grafana for a specific metric type.

        Args:
            metric_type: The type of metric to fetch.

        Returns:
            GrafanaResponse containing the query results.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        body = _build_grafana_query_body(metric_type)
        try:
            return await self.fetch_grafana_query(body)
        except httpx.HTTPStatusError as e:
            raise httpx.HTTPStatusError(
                f"Grafana query failed for {metric_type.value}: {e!s}",
                request=e.request,
                response=e.response,
            ) from e

    async def fetch_gpu_per_app_data(self) -> GrafanaResponse:
        """
        Fetch per-application GPU attribution (panel 18) only.

        refId ``A``: token-rate × tier GPU (panel 18). Namespaces without
        attribution are absent from the response and treated as 0 GPU in
        ``combine_metrics``.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        datasource = get_grafana_prometheus_datasource()
        body = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": datasource,
                    "expr": get_gpu_per_app_query(),
                    "instant": True,
                },
            ],
            "from": "now-5m",
            "to": "now",
        }
        return await self.fetch_grafana_query(body)

    async def fetch_gpu_tier_range(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        step_seconds: int = 300,
    ) -> GrafanaResponse:
        """Fetch historical tier-wide GPU units used for cost attribution."""
        if period_start.tzinfo is None or period_start.utcoffset() is None:
            raise ValueError("period_start must include a timezone")
        if period_end.tzinfo is None or period_end.utcoffset() is None:
            raise ValueError("period_end must include a timezone")
        if period_end <= period_start:
            raise ValueError("period_end must be later than period_start")
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")

        start_ms = int(period_start.timestamp() * 1000)
        end_ms = int(period_end.timestamp() * 1000)
        duration_seconds = (period_end - period_start).total_seconds()
        max_data_points = math.ceil(duration_seconds / step_seconds) + 1
        datasource = get_grafana_prometheus_datasource()
        body = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": datasource,
                    "expr": METRIC_QUERIES[MetricType.GPU]("Tier1"),
                    "instant": False,
                    "range": True,
                    "queryType": "range",
                    "interval": f"{step_seconds}s",
                    "intervalMs": step_seconds * 1000,
                    "maxDataPoints": max_data_points,
                },
            ],
            "from": str(start_ms),
            "to": str(end_ms),
        }
        return await self.fetch_grafana_query(body)

    @staticmethod
    def _gateway_counter_by_namespace_body(
        *,
        counter_name: str,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None,
        counter_selector: str = "",
        group_by: tuple[str, ...] = ("source_namespace",),
    ) -> dict:
        """Build a datasource body summing a gateway counter by source namespace.

        Gateway counters identify callers by ``requested_host`` (the caller pod
        IP), so the query joins that label to ``kube_pod_info.pod_ip`` and
        copies the matching Kubernetes namespace into ``source_namespace``. Any
        per-request counter with a ``requested_host`` label — input or output
        tokens, cumulative request-duration seconds — can be aggregated this
        way. ``counter_selector`` narrows the counter to one set of gateway
        providers, and ``group_by`` keeps further counter labels, such as the
        provider and model a public-API charge is priced from.
        """
        if period_start.tzinfo is None or period_start.utcoffset() is None:
            raise ValueError("period_start must include a timezone")
        if period_end.tzinfo is None or period_end.utcoffset() is None:
            raise ValueError("period_end must include a timezone")
        if period_end <= period_start:
            raise ValueError("period_end must be later than period_start")

        namespace = (source_namespace or "").strip()
        namespace_pattern = re.escape(namespace) if namespace else ".+"
        duration_seconds = max(
            1,
            math.ceil((period_end - period_start).total_seconds()),
        )
        expression = (
            f"sum by ({', '.join(group_by)}) ("
            "label_replace("
            f"increase({counter_name}{counter_selector}[{duration_seconds}s]), "
            '"pod_ip", "$1", "requested_host", "(.*)"'
            ") "
            "* on (pod_ip) group_left(source_namespace) "
            "label_replace("
            "max by (pod_ip, namespace) ("
            "max_over_time(kube_pod_info{"
            'host_network="false",pod_ip!="",'
            f'namespace=~"{namespace_pattern}"'
            f"}}[{duration_seconds}s])"
            "), "
            '"source_namespace", "$1", "namespace", "(.*)"'
            ")"
            ")"
        )
        datasource = get_grafana_prometheus_datasource()
        return {
            "queries": [
                {
                    "refId": "A",
                    "datasource": datasource,
                    "expr": expression,
                    "instant": True,
                }
            ],
            "from": str(int(period_start.timestamp() * 1000)),
            "to": str(int(period_end.timestamp() * 1000)),
        }

    async def fetch_llm_gateway_tokens_by_namespace(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None = None,
    ) -> GrafanaResponse:
        """Fetch total-token increases over an exact interval by source namespace.

        This is the datasource query behind the LLM gateway source-namespace
        dashboard. Grafana dashboard URLs return HTML; Athena calls Grafana's
        datasource API with the equivalent PromQL instead.
        """
        body = self._gateway_counter_by_namespace_body(
            counter_name="llm_total_tokens_total",
            period_start=period_start,
            period_end=period_end,
            source_namespace=source_namespace,
        )
        return await self.fetch_grafana_query(body)

    async def fetch_llm_gateway_request_duration_by_namespace(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None = None,
    ) -> GrafanaResponse:
        """Fetch the cumulative request-duration seconds by source namespace.

        Sums ``llm_duration_seconds_sum`` — the gateway histogram's running
        total of per-request durations — increased over the interval. The
        result is total request-seconds spent by each namespace's callers,
        which private-LLM cost treats as billable GPU time. Only the vLLM
        provider is counted: public-API calls occupy no GPU of ours and are
        priced from their token counters instead.
        """
        body = self._gateway_counter_by_namespace_body(
            counter_name="llm_duration_seconds_sum",
            period_start=period_start,
            period_end=period_end,
            source_namespace=source_namespace,
            counter_selector=PRIVATE_LLM_PROVIDER_SELECTOR,
        )
        return await self.fetch_grafana_query(body)

    async def _fetch_public_api_tokens_by_model(
        self,
        counter_name: str,
        *,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None,
    ) -> GrafanaResponse:
        """Fetch one public-API token counter by namespace, provider and model."""
        body = self._gateway_counter_by_namespace_body(
            counter_name=counter_name,
            period_start=period_start,
            period_end=period_end,
            source_namespace=source_namespace,
            counter_selector=PUBLIC_API_PROVIDER_SELECTOR,
            group_by=PUBLIC_API_GROUP_BY,
        )
        return await self.fetch_grafana_query(body)

    async def fetch_llm_gateway_public_input_tokens_by_model(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None = None,
    ) -> GrafanaResponse:
        """Fetch public-API input tokens per namespace, provider and model.

        Input and output tokens bill at different published rates, so they are
        queried separately and priced separately.
        """
        return await self._fetch_public_api_tokens_by_model(
            "llm_input_tokens_total",
            period_start=period_start,
            period_end=period_end,
            source_namespace=source_namespace,
        )

    async def fetch_llm_gateway_public_output_tokens_by_model(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        source_namespace: str | None = None,
    ) -> GrafanaResponse:
        """Fetch public-API output tokens per namespace, provider and model."""
        return await self._fetch_public_api_tokens_by_model(
            "llm_output_tokens_total",
            period_start=period_start,
            period_end=period_end,
            source_namespace=source_namespace,
        )

    async def fetch_llm_gateway_namespace_tiers(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> GrafanaResponse:
        """Fetch namespace tier labels observed during the usage interval."""
        if period_start.tzinfo is None or period_start.utcoffset() is None:
            raise ValueError("period_start must include a timezone")
        if period_end.tzinfo is None or period_end.utcoffset() is None:
            raise ValueError("period_end must include a timezone")
        if period_end <= period_start:
            raise ValueError("period_end must be later than period_start")

        duration_seconds = max(
            1,
            math.ceil((period_end - period_start).total_seconds()),
        )
        datasource = get_grafana_prometheus_datasource()
        body = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": datasource,
                    "expr": (
                        "max by (namespace, label_tier) "
                        "(max_over_time(kube_deployment_labels{"
                        'label_tier=~"tier1|tier2|tier3|prod"}'
                        f"[{duration_seconds}s]))"
                    ),
                    "instant": True,
                }
            ],
            "from": str(int(period_start.timestamp() * 1000)),
            "to": str(int(period_end.timestamp() * 1000)),
        }
        return await self.fetch_grafana_query(body)

    async def fetch_gpu_tier_totals(self) -> GrafanaResponse:
        """
        Fetch tier-wide Ray GPU totals (one query, ``label_tier`` dimension).

        refId ``A``: same expression as ``METRIC_QUERIES[MetricType.GPU]`` —
        ``avg by (label_tier)(label_replace(...) or ...)``. Parsed in
        ``combine_metrics`` via ``_parse_gpu_tier_totals``.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        datasource = get_grafana_prometheus_datasource()
        body = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": datasource,
                    "expr": METRIC_QUERIES[MetricType.GPU]("Tier1"),
                    "instant": True,
                },
            ],
            "from": "now-5m",
            "to": "now",
        }
        return await self.fetch_grafana_query(body)

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

    @staticmethod
    def extract_instances(frames: list[GrafanaFrame]) -> list[dict]:
        """
        Extract instances from Grafana response frames.

        Workload identity:
          groupname (unique key): "{namespace}/{deployment}" when both are present,
                                  otherwise label_app → owner_name → ray_io_cluster → "instance-N"
          nodename: namespace

        The returned dict also carries "display_name" (the deployment name without
        namespace prefix) for use as Instance.name in combine_metrics.

        Returns:
            List of dicts with id, groupname, nodename, value.
        """
        instances = []

        for index, frame in enumerate(frames):
            groupname = ""
            display_name = ""
            nodename = ""
            if len(frame.schema_.fields) > GrafanaClient.VALUE_FIELD_INDEX:
                field = frame.schema_.fields[GrafanaClient.VALUE_FIELD_INDEX]
                if field.labels:
                    lbl = field.labels
                    ns = lbl.namespace or ""
                    dep = (
                        lbl.deployment
                        or lbl.label_app
                        or lbl.owner_name
                        or lbl.ray_io_cluster
                        or ""
                    )
                    if ns and dep:
                        # Unique key per (namespace, deployment) pair.
                        groupname = f"{ns}/{dep}"
                        display_name = dep
                    else:
                        groupname = dep
                        display_name = dep
                    nodename = ns

            value = 0.0
            if (
                frame.data.values
                and len(frame.data.values) > GrafanaClient.VALUE_FIELD_INDEX
            ):
                values_list = frame.data.values[GrafanaClient.VALUE_FIELD_INDEX]
                if values_list:
                    value = (
                        values_list[0]
                        if isinstance(values_list[0], (int, float))
                        else 0.0
                    )

            fallback = f"instance-{index + 1}"
            instances.append(
                {
                    "id": index + 1,
                    "groupname": groupname or fallback,
                    "display_name": display_name or fallback,
                    "nodename": nodename,
                    "value": value,
                }
            )

        return instances

    @staticmethod
    def extract_total_value(frames: list[GrafanaFrame]) -> float:
        """
        Sum all metric values across frames (used for tier-level totals).

        Args:
            frames: List of Grafana data frames.

        Returns:
            Sum of all values in the frames.
        """
        total = 0.0
        for frame in frames:
            if (
                frame.data.values
                and len(frame.data.values) > GrafanaClient.VALUE_FIELD_INDEX
            ):
                values_list = frame.data.values[GrafanaClient.VALUE_FIELD_INDEX]
                if values_list:
                    value = (
                        values_list[0]
                        if isinstance(values_list[0], (int, float))
                        else 0.0
                    )
                    total += value
        return total

    @staticmethod
    def calculate_status(cpu: float, memory: float, gpu: float) -> Status:
        """
        Derive health status from metric percentages.

        Thresholds (matching frontend):
        - Critical: ANY metric >= 76 %
        - Healthy:  ALL metrics <= 50 %
        - Warning:  otherwise

        Args:
            cpu: CPU usage percentage of allocated share.
            memory: Memory usage percentage of allocated share.
            gpu: GPU usage percentage (placeholder).

        Returns:
            Status enum value.
        """
        if cpu >= 76 or memory >= 76 or gpu >= 76:
            return Status.CRITICAL
        if cpu <= 50 and memory <= 50 and gpu <= 50:
            return Status.HEALTHY
        return Status.WARNING

    @staticmethod
    def _k8s_namespace_from_field_labels(
        field: GrafanaField | None,
    ) -> str | None:
        """Kubernetes namespace from Grafana field labels (handles relabel exports)."""
        if not field or not field.labels:
            return None
        lbl = field.labels
        return lbl.namespace or lbl.exported_namespace

    @staticmethod
    def _namespace_from_frame(frame: GrafanaFrame) -> str | None:
        """Prefer labels on the value field; fall back to any field (Grafana varies)."""
        fields = frame.schema_.fields
        if len(fields) > GrafanaClient.VALUE_FIELD_INDEX:
            ns = GrafanaClient._k8s_namespace_from_field_labels(
                fields[GrafanaClient.VALUE_FIELD_INDEX]
            )
            if ns:
                return ns
        for fld in fields:
            ns = GrafanaClient._k8s_namespace_from_field_labels(fld)
            if ns:
                return ns
        return None

    @staticmethod
    def _label_tier_from_field_labels(field: GrafanaField | None) -> str | None:
        """``label_tier`` from Grafana field labels (tier-total GPU instant query)."""
        if not field or not field.labels:
            return None
        return field.labels.label_tier

    @staticmethod
    def _label_tier_from_frame(frame: GrafanaFrame) -> str | None:
        """Prefer labels on the value field; fall back to any field."""
        fields = frame.schema_.fields
        if len(fields) > GrafanaClient.VALUE_FIELD_INDEX:
            lt = GrafanaClient._label_tier_from_field_labels(
                fields[GrafanaClient.VALUE_FIELD_INDEX]
            )
            if lt:
                return lt
        for fld in fields:
            lt = GrafanaClient._label_tier_from_field_labels(fld)
            if lt:
                return lt
        return None

    @staticmethod
    def _ns_gpu_value_map(
        gpu_bundle: GrafanaResponse | None,
        ref_id: str,
    ) -> dict[str, float]:
        """
        Parse a Prometheus instant response ref into {namespace: raw_value}.

        Used for per-app GPU (``refId`` A: token × tier GPU) and Ray fallbacks
        (B: pod-join, C: sum by Ray ``namespace``).  Values are in the same
        0–num_gpus scale as tier totals; clamped defensively to 100.0.
        """
        result: dict[str, float] = {}
        if gpu_bundle is None:
            return result
        q = gpu_bundle.results.get(ref_id)
        if not q:
            return result
        for frame in q.frames:
            if len(frame.schema_.fields) <= GrafanaClient.VALUE_FIELD_INDEX:
                continue
            ns = GrafanaClient._namespace_from_frame(frame)
            if not ns:
                continue
            values_list = (
                frame.data.values[GrafanaClient.VALUE_FIELD_INDEX]
                if len(frame.data.values) > GrafanaClient.VALUE_FIELD_INDEX
                else []
            )
            raw = (
                values_list[0]
                if values_list and isinstance(values_list[0], (int, float))
                else 0.0
            )
            result[ns] = round(raw * 100) / 100
        return result

    @staticmethod
    def _parse_gpu_per_app(
        gpu_per_app_data: GrafanaResponse | None,
    ) -> dict[str, float]:
        """
        Parse the per-app GPU attribution response (panel 18) into a
        {namespace: value} map (``refId`` A).

        Each frame carries a ``namespace`` label; raw is token share × tier GPU
        (0–num_gpus range), same as panel 21.
        """
        return GrafanaClient._ns_gpu_value_map(gpu_per_app_data, "A")

    @staticmethod
    def _parse_gpu_tier_totals(
        gpu_tier_data: GrafanaResponse | None,
    ) -> dict[str, float]:
        """
        Parse tier-total GPU response (refId A) into ``{label_tier: value}``.

        Each frame carries a ``label_tier`` label (``tier1`` / ``tier2`` /
        ``tier3``). Values are in the same 0–num_gpus scale as Grafana panel 21.
        """
        result: dict[str, float] = {}
        if gpu_tier_data is None:
            return result
        q = gpu_tier_data.results.get("A")
        if not q:
            return result
        for frame in q.frames:
            if len(frame.schema_.fields) <= GrafanaClient.VALUE_FIELD_INDEX:
                continue
            lt = GrafanaClient._label_tier_from_frame(frame)
            if not lt:
                continue
            values_list = (
                frame.data.values[GrafanaClient.VALUE_FIELD_INDEX]
                if len(frame.data.values) > GrafanaClient.VALUE_FIELD_INDEX
                else []
            )
            raw = (
                values_list[0]
                if values_list and isinstance(values_list[0], (int, float))
                else 0.0
            )
            result[lt] = round(raw * 100) / 100
        return result

    def combine_metrics(
        self,
        cpu_data: GrafanaResponse,
        memory_data: GrafanaResponse,
        gpu_data: GrafanaResponse,
        cpu_total_data: GrafanaResponse | None = None,
        gpu_per_app_data: GrafanaResponse | None = None,
    ) -> tuple[dict[str, list[Instance] | None], dict[str, float | None]]:
        """
        Combine CPU, memory, and GPU responses into per-tier Instance lists,
        also returning per-tier GPU totals.

        Unit normalisation (k8s RayCluster model):
          CPU:    raw cores/sec → percentage of ATHENA_CPU_CORES_CAP (default 8 cores).
          Memory: raw GB        → percentage of ATHENA_MEMORY_GB_CAP  (default 80 GB).
          GPU tier total: Ray ``avg by (label_tier)`` instant query (0–num_gpus scale).
          GPU per app:    panel 18 (token×tier) lookup by Kubernetes namespace;
                          instances without an entry are reported as 0.

        Args:
            cpu_data:          Grafana response — CPU cores/sec per deployment.
            memory_data:       Grafana response — memory GB per deployment.
            gpu_data:          Tier-total GPU response (ref A) — ``label_tier`` → value.
            cpu_total_data:    Ignored; accepted for backwards compatibility.
            gpu_per_app_data:  Optional — panel 18 per-namespace GPU attribution.

        Returns:
            Tuple of:
              - tiers: tier name → list of Instance objects (or None for GPU-less tiers).
              - gpu_totals: tier name → tier-wide GPU % (or None for GPU-less tiers).
        """
        cpu_cap = _cpu_cores_cap()
        mem_cap = _memory_gb_cap()

        tiers: dict[str, list[Instance] | None] = {
            "Tier 1": [],
            "Tier 2": [],
            "Tier 3": [],
            "Tier 4": None,
        }
        gpu_totals: dict[str, float | None] = {
            "Tier 1": None,
            "Tier 2": None,
            "Tier 3": None,
            "Tier 4": None,
        }

        per_app_gpu = self._parse_gpu_per_app(gpu_per_app_data)
        tier_gpu_by_label = self._parse_gpu_tier_totals(gpu_data)

        for ref_id in REF_IDS:
            tier_name = TIER_MAP[ref_id]
            tier_config = TIER_CONFIG.get(tier_name)

            if tier_config is None:
                tiers[tier_name] = None
                gpu_totals[tier_name] = None
                continue

            cpu_result = cpu_data.results.get(ref_id)
            memory_result = memory_data.results.get(ref_id)

            cpu_instances = self.extract_instances(
                cpu_result.frames if cpu_result else []
            )
            memory_instances = self.extract_instances(
                memory_result.frames if memory_result else []
            )

            # GPU tier total: one Prometheus query keyed by label_tier (tier1–3).
            promql_label = TIER_DISPLAY_NAME_TO_GPU_PROMQL_LABEL.get(tier_name, "")
            tier_gpu_pct = tier_gpu_by_label.get(promql_label, 0.0)
            gpu_totals[tier_name] = tier_gpu_pct

            instance_map: dict[str, dict] = {}

            for inst in cpu_instances:
                instance_map[inst["groupname"]] = {
                    "id": inst["id"],
                    "name": inst.get("display_name") or inst["groupname"],
                    "nodename": inst["nodename"],
                    "cpu_raw": inst["value"],
                    "memory_raw": 0.0,
                    "gpu": 0.0,
                }

            for inst in memory_instances:
                if inst["groupname"] in instance_map:
                    instance_map[inst["groupname"]]["memory_raw"] = inst["value"]
                else:
                    instance_map[inst["groupname"]] = {
                        "id": inst["id"],
                        "name": inst.get("display_name") or inst["groupname"],
                        "nodename": inst["nodename"],
                        "cpu_raw": 0.0,
                        "memory_raw": inst["value"],
                        "gpu": 0.0,
                    }

            # Per-instance GPU is sourced strictly from panel 18 (token×tier).
            # Panel 18 keys series by Kubernetes namespace, so we look up by
            # nodename. Anything absent from the map stays at 0.
            for inst in instance_map.values():
                inst["gpu"] = per_app_gpu.get(inst["nodename"], 0.0)

            excluded = excluded_deployment_names()

            instances = []
            for idx, (name, inst) in enumerate(instance_map.items()):
                if name.startswith("instance-"):
                    continue
                if inst.get("name", "").lower() in excluded:
                    continue

                # Normalise raw units → percentage, clamp to [0, 100].
                cpu_pct = round(min(100.0, inst["cpu_raw"] / cpu_cap * 100) * 100) / 100
                memory_pct = (
                    round(min(100.0, inst["memory_raw"] / mem_cap * 100) * 100) / 100
                )
                gpu_pct = round(inst["gpu"] * 100) / 100

                instances.append(
                    Instance(
                        id=idx + 1,
                        name=inst["name"],
                        nodename=inst["nodename"],
                        cpu=cpu_pct,
                        memory=memory_pct,
                        gpu=gpu_pct,
                        status=self.calculate_status(cpu_pct, memory_pct, gpu_pct),
                    )
                )

            tiers[tier_name] = instances

        return tiers, gpu_totals

    async def get_all_metrics(
        self, tier: int | None = None
    ) -> tuple[dict[str, list[Instance] | None], dict[str, float | None]]:
        """
        Fetch all metrics from Grafana in parallel and return combined data.

        Fetches four Grafana requests concurrently:
          - CPU, Memory (refIds A/B/C per tier)
          - Tier-total GPU: single query (``label_tier`` dimension), ref A
          - Per-app GPU: panel 18 (ref A)

        Args:
            tier: Optional tier number (1-3) to filter results.

        Returns:
            Tuple of (tiers, gpu_totals) where:
              tiers:      tier name → list of Instance objects.
              gpu_totals: tier name → tier-wide GPU percentage.
        """
        cpu_data, memory_data, gpu_tier_data, gpu_per_app_data = await asyncio.gather(
            self.fetch_grafana_data(MetricType.CPU),
            self.fetch_grafana_data(MetricType.MEMORY),
            self.fetch_gpu_tier_totals(),
            self.fetch_gpu_per_app_data(),
        )

        tiers, gpu_totals = self.combine_metrics(
            cpu_data, memory_data, gpu_tier_data, gpu_per_app_data=gpu_per_app_data
        )

        if tier is not None and 1 <= tier <= 3:
            tier_name = f"Tier {tier}"
            return (
                {tier_name: tiers.get(tier_name, [])},
                {tier_name: gpu_totals.get(tier_name)},
            )

        return tiers, gpu_totals


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

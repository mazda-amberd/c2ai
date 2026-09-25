"""LLM gateway counters for cost ingestion, read through the Grafana transport."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime

from c2ai.clients.grafana import GrafanaClient
from c2ai.constants.prometheus import (
    get_grafana_prometheus_datasource,
)
from c2ai.schemas.grafana import (
    GrafanaResponse,
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



class GatewayMetrics:
    """Per-namespace gateway usage over an interval."""

    def __init__(self, client: GrafanaClient):
        self.client = client

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
        return await self.client.fetch_grafana_query(body)

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
        return await self.client.fetch_grafana_query(body)

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
        return await self.client.fetch_grafana_query(body)

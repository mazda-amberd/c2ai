"""
Tests for the Grafana API client.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from c2ai.clients.grafana import GrafanaClient
from c2ai.metrics.gateway import GatewayMetrics
from c2ai.metrics.tiers import TierMetrics, _build_grafana_query_body as build_grafana_query_body
from c2ai.schemas.grafana import MetricType


class TestBuildQueryBody:
    """Tests for the build_grafana_query_body function (k8s / RayCluster model)."""

    def test_build_cpu_query(self):
        """CPU query: cAdvisor rate, pod→RS→deployment join, masked by kube_deployment_labels tier."""
        body = build_grafana_query_body(MetricType.CPU)

        assert "queries" in body
        assert len(body["queries"]) == 3
        assert body["from"] == "now-1m"
        assert body["to"] == "now"

        query = body["queries"][0]
        assert query["refId"] == "A"
        assert query["instant"] is True
        expr = query["expr"]
        assert "container_cpu_usage_seconds_total" in expr
        assert 'owner_kind="ReplicaSet"' in expr
        assert 'owner_kind="Deployment"' in expr
        assert "kube_pod_owner" in expr
        assert "kube_replicaset_owner" in expr
        assert "kube_deployment_labels" in expr
        assert "kube_pod_labels" not in expr
        assert "or on(" not in expr

    def test_cpu_query_tier_namespaces(self):
        """Each tier query filters deployments by label_tier (tier1 / tier2 / tier3|prod)."""
        body = build_grafana_query_body(MetricType.CPU)
        exprs = [q["expr"] for q in body["queries"]]
        assert 'label_tier=~"tier1"' in exprs[0]
        assert 'label_tier=~"tier2"' in exprs[1]
        assert 'label_tier=~"tier3|prod"' in exprs[2]

    def test_build_memory_query(self):
        """Memory query: working set bytes, pod→deployment join, GB via / 1073741824."""
        body = build_grafana_query_body(MetricType.MEMORY)
        query = body["queries"][0]
        expr = query["expr"]
        assert "container_memory_working_set_bytes" in expr
        assert "kube_replicaset_owner" in expr
        assert "/ 1073741824" in expr

    def test_build_gpu_query(self):
        """GPU query: avg Ray util by label_tier (one expr repeated for A/B/C refIds)."""
        body = build_grafana_query_body(MetricType.GPU)
        exprs = [q["expr"] for q in body["queries"]]
        assert len(exprs) == 3
        assert exprs[0] == exprs[1] == exprs[2]
        expr = exprs[0]
        assert "avg by (label_tier)" in expr
        assert "ray_node_gpus_utilization" in expr
        assert "label_replace" in expr
        assert "qwen-5254d" in expr
        assert "qwen-pq9sc" in expr
        assert "qwen-l8dnl" in expr
        assert "llm_total_tokens_total" not in expr
        assert "clamp_min" not in expr

    def test_build_gpu_query_same_expr_all_refids(self):
        """All three refIds use the same global tier-total GPU expression."""
        body = build_grafana_query_body(MetricType.GPU)
        exprs = [q["expr"] for q in body["queries"]]
        assert exprs[0] == exprs[1] == exprs[2]
        assert "avg by (label_tier)" in exprs[0]

    def test_build_memory_total_query(self):
        """MEMORY_TOTAL: sum of per-deployment working-set in tier, normalised to GB."""
        body = build_grafana_query_body(MetricType.MEMORY_TOTAL)
        query = body["queries"][0]
        assert "container_memory_working_set_bytes" in query["expr"]
        assert "kube_replicaset_owner" in query["expr"]

    def test_build_cpu_total_query(self):
        """CPU_TOTAL: sum of per-deployment CPU rate in tier."""
        body = build_grafana_query_body(MetricType.CPU_TOTAL)
        query = body["queries"][0]
        assert "container_cpu_usage_seconds_total" in query["expr"]
        assert "kube_replicaset_owner" in query["expr"]

    def test_all_tiers_included(self):
        """All three refIds A/B/C are present and each tiers via kube_deployment_labels."""
        body = build_grafana_query_body(MetricType.CPU)
        ref_ids = [q["refId"] for q in body["queries"]]
        assert ref_ids == ["A", "B", "C"]
        exprs = [q["expr"] for q in body["queries"]]
        assert all("kube_deployment_labels" in e for e in exprs)


def test_prometheus_datasource_uid_default():
    """Default datasource UID is 'prometheus' (correct for k8s cluster)."""
    import os

    from c2ai.constants.prometheus import get_grafana_prometheus_datasource

    os.environ.pop("GRAFANA_PROMETHEUS_DATASOURCE_UID", None)
    ds = get_grafana_prometheus_datasource()
    assert ds == {"type": "prometheus", "uid": "prometheus"}


def test_prometheus_datasource_uid_from_env(monkeypatch):
    """GRAFANA_PROMETHEUS_DATASOURCE_UID env overrides the default."""
    from c2ai.constants.prometheus import get_grafana_prometheus_datasource

    monkeypatch.setenv("GRAFANA_PROMETHEUS_DATASOURCE_UID", "custom-uid")
    assert get_grafana_prometheus_datasource() == {
        "type": "prometheus",
        "uid": "custom-uid",
    }
    body = build_grafana_query_body(MetricType.CPU)
    assert body["queries"][0]["datasource"]["uid"] == "custom-uid"


class TestGrafanaClient:
    """Tests for the GrafanaClient class."""

    def test_client_initialization_requires_url(self):
        """Test client initialization requires GRAFANA_API_URL."""
        import os

        old_value = os.environ.pop("GRAFANA_API_URL", None)
        try:
            with pytest.raises(
                ValueError, match="GRAFANA_API_URL environment variable is required"
            ):
                GrafanaClient()
        finally:
            if old_value:
                os.environ["GRAFANA_API_URL"] = old_value

    def test_client_initialization_custom(self):
        """Test client initialization with custom values."""
        client = GrafanaClient(
            api_url="https://custom.grafana.io/api",
            api_token="custom-token",
        )
        assert client.api_url == "https://custom.grafana.io/api"
        assert client.api_token == "custom-token"

    @pytest.mark.asyncio
    async def test_fetch_grafana_data_success(self, sample_grafana_response):
        """Test successful data fetch from Grafana API."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        mock_response = MagicMock()
        mock_response.json.return_value = sample_grafana_response.model_dump(
            by_alias=True
        )
        mock_response.raise_for_status = MagicMock()

        with patch("c2ai.clients.grafana.http_client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            result = await tiers.fetch_grafana_data(MetricType.CPU)

            assert result.results is not None
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_grafana_data_error(self):
        """Test error handling when Grafana API fails."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        with patch("c2ai.clients.grafana.http_client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=AsyncMock(),
                response=AsyncMock(status_code=500),
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await tiers.fetch_grafana_data(MetricType.CPU)

    @pytest.mark.asyncio
    async def test_get_all_metrics(
        self, sample_grafana_response, sample_gpu_tier_totals_response
    ):
        """get_all_metrics returns (tiers, gpu_totals) tuple with all tier keys."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        with (
            patch.object(tiers, "fetch_grafana_data", new_callable=AsyncMock
            ) as mock_fetch,
            patch.object(tiers, "fetch_gpu_per_app_data", new_callable=AsyncMock
            ) as mock_gpu_app,
            patch.object(tiers, "fetch_gpu_tier_totals", new_callable=AsyncMock
            ) as mock_gpu_tier,
        ):
            mock_fetch.return_value = sample_grafana_response
            mock_gpu_app.return_value = sample_grafana_response
            mock_gpu_tier.return_value = sample_gpu_tier_totals_response

            tiers, gpu_totals = await tiers.get_all_metrics()

            assert "Tier 1" in tiers
            assert "Tier 2" in tiers
            assert "Tier 3" in tiers
            assert "Tier 1" in gpu_totals
            # fetch_grafana_data called for CPU and MEMORY only; the unused
            # CPU_TOTAL series is no longer queried on every poll.
            assert mock_fetch.call_count == 2
            mock_gpu_app.assert_awaited_once()
            mock_gpu_tier.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_all_metrics_with_tier_filter(
        self, sample_grafana_response, sample_gpu_tier_totals_response
    ):
        """Tier filter returns a single-tier slice of both tiers and gpu_totals."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        with (
            patch.object(tiers, "fetch_grafana_data", new_callable=AsyncMock
            ) as mock_fetch,
            patch.object(tiers, "fetch_gpu_per_app_data", new_callable=AsyncMock
            ) as mock_gpu_app,
            patch.object(tiers, "fetch_gpu_tier_totals", new_callable=AsyncMock
            ) as mock_gpu_tier,
        ):
            mock_fetch.return_value = sample_grafana_response
            mock_gpu_app.return_value = sample_grafana_response
            mock_gpu_tier.return_value = sample_gpu_tier_totals_response

            tiers, gpu_totals = await tiers.get_all_metrics(tier=1)

            assert "Tier 1" in tiers
            assert len(tiers) == 1
            assert "Tier 1" in gpu_totals
            assert len(gpu_totals) == 1


class TestCombineMetrics:
    """Tests for combine_metrics with k8s/RayCluster unit normalisation."""

    def test_combine_tier1_normalised(
        self,
        sample_cpu_used_response,
        sample_memory_used_response,
        sample_grafana_response,
        sample_cpu_total_response,
    ):
        """
        Tier 1: qwen-496gt at 4 cores and 40 GB.
        Default caps: 8 cores, 80 GB → expect 50% each.
        """
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        tiers, _ = tiers.combine_metrics(
            cpu_data=sample_cpu_used_response,
            memory_data=sample_memory_used_response,
            gpu_data=sample_grafana_response,
            cpu_total_data=sample_cpu_total_response,
        )

        tier1 = tiers["Tier 1"]
        assert tier1 is not None and len(tier1) == 1
        inst = tier1[0]
        assert inst.name == "qwen-496gt"
        assert inst.nodename == "tier1"
        assert 49 <= inst.cpu <= 51
        assert 49 <= inst.memory <= 51

    def test_combine_tier2_clamped_at_100(
        self,
        sample_cpu_used_response,
        sample_memory_used_response,
        sample_grafana_response,
        sample_cpu_total_response,
    ):
        """Tier 2: 8 cores / 80 GB = 100% each (at cap, clamped)."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        tiers, _ = tiers.combine_metrics(
            cpu_data=sample_cpu_used_response,
            memory_data=sample_memory_used_response,
            gpu_data=sample_grafana_response,
            cpu_total_data=sample_cpu_total_response,
        )

        tier2 = tiers["Tier 2"]
        assert tier2 is not None and len(tier2) == 1
        inst = tier2[0]
        assert inst.cpu == 100.0
        assert inst.memory == 100.0

    def test_combine_tier3_normalised(
        self,
        sample_cpu_used_response,
        sample_memory_used_response,
        sample_grafana_response,
        sample_cpu_total_response,
    ):
        """Tier 3: 6 cores / 60 GB → 75% each."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        tiers, _ = tiers.combine_metrics(
            cpu_data=sample_cpu_used_response,
            memory_data=sample_memory_used_response,
            gpu_data=sample_grafana_response,
            cpu_total_data=sample_cpu_total_response,
        )

        tier3 = tiers["Tier 3"]
        assert tier3 is not None and len(tier3) == 1
        inst = tier3[0]
        assert 74 <= inst.cpu <= 76
        assert 74 <= inst.memory <= 76

    def test_combine_tier4_is_null(
        self,
        sample_cpu_used_response,
        sample_memory_used_response,
        sample_grafana_response,
        sample_cpu_total_response,
    ):
        """Tier 4 has no config entry — always None in tiers and gpu_totals."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        tiers, gpu_totals = tiers.combine_metrics(
            cpu_data=sample_cpu_used_response,
            memory_data=sample_memory_used_response,
            gpu_data=sample_grafana_response,
            cpu_total_data=sample_cpu_total_response,
        )

        assert tiers["Tier 4"] is None
        assert gpu_totals["Tier 4"] is None

    def test_status_calculation(self):
        """Status thresholds: Healthy ≤50%, Warning >50% & <76%, Critical ≥76%."""
        assert TierMetrics.calculate_status(30, 30, 30).value == "Healthy"
        assert TierMetrics.calculate_status(50, 50, 50).value == "Healthy"
        assert TierMetrics.calculate_status(30, 51, 30).value == "Warning"
        assert TierMetrics.calculate_status(75, 30, 30).value == "Warning"
        assert TierMetrics.calculate_status(30, 76, 30).value == "Critical"
        assert TierMetrics.calculate_status(80, 30, 30).value == "Critical"



class TestGpuQueryBuilders:
    """Tests for GPU-specific PromQL builders in prometheus.py."""

    def test_k8s_gpu_util_is_ray_avg_by_label_tier(self):
        """_k8s_gpu_util returns avg Ray GPU util grouped by label_tier (no token query)."""
        from c2ai.constants.prometheus import _k8s_gpu_util

        expr = _k8s_gpu_util()

        assert "avg by (label_tier)" in expr
        assert "ray_node_gpus_utilization" in expr
        assert "label_replace" in expr
        assert "qwen-5254d" in expr
        assert "qwen-pq9sc" in expr
        assert "qwen-l8dnl" in expr
        assert "llm_total_tokens_total" not in expr
        assert "kube_deployment_labels" not in expr
        assert "ray_io_cluster=~" not in expr

    def test_gpu_cluster_name_env_override(self, monkeypatch):
        """ATHENA_TIER{N}_GPU_CLUSTER overrides the default cluster name."""
        from c2ai.constants.prometheus import _gpu_cluster_name

        monkeypatch.setenv("ATHENA_TIER2_GPU_CLUSTER", "custom-cluster")
        assert _gpu_cluster_name("Tier2") == "custom-cluster"

    def test_get_gpu_per_app_query_contains_key_parts(self):
        """get_gpu_per_app_query() produces panel-18 PromQL (all apps, no regex filters)."""
        from c2ai.constants.prometheus import get_gpu_per_app_query

        expr = get_gpu_per_app_query()

        assert "llm_total_tokens_total" in expr
        assert "kube_deployment_labels" in expr
        assert "ray_node_gpus_utilization" in expr
        assert "avg_over_time" in expr
        assert "on(label_tier) group_left()" in expr
        assert "clamp_min" in expr
        assert "label_tier" in expr
        assert "sum by (namespace)" in expr
        assert "qwen-5254d" in expr
        assert "avg by (label_tier)" in expr
        assert "label_app" not in expr
        assert "ray_io_cluster=~" not in expr
        assert "namespace=~" not in expr

    def test_get_gpu_per_app_query_has_cluster_names_in_label_replace(self):
        """Per-app tier multiplier references all three Ray cluster literals."""
        from c2ai.constants.prometheus import get_gpu_per_app_query

        expr = get_gpu_per_app_query()

        assert "qwen-5254d" in expr
        assert "qwen-pq9sc" in expr
        assert "qwen-l8dnl" in expr

    @pytest.mark.asyncio
    async def test_fetch_gpu_per_app_data_posts_only_panel18(
        self, sample_grafana_response
    ):
        """fetch_gpu_per_app_data sends a single ref A (panel 18); no Ray fallbacks."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        mock_response = MagicMock()
        mock_response.json.return_value = sample_grafana_response.model_dump(
            by_alias=True
        )
        mock_response.raise_for_status = MagicMock()

        with patch("c2ai.clients.grafana.http_client") as mock_client_class:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.__aenter__.return_value = mock_http
            mock_http.__aexit__.return_value = None
            mock_client_class.return_value = mock_http

            result = await tiers.fetch_gpu_per_app_data()

            assert result.results is not None
            call_kwargs = mock_http.post.call_args
            body = (
                call_kwargs[1]["json"]
                if "json" in call_kwargs[1]
                else call_kwargs[0][1]
            )
            assert len(body["queries"]) == 1
            assert body["queries"][0]["refId"] == "A"
            assert "llm_total_tokens_total" in body["queries"][0]["expr"]
            assert "ray_node_gpus_utilization" in body["queries"][0]["expr"]



    @pytest.mark.asyncio
    async def test_fetch_gateway_request_duration_uses_interval_and_namespace_filter(
        self, sample_grafana_response
    ):
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        gateway = GatewayMetrics(client)
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = start + timedelta(hours=6)

        with patch.object(
            client,
            "fetch_grafana_query",
            new_callable=AsyncMock,
            return_value=sample_grafana_response,
        ) as fetch_query:
            await gateway.fetch_llm_gateway_request_duration_by_namespace(
                period_start=start,
                period_end=end,
                source_namespace="customer.app",
            )

        body = fetch_query.await_args.args[0]
        expression = body["queries"][0]["expr"]
        assert "sum by (source_namespace)" in expression
        assert "increase(llm_duration_seconds_sum" in expression
        assert "llm_total_tokens_total" not in expression
        assert '"pod_ip", "$1", "requested_host"' in expression
        assert "* on (pod_ip) group_left(source_namespace)" in expression
        assert 'namespace=~"customer\\.app"' in expression
        assert "[21600s]" in expression
        assert body["from"] == str(int(start.timestamp() * 1000))
        assert body["to"] == str(int(end.timestamp() * 1000))

    @pytest.mark.asyncio
    async def test_fetch_namespace_tiers_uses_historical_interval(
        self, sample_grafana_response
    ):
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        gateway = GatewayMetrics(client)
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = start + timedelta(hours=2)

        with patch.object(
            client,
            "fetch_grafana_query",
            new_callable=AsyncMock,
            return_value=sample_grafana_response,
        ) as fetch_query:
            await gateway.fetch_llm_gateway_namespace_tiers(
                period_start=start,
                period_end=end,
            )

        expression = fetch_query.await_args.args[0]["queries"][0]["expr"]
        assert "kube_deployment_labels" in expression
        assert "max_over_time" in expression
        assert "[7200s]" in expression


class TestGpuTierTotals:
    """Tier-total GPU fetch and parsing."""

    @pytest.mark.asyncio
    async def test_fetch_gpu_tier_totals_posts_single_ray_query(
        self, sample_grafana_response
    ):
        """fetch_gpu_tier_totals sends one ref A with METRIC_QUERIES[GPU] expr."""
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        tiers = TierMetrics(client)

        mock_response = MagicMock()
        mock_response.json.return_value = sample_grafana_response.model_dump(
            by_alias=True
        )
        mock_response.raise_for_status = MagicMock()

        with patch("c2ai.clients.grafana.http_client") as mock_client_class:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.__aenter__.return_value = mock_http
            mock_http.__aexit__.return_value = None
            mock_client_class.return_value = mock_http

            await tiers.fetch_gpu_tier_totals()

            body = mock_http.post.call_args[1]["json"]
            assert len(body["queries"]) == 1
            assert body["queries"][0]["refId"] == "A"
            assert "avg by (label_tier)" in body["queries"][0]["expr"]
            assert "ray_node_gpus_utilization" in body["queries"][0]["expr"]

    def test_parse_gpu_tier_totals_maps_label_tier(
        self, sample_gpu_tier_totals_response
    ):
        """_parse_gpu_tier_totals maps tier1/tier2/tier3 frame labels to values."""
        GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        out = TierMetrics._parse_gpu_tier_totals(sample_gpu_tier_totals_response)
        assert out["tier1"] == 0.5
        assert out["tier2"] == 0.5
        assert out["tier3"] == 0.5


class TestGatewayCounterQueries:
    """Tests for the LLM gateway cost queries by caller namespace."""

    PERIOD_START = datetime(2026, 8, 1, tzinfo=UTC)
    PERIOD_END = PERIOD_START + timedelta(hours=1)

    def _body(self, **overrides) -> dict:
        return GatewayMetrics._gateway_counter_by_namespace_body(
            counter_name="llm_input_tokens_total",
            period_start=self.PERIOD_START,
            period_end=self.PERIOD_END,
            source_namespace=None,
            **overrides,
        )

    def test_duration_query_counts_only_the_private_gpu_provider(self):
        """GPU time must not be charged for calls served by a public API."""
        body = GatewayMetrics._gateway_counter_by_namespace_body(
            counter_name="llm_duration_seconds_sum",
            period_start=self.PERIOD_START,
            period_end=self.PERIOD_END,
            source_namespace=None,
            counter_selector='{provider="vllm"}',
        )

        expr = body["queries"][0]["expr"]
        assert 'increase(llm_duration_seconds_sum{provider="vllm"}[3600s])' in expr
        assert "sum by (source_namespace) (" in expr

    def test_public_token_query_keeps_the_provider_and_model_labels(self):
        """Public pricing is per model, so those labels survive the join."""
        body = self._body(
            counter_selector='{provider!="vllm"}',
            group_by=("source_namespace", "provider", "model"),
        )

        expr = body["queries"][0]["expr"]
        assert "sum by (source_namespace, provider, model) (" in expr
        assert 'increase(llm_input_tokens_total{provider!="vllm"}[3600s])' in expr
        assert "kube_pod_info" in expr
        assert 'label_replace(' in expr

    def test_counter_query_defaults_to_every_provider_by_namespace(self):
        expr = self._body()["queries"][0]["expr"]

        assert "sum by (source_namespace) (" in expr
        assert "increase(llm_input_tokens_total[3600s])" in expr

    @pytest.mark.asyncio
    async def test_public_token_fetchers_query_the_two_token_counters(self):
        client = GrafanaClient(
            api_url="https://test.grafana.io/api", api_token="test-token"
        )
        gateway = GatewayMetrics(client)
        bodies = []

        async def capture(body):
            bodies.append(body)
            return MagicMock()

        with patch.object(client, "fetch_grafana_query", side_effect=capture):
            await gateway.fetch_llm_gateway_public_input_tokens_by_model(
                period_start=self.PERIOD_START,
                period_end=self.PERIOD_END,
            )
            await gateway.fetch_llm_gateway_public_output_tokens_by_model(
                period_start=self.PERIOD_START,
                period_end=self.PERIOD_END,
            )
            await gateway.fetch_llm_gateway_request_duration_by_namespace(
                period_start=self.PERIOD_START,
                period_end=self.PERIOD_END,
            )

        input_expr, output_expr, duration_expr = (
            body["queries"][0]["expr"] for body in bodies
        )
        assert "llm_input_tokens_total" in input_expr
        assert "llm_output_tokens_total" in output_expr
        assert 'provider!="vllm"' in input_expr
        assert 'provider!="vllm"' in output_expr
        assert "sum by (source_namespace, provider, model) (" in output_expr
        # The two selectors are complementary: no request is charged twice.
        assert 'provider="vllm"' in duration_expr
        assert "sum by (source_namespace) (" in duration_expr

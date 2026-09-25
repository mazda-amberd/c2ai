"""
Tests for metrics processing business logic (k8s / RayCluster label model).

All frames use owner_name + namespace labels as returned by the k8s Grafana
datasource (docs/grafana-k8s-exploration.md §7).
"""


from c2ai.clients.grafana import GrafanaClient
from c2ai.metrics.tiers import TierMetrics
from c2ai.schemas.grafana import (
    GrafanaField,
    GrafanaFieldLabels,
    GrafanaFrame,
    GrafanaFrameData,
    GrafanaFrameSchema,
    GrafanaQueryResult,
    GrafanaResponse,
    Status,
)


def _rc_frame(ref_id: str, owner_name: str, namespace: str, value: float) -> GrafanaFrame:
    """RayCluster CPU/memory frame: labels = {owner_name, namespace}."""
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId=ref_id,
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(owner_name=owner_name, namespace=namespace),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )


def _gpu_frame(ref_id: str, ray_io_cluster: str, namespace: str, value: float) -> GrafanaFrame:
    """GPU frame: labels = {ray_io_cluster, namespace}."""
    return GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId=ref_id,
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(ray_io_cluster=ray_io_cluster, namespace=namespace),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )


def _empty_response() -> GrafanaResponse:
    return GrafanaResponse(
        results={k: GrafanaQueryResult(status=200, frames=[]) for k in ("A", "B", "C")}
    )


# =============================================================================
# calculate_status
# =============================================================================

class TestCalculateStatus:
    """Tests for the calculate_status static method."""

    def test_healthy_all_low(self):
        assert TierMetrics.calculate_status(10.0, 20.0, 30.0) == Status.HEALTHY

    def test_healthy_at_threshold(self):
        assert TierMetrics.calculate_status(50.0, 50.0, 50.0) == Status.HEALTHY

    def test_warning_cpu(self):
        assert TierMetrics.calculate_status(60.0, 20.0, 20.0) == Status.WARNING

    def test_warning_memory(self):
        assert TierMetrics.calculate_status(10.0, 60.0, 20.0) == Status.WARNING

    def test_warning_gpu(self):
        assert TierMetrics.calculate_status(10.0, 20.0, 60.0) == Status.WARNING

    def test_warning_just_over_50(self):
        assert TierMetrics.calculate_status(51.0, 50.0, 30.0) == Status.WARNING

    def test_critical_cpu(self):
        assert TierMetrics.calculate_status(76.0, 20.0, 20.0) == Status.CRITICAL

    def test_critical_memory(self):
        assert TierMetrics.calculate_status(10.0, 76.0, 20.0) == Status.CRITICAL

    def test_critical_gpu(self):
        assert TierMetrics.calculate_status(10.0, 20.0, 76.0) == Status.CRITICAL

    def test_critical_overrides_warning(self):
        assert TierMetrics.calculate_status(80.0, 20.0, 60.0) == Status.CRITICAL

    def test_75_is_warning_not_critical(self):
        assert TierMetrics.calculate_status(75.0, 75.0, 75.0) == Status.WARNING


# =============================================================================
# extract_instances
# =============================================================================

class TestExtractInstances:
    """Tests for GrafanaClient.extract_instances with k8s label shapes."""

    def test_extract_raycluster_cpu_frame(self, sample_cpu_frame):
        """namespace + owner_name → groupname key namespace/deployment; nodename = namespace."""
        instances = TierMetrics.extract_instances([sample_cpu_frame])

        assert len(instances) == 1
        assert instances[0]["groupname"] == "tier1/qwen-496gt"
        assert instances[0]["display_name"] == "qwen-496gt"
        assert instances[0]["nodename"] == "tier1"
        assert instances[0]["value"] == 4.0
        assert instances[0]["id"] == 1

    def test_extract_empty_frames(self):
        instances = TierMetrics.extract_instances([])
        assert instances == []

    def test_extract_multiple_raycluster_frames(self):
        """Multiple RayClusters in one refId response."""
        frames = [
            _rc_frame("A", "qwen-496gt", "tier1", 4.0),
            _rc_frame("A", "qwen-hrcg7", "tier2", 8.0),
        ]
        instances = TierMetrics.extract_instances(frames)

        assert len(instances) == 2
        assert instances[0]["groupname"] == "tier1/qwen-496gt"
        assert instances[1]["groupname"] == "tier2/qwen-hrcg7"
        assert instances[0]["id"] == 1
        assert instances[1]["id"] == 2

    def test_extract_gpu_frame_uses_ray_io_cluster(self):
        """GPU frames use ray_io_cluster as deployment label in namespace/deployment key."""
        frame = _gpu_frame("A", "qwen-496gt", "tier1", 0.5)
        instances = TierMetrics.extract_instances([frame])

        assert len(instances) == 1
        assert instances[0]["groupname"] == "tier1/qwen-496gt"
        assert instances[0]["display_name"] == "qwen-496gt"
        assert instances[0]["nodename"] == "tier1"
        assert instances[0]["value"] == 0.5

    def test_extract_no_labels_falls_back_to_instance_name(self):
        """Frames without labels get a default 'instance-N' groupname."""
        frame = GrafanaFrame(
            schema=GrafanaFrameSchema(
                refId="A",
                fields=[
                    GrafanaField(name="Time", type="time"),
                    GrafanaField(name="Value", type="number", labels=None),
                ],
            ),
            data=GrafanaFrameData(values=[[1234], [15.0]]),
        )
        instances = TierMetrics.extract_instances([frame])

        assert len(instances) == 1
        assert instances[0]["groupname"] == "instance-1"
        assert instances[0]["value"] == 15.0


# =============================================================================
# combine_metrics
# =============================================================================

def _tier_total_gpu_frames_response(values: dict[str, float]) -> GrafanaResponse:
    """Simulate fetch_gpu_tier_totals: ref A with one frame per ``label_tier``."""
    frames = [
        GrafanaFrame(
            schema=GrafanaFrameSchema(
                refId="A",
                fields=[
                    GrafanaField(name="Time", type="time"),
                    GrafanaField(
                        name="Value",
                        type="number",
                        labels=GrafanaFieldLabels(label_tier=lt),
                    ),
                ],
            ),
            data=GrafanaFrameData(values=[[1768233053154], [v]]),
        )
        for lt, v in values.items()
    ]
    return GrafanaResponse(
        results={"A": GrafanaQueryResult(status=200, frames=frames)}
    )


def _per_app_gpu_response(namespace: str, label_tier: str, value: float) -> GrafanaResponse:
    """
    Simulate the panel-18 per-app GPU attribution response.

    The formula returns {namespace, label_tier} labels with a 0–num_gpus value.
    """
    frame = GrafanaFrame(
        schema=GrafanaFrameSchema(
            refId="A",
            fields=[
                GrafanaField(name="Time", type="time"),
                GrafanaField(
                    name="Value",
                    type="number",
                    labels=GrafanaFieldLabels(namespace=namespace),
                ),
            ],
        ),
        data=GrafanaFrameData(values=[[1768233053154], [value]]),
    )
    return GrafanaResponse(
        results={"A": GrafanaQueryResult(status=200, frames=[frame])}
    )


class TestCombineMetrics:
    """Tests for combine_metrics with RayCluster unit normalisation."""

    def test_basic_structure(self, sample_grafana_response, sample_cpu_total_response):
        """All four tier keys are always present; Tier 4 is None."""
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        tiers, gpu_totals = tiers.combine_metrics(
            sample_grafana_response,
            sample_grafana_response,
            sample_grafana_response,
            sample_cpu_total_response,
        )

        assert "Tier 1" in tiers
        assert "Tier 2" in tiers
        assert "Tier 3" in tiers
        assert tiers["Tier 4"] is None
        assert "Tier 1" in gpu_totals
        assert gpu_totals["Tier 4"] is None

    def test_empty_response_yields_empty_lists(self, sample_cpu_total_response):
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)
        empty = _empty_response()

        tiers, gpu_totals = tiers.combine_metrics(empty, empty, empty, sample_cpu_total_response)

        assert tiers["Tier 1"] == []
        assert tiers["Tier 2"] == []
        assert tiers["Tier 3"] == []
        assert gpu_totals["Tier 1"] == 0.0

    def test_cpu_normalised_to_percentage(self, monkeypatch):
        """4 cores / 8-core cap → 50%; values are output as percentages."""
        monkeypatch.setenv("ATHENA_CPU_CORES_CAP", "8")
        monkeypatch.setenv("ATHENA_MEMORY_GB_CAP", "80")
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        cpu_resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200, frames=[_rc_frame("A", "qwen-496gt", "tier1", 4.0)]
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )
        empty = _empty_response()

        tiers, _ = tiers.combine_metrics(cpu_resp, empty, empty, empty)
        inst = tiers["Tier 1"][0]
        assert 49 <= inst.cpu <= 51

    def test_memory_normalised_to_percentage(self, monkeypatch):
        """40 GB / 80-GB cap → 50%."""
        monkeypatch.setenv("ATHENA_CPU_CORES_CAP", "8")
        monkeypatch.setenv("ATHENA_MEMORY_GB_CAP", "80")
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        mem_resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200, frames=[_rc_frame("A", "qwen-496gt", "tier1", 40.0)]
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )
        empty = _empty_response()

        tiers, _ = tiers.combine_metrics(empty, mem_resp, empty, empty)
        inst = tiers["Tier 1"][0]
        assert 49 <= inst.memory <= 51

    def test_tier_gpu_total_from_labeled_frames(self):
        """
        Tier-total GPU instant response carries ``label_tier`` per series.
        gpu_totals[tier] stores that raw value unchanged.
        """
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)
        empty = _empty_response()
        gpu_resp = _tier_total_gpu_frames_response({"tier1": 8.9})

        _, gpu_totals = tiers.combine_metrics(empty, empty, gpu_resp, empty)

        assert abs(gpu_totals["Tier 1"] - 8.9) < 0.01

    def test_tier_gpu_total_not_clamped(self):
        """Tier total is the raw 0–num_gpus value; values above 100 are kept."""
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)
        empty = _empty_response()
        gpu_resp = _tier_total_gpu_frames_response({"tier1": 200.0})

        _, gpu_totals = tiers.combine_metrics(empty, empty, gpu_resp, empty)

        assert gpu_totals["Tier 1"] == 200.0

    def test_per_app_gpu_matched_by_namespace(self, monkeypatch):
        """
        gpu_per_app_data frames with namespace label are matched to instances
        by nodename.  The matched app gets the per-app value; others get 0.
        """
        monkeypatch.setenv("ATHENA_CPU_CORES_CAP", "8")
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        cpu_resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200,
                    frames=[
                        _rc_frame("A", "app-alpha", "ns-alpha", 2.0),
                        _rc_frame("A", "app-beta", "ns-beta", 2.0),
                    ],
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )
        empty = _empty_response()
        # Only ns-alpha has token traffic; ns-beta gets 0.
        gpu_per_app = _per_app_gpu_response("ns-alpha", "tier1", 5.5)

        tiers, _ = tiers.combine_metrics(cpu_resp, empty, empty, empty, gpu_per_app)

        instances = {i.nodename: i for i in tiers["Tier 1"]}
        assert abs(instances["ns-alpha"].gpu - 5.5) < 0.01
        assert instances["ns-beta"].gpu == 0.0

    def test_per_app_gpu_zero_when_no_attribution(self):
        """When panel-18 has no entry for an instance's namespace, GPU is 0."""
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        cpu_resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200,
                    frames=[_rc_frame("A", "qwen-496gt", "tier1", 4.0)],
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )
        empty = _empty_response()
        gpu_resp = _tier_total_gpu_frames_response({"tier1": 8.9})
        empty_gpu_attribution = GrafanaResponse(
            results={"A": GrafanaQueryResult(status=200, frames=[])}
        )

        tiers, gpu_totals = tiers.combine_metrics(
            cpu_resp, empty, gpu_resp, empty, empty_gpu_attribution
        )

        assert abs(gpu_totals["Tier 1"] - 8.9) < 0.01
        assert tiers["Tier 1"][0].gpu == 0.0

    def test_critical_status_from_high_cpu(self, monkeypatch):
        """8 cores / 8-core cap = 100% → Critical."""
        monkeypatch.setenv("ATHENA_CPU_CORES_CAP", "8")
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        cpu_resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200, frames=[_rc_frame("A", "qwen-496gt", "tier1", 8.0)]
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )
        empty = _empty_response()

        tiers, _ = tiers.combine_metrics(cpu_resp, empty, empty, empty)
        assert tiers["Tier 1"][0].status == Status.CRITICAL

    def test_default_name_instances_filtered(self, sample_cpu_total_response):
        """Frames without labels produce 'instance-N' names that are filtered out."""
        client = GrafanaClient(api_url="https://test.grafana.io/api")
        tiers = TierMetrics(client)

        resp = GrafanaResponse(
            results={
                "A": GrafanaQueryResult(
                    status=200,
                    frames=[
                        GrafanaFrame(
                            schema=GrafanaFrameSchema(
                                refId="A",
                                fields=[
                                    GrafanaField(name="Time", type="time"),
                                    GrafanaField(name="Value", type="number", labels=None),
                                ],
                            ),
                            data=GrafanaFrameData(values=[[1234], [10.0]]),
                        )
                    ],
                ),
                "B": GrafanaQueryResult(status=200, frames=[]),
                "C": GrafanaQueryResult(status=200, frames=[]),
            }
        )

        tiers, _ = tiers.combine_metrics(resp, resp, resp, sample_cpu_total_response)
        assert tiers["Tier 1"] == []


# =============================================================================
# _parse_gpu_per_app
# =============================================================================

class TestParseGpuPerApp:
    """Tests for GrafanaClient._parse_gpu_per_app."""

    def test_returns_empty_on_none(self):
        assert TierMetrics._parse_gpu_per_app(None) == {}

    def test_returns_empty_on_missing_refid(self):
        resp = GrafanaResponse(results={})
        assert TierMetrics._parse_gpu_per_app(resp) == {}

    def test_parses_namespace_and_value(self):
        """Frames with namespace label are indexed by namespace, value stored as-is."""
        resp = _per_app_gpu_response("amberd-gpu-request2", "tier1", 8.9)
        result = TierMetrics._parse_gpu_per_app(resp)

        assert "amberd-gpu-request2" in result
        assert abs(result["amberd-gpu-request2"] - 8.9) < 0.01

    def test_value_not_clamped(self):
        """Raw panel-18 values are kept as-is (no upper clamp)."""
        resp = _per_app_gpu_response("some-ns", "tier1", 999.0)
        result = TierMetrics._parse_gpu_per_app(resp)
        assert result["some-ns"] == 999.0

    def test_frames_without_namespace_skipped(self):
        """Frames with no namespace label are silently ignored."""
        frame = GrafanaFrame(
            schema=GrafanaFrameSchema(
                refId="A",
                fields=[
                    GrafanaField(name="Time", type="time"),
                    GrafanaField(name="Value", type="number", labels=None),
                ],
            ),
            data=GrafanaFrameData(values=[[1234], [5.0]]),
        )
        resp = GrafanaResponse(
            results={"A": GrafanaQueryResult(status=200, frames=[frame])}
        )
        assert TierMetrics._parse_gpu_per_app(resp) == {}

    def test_exported_namespace_label(self):
        """Relabel-conflicted metrics may expose Kubernetes ns as exported_namespace."""
        frame = GrafanaFrame(
            schema=GrafanaFrameSchema(
                refId="A",
                fields=[
                    GrafanaField(name="Time", type="time"),
                    GrafanaField(
                        name="Value",
                        type="number",
                        labels=GrafanaFieldLabels(exported_namespace="amberd-demo"),
                    ),
                ],
            ),
            data=GrafanaFrameData(values=[[1234], [2.5]]),
        )
        resp = GrafanaResponse(
            results={"A": GrafanaQueryResult(status=200, frames=[frame])}
        )
        assert TierMetrics._parse_gpu_per_app(resp) == {"amberd-demo": 2.5}

    def test_namespace_on_time_field_still_parsed(self):
        """Grafana may attach labels to the time field; still resolve namespace."""
        frame = GrafanaFrame(
            schema=GrafanaFrameSchema(
                refId="A",
                fields=[
                    GrafanaField(
                        name="Time",
                        type="time",
                        labels=GrafanaFieldLabels(namespace="amberd-from-time"),
                    ),
                    GrafanaField(name="Value", type="number", labels=None),
                ],
            ),
            data=GrafanaFrameData(values=[[1234], [1.25]]),
        )
        resp = GrafanaResponse(
            results={"A": GrafanaQueryResult(status=200, frames=[frame])}
        )
        assert TierMetrics._parse_gpu_per_app(resp) == {"amberd-from-time": 1.25}

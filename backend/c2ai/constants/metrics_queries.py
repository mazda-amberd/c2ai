"""PromQL catalogue for ``GET /api/v2/metrics``, taken from the Story 1.2-1.4 dashboards.

Source of truth (ATHENAV2607-3), read from the Grafana dashboard JSON:
  cluster      -> uid ``k8s-cluster-core``
  tier         -> uid ``story-1-3-tier-gpu-metrics``
  application  -> uid ``story-1-4-application-metrics``

Two deliberate departures from the panels, both forced by the API returning one value per
scope where a panel draws a multi-series graph:

* Panels that plot per-GPU or per-pod series are wrapped in an outer aggregator. The
  choice per metric is recorded in ``AGGREGATION_NOTES`` and surfaced in the API docs.
* Application panels select one workload via ``$namespace`` / ``$application``. The API
  reports every workload at once, so the pod filter is replaced by the pod -> ReplicaSet
  -> Deployment join already used elsewhere, grouping by (namespace, deployment).

Grafana macros (``$__range``, ``$__rate_interval``) are substituted from the request window.
"""

from __future__ import annotations

from c2ai.metrics.promql import REAL_CONTAINERS, by_deployment
from c2ai.schemas.metrics import MetricUnit

# Virtual interfaces and pseudo block devices, excluded exactly as the cluster panels do.
_NET_EXCLUDE = r'device!~"lo|veth.*|cali.*|flannel.*|cni.*|docker.*|br-.*|virbr.*|tun.*|tap.*"'
_DISK_EXCLUDE = r'device!~"loop.*|ram.*|fd.*|sr.*"'

AGGREGATION_NOTES = {
    "gpu_utilization_percent": "avg across the tier's GPUs",
    "gpu_temperature_celsius": "max — the hottest GPU in the tier",
    "gpu_power_watts": "sum — total board power for the tier",
    "gpu_memory_percent": "avg across the tier's GPUs",
}


def _dcgm_for_tier(metric_expr: str, tier_ns: str) -> str:
    """DCGM series restricted to the GPUs Ray exposes in this tier (panel 3/4/6 pattern)."""
    return (
        f'max by (gpu, GpuIndex) ('
        f'label_replace({metric_expr}, "GpuIndex", "$1", "gpu", "(.*)")'
        f' and on (GpuIndex) max by (GpuIndex)'
        f' (ray_node_gpus_utilization{{namespace=~"{tier_ns}"}} >= bool 0))'
    )


def cluster_queries(rng: str, rate: str) -> dict[str, tuple[str, MetricUnit]]:
    """Story 1.2 — node and pod health, throughput, and allocation against node capacity."""
    def allocated(resource: str) -> str:
        return (
            f'100 * sum(kube_pod_container_resource_requests{{resource="{resource}"}}'
            f' * on(namespace, pod) group_left() max by(namespace, pod)'
            f' (kube_pod_status_phase{{phase=~"Pending|Running"}} == 1))'
            f' / sum(kube_node_status_capacity{{resource="{resource}"}})'
        )

    return {
        "nodes_ready": (
            'sum(kube_node_status_condition{condition="Ready",status="true"})',
            MetricUnit.COUNT,
        ),
        "nodes_not_ready": (
            'sum(kube_node_status_condition{condition="Ready",status=~"false|unknown"})',
            MetricUnit.COUNT,
        ),
        "pods_running": ('sum(kube_pod_status_phase{phase="Running"})', MetricUnit.COUNT),
        "pods_pending": ('sum(kube_pod_status_phase{phase="Pending"})', MetricUnit.COUNT),
        "pods_failed": ('sum(kube_pod_status_phase{phase="Failed"})', MetricUnit.COUNT),
        "pod_restarts": (
            f"round(sum(increase(kube_pod_container_status_restarts_total[{rng}])))",
            MetricUnit.COUNT,
        ),
        "network_receive_bps": (
            f"sum(rate(node_network_receive_bytes_total{{{_NET_EXCLUDE}}}[{rate}]))",
            MetricUnit.BYTES_PER_SECOND,
        ),
        "network_transmit_bps": (
            f"sum(rate(node_network_transmit_bytes_total{{{_NET_EXCLUDE}}}[{rate}]))",
            MetricUnit.BYTES_PER_SECOND,
        ),
        "disk_read_bps": (
            f"sum(rate(node_disk_read_bytes_total{{{_DISK_EXCLUDE}}}[{rate}]))",
            MetricUnit.BYTES_PER_SECOND,
        ),
        "disk_write_bps": (
            f"sum(rate(node_disk_written_bytes_total{{{_DISK_EXCLUDE}}}[{rate}]))",
            MetricUnit.BYTES_PER_SECOND,
        ),
        "cpu_allocated_percent": (allocated("cpu"), MetricUnit.PERCENT),
        "memory_allocated_percent": (allocated("memory"), MetricUnit.PERCENT),
        "pod_capacity_percent": (
            '100 * sum(kube_pod_status_phase{phase=~"Pending|Running"})'
            ' / sum(kube_node_status_capacity{resource="pods"})',
            MetricUnit.PERCENT,
        ),
    }


def tier_queries(tier_ns: str, rng: str, rate: str) -> dict[str, tuple[str, MetricUnit]]:
    """Story 1.3 — Ray GPU inventory and DCGM device health for one tier namespace."""
    return {
        "gpus_available": (
            f'sum(ray_resources{{namespace=~"{tier_ns}",Name="GPU",State="AVAILABLE"}})',
            MetricUnit.GPUS,
        ),
        "gpus_allocated": (
            f'sum(ray_resources{{namespace=~"{tier_ns}",Name="GPU",State="USED"}})',
            MetricUnit.GPUS,
        ),
        "active_deployments": (
            f'count(max by (application, deployment)'
            f' (ray_serve_deployment_replica_healthy{{namespace=~"{tier_ns}"}}) > 0)',
            MetricUnit.COUNT,
        ),
        "gpu_utilization_percent": (
            f'avg(max by (pod, GpuIndex)'
            f' (ray_node_gpus_utilization{{namespace=~"{tier_ns}"}}))',
            MetricUnit.PERCENT,
        ),
        "gpu_temperature_celsius": (
            f"max({_dcgm_for_tier('DCGM_FI_DEV_GPU_TEMP', tier_ns)})",
            MetricUnit.CELSIUS,
        ),
        "gpu_power_watts": (
            f"sum({_dcgm_for_tier('DCGM_FI_DEV_POWER_USAGE', tier_ns)})",
            MetricUnit.WATTS,
        ),
        "gpu_memory_percent": (
            "avg("
            + _dcgm_for_tier(
                "(100 * DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE))",
                tier_ns,
            )
            + ")",
            MetricUnit.PERCENT,
        ),
    }


def application_queries(rng: str, rate: str) -> dict[str, tuple[str, MetricUnit]]:
    """Story 1.4 — per-workload health, resources, and LLM/HTTP behaviour, fleet-wide."""
    def http_share(codes: str) -> str:
        """Share of requests in a status class; label name varies by app framework."""
        matched = " or ".join(
            by_deployment(f'rate(http_requests_total{{{label}=~"{codes}"}}[{rate}])')
            for label in ("status_code", "status", "code")
        )
        total = by_deployment(f"rate(http_requests_total[{rate}])")
        return f"100 * ({matched}) / ({total})"

    return {
        "health": (
            'max by (namespace, deployment) (kube_deployment_status_condition'
            '{condition="Available",status="true"})',
            MetricUnit.COUNT,
        ),
        "uptime_seconds": (
            "time() - max by (namespace, deployment) (kube_deployment_created)",
            MetricUnit.SECONDS,
        ),
        "replicas_running": (
            "max by (namespace, deployment) (kube_deployment_status_replicas_available)",
            MetricUnit.COUNT,
        ),
        "restarts": (
            by_deployment(f"round(increase(kube_pod_container_status_restarts_total[{rng}]))"),
            MetricUnit.COUNT,
        ),
        "cpu_cores": (
            by_deployment(
                f"rate(container_cpu_usage_seconds_total{{{REAL_CONTAINERS}}}[{rate}])"
            ),
            MetricUnit.CORES,
        ),
        "memory_bytes": (
            by_deployment(f"container_memory_working_set_bytes{{{REAL_CONTAINERS}}}"),
            MetricUnit.BYTES,
        ),
        "prompt_tokens_per_second": (
            by_deployment(f"rate(llm_input_tokens_total[{rate}])"),
            MetricUnit.OPS,
        ),
        "completion_tokens_per_second": (
            by_deployment(f"rate(llm_output_tokens_total[{rate}])"),
            MetricUnit.OPS,
        ),
        "total_tokens_per_second": (
            by_deployment(f"rate(llm_total_tokens_total[{rate}])"),
            MetricUnit.OPS,
        ),
        "llm_latency_seconds": (
            f"({by_deployment(f'rate(llm_duration_seconds_sum[{rate}])')})"
            f" / ({by_deployment(f'rate(llm_duration_seconds_count[{rate}])')})",
            MetricUnit.SECONDS,
        ),
        "request_success_percent": (http_share("[23].."), MetricUnit.PERCENT),
        "request_error_percent": (http_share("[45].."), MetricUnit.PERCENT),
    }

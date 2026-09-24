"""
Prometheus Query Reference and Configuration for Grafana API (k8s Grafana).

Cluster model (docs/grafana-k8s-exploration.md):
  - Workloads are standard Kubernetes Deployments whose pods carry a ``tier``
    label (kube_deployment_labels exposes: namespace, deployment, label_tier).
  - Tier membership is determined by kube_deployment_labels{label_tier=~"..."}.
  - Pod → Deployment mapping: pod → ReplicaSet (kube_pod_owner) → Deployment
    (kube_replicaset_owner), joined via label_replace to share replicaset name.
  - GPU: ray_node_gpus_utilization (DCGM not available on this cluster).
    Each tier has a dedicated Ray cluster; GPU is a shared tier resource.

Per-tier label_tier value regex (Prometheus RE2), used for CPU / memory only:
  ATHENA_TIER1_LABEL_REGEX  (default: tier1)
  ATHENA_TIER2_LABEL_REGEX  (default: tier2)
  ATHENA_TIER3_LABEL_REGEX  (default: tier3|prod)

Per-tier Ray GPU cluster name (``ray_io_cluster`` label) for GPU PromQL
(``label_replace`` match value, not a regex selector):

  ATHENA_TIER1_GPU_CLUSTER  (default: qwen-5254d)
  ATHENA_TIER2_GPU_CLUSTER  (default: qwen-pq9sc)
  ATHENA_TIER3_GPU_CLUSTER  (default: qwen-l8dnl)

Panel-18 per-app GPU uses unfiltered ``kube_deployment_labels`` and
``llm_total_tokens_total`` so all apps contribute. Tier totals use a single
Prometheus query ``avg by (label_tier)(...)`` over Ray utilisation.

Unit normalisation (raw Prometheus values → 0-100% for UI/status thresholds):
  CPU: query returns cores/sec; divided by ATHENA_CPU_CORES_CAP (default 8).
  Memory: query returns GB; divided by ATHENA_MEMORY_GB_CAP (default 80).
  GPU: ray_node_gpus_utilization (0–1 per device); tier sums use 0–num_gpus scale in the UI.

Datasource UID:
  GRAFANA_PROMETHEUS_DATASOURCE_UID (default: prometheus)
"""

from __future__ import annotations

import os
from collections.abc import Callable

from c2ai.schemas.grafana import MetricType

# =============================================================================
# DATASOURCE CONFIGURATION
# =============================================================================

_DEFAULT_PROMETHEUS_DS_UID = "prometheus"


def get_grafana_prometheus_datasource() -> dict[str, str]:
    """Return Grafana datasource object for Prometheus queries."""
    uid = os.getenv("GRAFANA_PROMETHEUS_DATASOURCE_UID", _DEFAULT_PROMETHEUS_DS_UID)
    return {"type": "prometheus", "uid": uid}


# =============================================================================
# TIER CONFIGURATION
# =============================================================================

TIER_NAMES = ["Tier1", "Tier2", "Tier3"]
REF_IDS = ["A", "B", "C"]

TIER_MAP = {
    "A": "Tier 1",
    "B": "Tier 2",
    "C": "Tier 3",
}

# Maps UI tier name → ``label_tier`` value on tier-total GPU instant-query frames.
TIER_DISPLAY_NAME_TO_GPU_PROMQL_LABEL: dict[str, str] = {
    "Tier 1": "tier1",
    "Tier 2": "tier2",
    "Tier 3": "tier3",
}

TIER_CONFIG: dict[str, dict[str, int] | None] = {
    "Tier 1": {"gpus": 2},
    "Tier 2": {"gpus": 2},
    "Tier 3": {"gpus": 2},
    "Tier 4": None,
}

# Default label_tier values per Athena tier.
# "prod" is an alias for Tier 3 until the cluster labels are standardised.
_DEFAULT_TIER_LABEL_REGEX: dict[str, str] = {
    "Tier1": "tier1",
    "Tier2": "tier2",
    "Tier3": "tier3|prod",
}


def _tier_label_regex(tier_key: str) -> str:
    """
    Prometheus RE2 regex matching the kube_deployment_labels label_tier value
    for this logical tier.  Overridable via ATHENA_TIER{N}_LABEL_REGEX.
    """
    env_name = f"ATHENA_{tier_key.upper()}_LABEL_REGEX"
    default = _DEFAULT_TIER_LABEL_REGEX.get(tier_key, "^$")
    raw = os.getenv(env_name, default)
    return raw.strip() or "^$"


# Default ray_io_cluster name for the Ray GPU cluster in each tier.
# Overridable via ATHENA_TIER{N}_GPU_CLUSTER.
# Defaults align with Grafana k8s app deployments overview (Ray per tier).
_DEFAULT_TIER_GPU_CLUSTERS: dict[str, str] = {
    "Tier1": "qwen-5254d",
    "Tier2": "qwen-pq9sc",
    "Tier3": "qwen-l8dnl",
}


def _gpu_cluster_name(tier_key: str) -> str:
    """Ray cluster name (ray_io_cluster label) for GPU queries in this tier."""
    env_name = f"ATHENA_{tier_key.upper()}_GPU_CLUSTER"
    default = _DEFAULT_TIER_GPU_CLUSTERS.get(tier_key, "")
    return os.getenv(env_name, default).strip()


# =============================================================================
# UNIT NORMALISATION CAPS
# =============================================================================

def excluded_deployment_names() -> frozenset[str]:
    """
    Set of deployment names to exclude from the rendered instance list.

    Controlled via ATHENA_EXCLUDED_DEPLOYMENTS (comma-separated, case-insensitive).
    Default: nginx  (infrastructure sidecar, not a customer application).

    Example:
        ATHENA_EXCLUDED_DEPLOYMENTS=nginx,prometheus-adapter,kube-state-metrics
    """
    raw = os.getenv("ATHENA_EXCLUDED_DEPLOYMENTS", "nginx")
    return frozenset(name.strip().lower() for name in raw.split(",") if name.strip())


def _cpu_cores_cap() -> float:
    """Total CPU cores → 100% denominator. Override via ATHENA_CPU_CORES_CAP."""
    try:
        return max(1.0, float(os.getenv("ATHENA_CPU_CORES_CAP", "8")))
    except ValueError:
        return 8.0


def _memory_gb_cap() -> float:
    """Total memory GB → 100% denominator. Override via ATHENA_MEMORY_GB_CAP."""
    try:
        return max(1.0, float(os.getenv("ATHENA_MEMORY_GB_CAP", "80")))
    except ValueError:
        return 80.0


# =============================================================================
# PROMQL QUERY BUILDERS
# =============================================================================
#
# Pod → Deployment mapping pattern (used for CPU and memory):
#
#   rate(metric{...}[5m])
#   * on(namespace, pod) group_left(replicaset)
#     label_replace(kube_pod_owner{owner_kind="ReplicaSet"}, "replicaset", "$1", "owner_name", "(.*)")
#   * on(namespace, replicaset) group_left(deployment)
#     label_replace(kube_replicaset_owner{owner_kind="Deployment"}, "deployment", "$1", "owner_name", "(.*)")
#
# Then multiply by kube_deployment_labels{label_tier=~"..."} (value = 1) to
# filter to only the deployments whose Kubernetes tier label matches this tier.
# =============================================================================

def _pod_to_deployment_filter(tier_label_re: str) -> str:
    """
    PromQL suffix that:
      1. Maps pod → replicaset → deployment labels.
      2. Filters to deployments whose label_tier matches tier_label_re.
    Returns the two binary-join clauses to append after a pod-level metric.
    """
    return (
        '* on(namespace, pod) group_left(replicaset)'
        ' label_replace(kube_pod_owner{owner_kind="ReplicaSet"},'
        ' "replicaset", "$1", "owner_name", "(.*)")'
        ' * on(namespace, replicaset) group_left(deployment)'
        ' label_replace(kube_replicaset_owner{owner_kind="Deployment"},'
        ' "deployment", "$1", "owner_name", "(.*)")'
    )


def _tier_filter(tier_label_re: str) -> str:
    """kube_deployment_labels filter for this tier (value always 1 = acts as mask)."""
    return f'kube_deployment_labels{{label_tier=~"{tier_label_re}"}}'


def _k8s_cpu_by_deployment(tier_label_re: str) -> str:
    """CPU usage in cores/sec per (namespace, deployment), filtered by label_tier."""
    join = _pod_to_deployment_filter(tier_label_re)
    mask = _tier_filter(tier_label_re)
    return (
        f'sum by (namespace, deployment) ('
        f'  rate(container_cpu_usage_seconds_total{{'
        f'container!="",container!="POD"}}[5m])'
        f'  {join}'
        f') * on(namespace, deployment) group_left() {mask}'
    )


def _k8s_memory_by_deployment(tier_label_re: str) -> str:
    """Memory usage in GB per (namespace, deployment), filtered by label_tier."""
    join = _pod_to_deployment_filter(tier_label_re)
    mask = _tier_filter(tier_label_re)
    return (
        f'sum by (namespace, deployment) ('
        f'  container_memory_working_set_bytes{{'
        f'container!="",container!="POD"}}'
        f'  {join}'
        f') / 1073741824'
        f' * on(namespace, deployment) group_left() {mask}'
    )


def _tier_ray_gpu_avg_by_label_tier() -> str:
    """
    Per-tier Ray GPU util (avg by node series), keyed as label_tier (tier1–3).

    One unfiltered ``avg_over_time(ray_node_gpus_utilization[5m])`` per arm;
    ``label_replace`` maps each cluster's ``ray_io_cluster`` literal to a tier
    label. No regex selectors on metrics.
    """
    c1 = _gpu_cluster_name("Tier1")
    c2 = _gpu_cluster_name("Tier2")
    c3 = _gpu_cluster_name("Tier3")
    return (
        f'avg by (label_tier) ('
        f'  label_replace('
        f'    avg_over_time(ray_node_gpus_utilization[5m]),'
        f'    "label_tier", "tier1", "ray_io_cluster", "{c1}"'
        f'  )'
        f'  or label_replace('
        f'    avg_over_time(ray_node_gpus_utilization[5m]),'
        f'    "label_tier", "tier2", "ray_io_cluster", "{c2}"'
        f'  )'
        f'  or label_replace('
        f'    avg_over_time(ray_node_gpus_utilization[5m]),'
        f'    "label_tier", "tier3", "ray_io_cluster", "{c3}"'
        f'  )'
        f')'
    )


def _k8s_gpu_util() -> str:
    """Tier-total GPU: single Prometheus expression for all tiers (``label_tier`` dimension)."""
    return _tier_ray_gpu_avg_by_label_tier()


def _k8s_gpu_per_app() -> str:
    """
    Per-application GPU attribution (panel 18): token share × tier Ray GPU avg.

    No filtration on ``kube_deployment_labels`` or token counters — all apps
    with series in Prometheus participate.
    """
    tier_gpu = _tier_ray_gpu_avg_by_label_tier()
    return (
        f'('
        f'  sum by (namespace, label_tier) ('
        f'    sum by (namespace) (rate(llm_total_tokens_total[5m]))'
        f'    * on(namespace) group_left(label_tier)'
        f'      max by (namespace, label_tier) (kube_deployment_labels)'
        f'  )'
        f'  / on(label_tier) group_left()'
        f'  clamp_min('
        f'    sum by (label_tier) ('
        f'      sum by (namespace, label_tier) ('
        f'        sum by (namespace) (rate(llm_total_tokens_total[5m]))'
        f'        * on(namespace) group_left(label_tier)'
        f'          max by (namespace, label_tier) (kube_deployment_labels)'
        f'      )'
        f'    ),'
        f'    0.000001'
        f'  )'
        f')'
        f'* on(label_tier) group_left()'
        f'('
        f'  {tier_gpu}'
        f')'
    )


def _k8s_memory_total(tier_label_re: str) -> str:
    """Total working-set memory in GB for all tier-labelled deployments."""
    join = _pod_to_deployment_filter(tier_label_re)
    mask = _tier_filter(tier_label_re)
    return (
        f'sum('
        f'  sum by (namespace, deployment) ('
        f'    container_memory_working_set_bytes{{'
        f'container!="",container!="POD"}}'
        f'    {join}'
        f'  ) * on(namespace, deployment) group_left() {mask}'
        f') / 1073741824'
    )


def _k8s_cpu_total(tier_label_re: str) -> str:
    """Total CPU in cores/sec for all tier-labelled deployments."""
    join = _pod_to_deployment_filter(tier_label_re)
    mask = _tier_filter(tier_label_re)
    return (
        f'sum('
        f'  sum by (namespace, deployment) ('
        f'    rate(container_cpu_usage_seconds_total{{'
        f'container!="",container!="POD"}}[5m])'
        f'    {join}'
        f'  ) * on(namespace, deployment) group_left() {mask}'
        f')'
    )


def _tier_key_from_tier_param(tier: str) -> str:
    """Map the METRIC_QUERIES lambda argument (e.g. 'Tier1') to a tier key."""
    if tier in ("Tier1", "Tier2", "Tier3"):
        return tier
    for k in ("Tier1", "Tier2", "Tier3"):
        if tier.lower() == k.lower():
            return k
    return "Tier1"


METRIC_QUERIES: dict[MetricType, Callable[[str], str]] = {
    MetricType.CPU: lambda tier: _k8s_cpu_by_deployment(
        _tier_label_regex(_tier_key_from_tier_param(tier))
    ),
    MetricType.MEMORY: lambda tier: _k8s_memory_by_deployment(
        _tier_label_regex(_tier_key_from_tier_param(tier))
    ),
    MetricType.GPU: lambda _tier: _k8s_gpu_util(),
    MetricType.MEMORY_TOTAL: lambda tier: _k8s_memory_total(
        _tier_label_regex(_tier_key_from_tier_param(tier))
    ),
    MetricType.CPU_TOTAL: lambda tier: _k8s_cpu_total(
        _tier_label_regex(_tier_key_from_tier_param(tier))
    ),
}


def get_gpu_per_app_query() -> str:
    """Build the per-app GPU attribution query (panel 18) covering all tiers."""
    return _k8s_gpu_per_app()

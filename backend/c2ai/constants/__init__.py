"""
Constants package for Athena service.
"""

from c2ai.constants.prometheus import (
    METRIC_QUERIES,
    REF_IDS,
    TIER_CONFIG,
    TIER_MAP,
    TIER_NAMES,
    _cpu_cores_cap,
    _memory_gb_cap,
    excluded_deployment_names,
    get_grafana_prometheus_datasource,
)

__all__ = [
    "METRIC_QUERIES",
    "REF_IDS",
    "TIER_CONFIG",
    "TIER_MAP",
    "TIER_NAMES",
    "_cpu_cores_cap",
    "_memory_gb_cap",
    "excluded_deployment_names",
    "get_grafana_prometheus_datasource",
]

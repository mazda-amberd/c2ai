"""
Pydantic models for Grafana API requests and responses.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricType(str, Enum):
    """Types of metrics that can be fetched from Grafana."""

    CPU = "cpu"
    MEMORY = "memory"
    GPU = "gpu"
    MEMORY_TOTAL = "memory_total"
    CPU_TOTAL = "cpu_total"


class Status(str, Enum):
    """Health status of an instance based on resource utilization."""

    HEALTHY = "Healthy"
    WARNING = "Warning"
    CRITICAL = "Critical"


class Instance(BaseModel):
    """Represents a single application instance with its metrics.

    Attributes:
        id: Unique identifier for the instance
        name: Instance name (groupname from Prometheus)
        nodename: Node where the instance is running
        cpu: CPU usage as percentage of instance's allocated share
        memory: Memory usage as percentage of instance's allocated share
        gpu: GPU usage percentage (placeholder, to be implemented)
        status: Health status based on resource utilization
    """

    id: int
    name: str
    nodename: str
    client_name: str | None = None
    instance_name: str | None = None
    version: str | None = None
    cpu: float = 0.0
    memory: float = 0.0
    gpu: float = 0.0
    status: Status = Status.HEALTHY


class TierMetrics(BaseModel):
    """Metrics for a single tier."""

    instances: list[Instance] = Field(default_factory=list)


class TiersResponse(BaseModel):
    """Response containing metrics for all tiers."""

    tiers: dict[str, list[Instance] | None]
    tier_gpu_totals: dict[str, float | None] = Field(default_factory=dict)


# Grafana API Response Models


class GrafanaFieldLabels(BaseModel):
    """Labels attached to a Grafana field.

    Standard Deployment series (kube_pod_labels join) return:
      ``namespace`` + ``label_app``  (label_app == Kubernetes app label / deployment name)

    RayCluster series return:
      ``namespace`` + ``owner_name``

    GPU series (ray_node_gpus_utilization) return:
      ``namespace`` + ``ray_io_cluster``

    Unknown extra labels are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")

    namespace: str | None = None
    exported_namespace: str | None = None
    # Standard Kubernetes Deployment workloads (from RS→Deployment join)
    deployment: str | None = None
    label_app: str | None = None
    # RayCluster workloads (from kube_pod_owner join)
    owner_name: str | None = None
    # GPU series
    ray_io_cluster: str | None = None
    # Tier-total GPU instant query (avg by label_tier after label_replace)
    label_tier: str | None = None
    # LLM gateway usage dashboard groups token counters by caller namespace.
    source_namespace: str | None = None
    # Public-API token counters are additionally grouped by the upstream the
    # gateway routed to and the model it billed.
    provider: str | None = None
    model: str | None = None
    # Pod-IP lookup used to attribute LLM gateway counters to applications.
    pod: str | None = None
    pod_ip: str | None = None


class GrafanaField(BaseModel):
    """A field in a Grafana frame schema."""

    name: str
    type: str
    labels: GrafanaFieldLabels | None = None
    config: dict[str, Any] | None = None
    typeInfo: dict[str, Any] | None = None


class GrafanaFrameSchema(BaseModel):
    """Schema of a Grafana data frame."""

    refId: str
    fields: list[GrafanaField]
    meta: dict[str, Any] | None = None


class GrafanaFrameData(BaseModel):
    """Data values in a Grafana frame."""

    values: list[list[Any]]


class GrafanaFrame(BaseModel):
    """A single Grafana data frame containing schema and data."""

    model_config = {"populate_by_name": True}

    schema_: GrafanaFrameSchema = Field(alias="schema")
    data: GrafanaFrameData


class GrafanaQueryResult(BaseModel):
    """Result of a single Grafana query (by refId)."""

    status: int
    frames: list[GrafanaFrame] = Field(default_factory=list)


class GrafanaResponse(BaseModel):
    """Full response from Grafana API."""

    results: dict[str, GrafanaQueryResult]

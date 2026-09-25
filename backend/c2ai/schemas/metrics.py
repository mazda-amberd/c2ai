"""Response schemas for the level-based metrics API (``GET /api/v2/metrics``).

``metrics`` is a name-keyed map and ``scope.kind`` is a plain string, so new metrics and
new levels are additive — the frontend contract does not change shape when they land.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MetricLevel(str, Enum):
    """Aggregation level requested via ``?level=``."""

    CLUSTER = "cluster"
    TIER = "tier"
    APPLICATION = "application"


class MetricUnit(str, Enum):
    """Unit of a metric value; drives frontend formatting."""

    PERCENT = "percent"
    CORES = "cores"
    BYTES = "bytes"
    BYTES_PER_SECOND = "bytes_per_second"
    COUNT = "count"
    SECONDS = "seconds"
    OPS = "ops"
    CELSIUS = "celsius"
    WATTS = "watts"
    # Ray reports whole GPUs, not a percentage.
    GPUS = "gpus"


class MetricPoint(BaseModel):
    """One sample inside the requested window."""

    timestamp: int = Field(description="Milliseconds since the epoch.")
    value: float


class MetricValue(BaseModel):
    """One metric for one scope; ``value`` is null when Grafana did not return it."""

    value: float | None = None
    unit: MetricUnit = MetricUnit.PERCENT
    status: str | None = None
    available: bool = True
    points: list[MetricPoint] = Field(default_factory=list)


class Scope(BaseModel):
    """The entity the metrics describe; ``id`` is unique within a response."""

    kind: str
    id: str
    name: str
    tier: int | None = None
    subdomain: str | None = None
    client_name: str | None = None
    instance_name: str | None = None


class MetricSeries(BaseModel):
    """Metrics for a single scope."""

    scope: Scope
    metrics: dict[str, MetricValue] = Field(default_factory=dict)


class WindowOut(BaseModel):
    """The window the values were averaged over."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")
    preset: str | None = None
    step_seconds: int


class MetricsError(BaseModel):
    """One metric that could not be fetched; the rest of the payload is still valid."""

    metric: str
    message: str


class MetricsResponse(BaseModel):
    """Envelope returned for every level."""

    level: MetricLevel
    window: WindowOut
    generated_at: datetime
    refresh_after_seconds: int
    series: list[MetricSeries] = Field(default_factory=list)
    degraded: bool = False
    errors: list[MetricsError] = Field(default_factory=list)

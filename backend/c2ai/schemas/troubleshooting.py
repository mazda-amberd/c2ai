"""Request and response contracts for AI application troubleshooting."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from c2ai.schemas.deployment import DEPLOYMENT_LOG_NAME_RE, SUBDOMAIN_RE

TroubleshootingJobStatus = Literal[
    "gathering_data",
    "analyzing_data",
    "generating_recommendations",
    "completed",
    "failed",
]
TroubleshootingSeverity = Literal["critical", "warning", "normal"]


class TroubleshootingReportRequest(BaseModel):
    """Identify the deployed application to analyse."""

    subdomain: str = Field(min_length=3, max_length=63, pattern=SUBDOMAIN_RE.pattern)
    deployment: str = Field(
        min_length=1,
        max_length=253,
        pattern=DEPLOYMENT_LOG_NAME_RE.pattern,
    )
    tier: int | None = Field(default=None, ge=1, le=4)
    status: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=128)
    instance: str | None = Field(default=None, max_length=128)
    client: str | None = Field(default=None, max_length=128)

    @field_validator(
        "subdomain",
        "deployment",
        "status",
        "version",
        "instance",
        "client",
        mode="before",
    )
    @classmethod
    def strip_identifiers(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class TroubleshootingWindow(BaseModel):
    """The immutable time window used to produce a report."""

    start: datetime
    end: datetime
    hours: int = Field(ge=1, le=168)


class TroubleshootingEvent(BaseModel):
    """A source-backed event selected as evidence for the diagnosis."""

    id: str
    timestamp: datetime
    source: Literal["application", "kubernetes"]
    severity: Literal[
        "debug",
        "trace",
        "info",
        "warning",
        "error",
        "fatal",
        "critical",
    ]
    resource: str
    event_type: str
    reason: str | None = None
    message: str
    attributes: dict[str, str] = Field(default_factory=dict)


class TroubleshootingMetricPoint(BaseModel):
    """One Prometheus sample within the report's analysis window."""

    timestamp: datetime
    value: float


class TroubleshootingMetricQuery(BaseModel):
    """One trusted, application-scoped PromQL option supplied to the LLM."""

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=300)
    expr: str = Field(min_length=1, max_length=10_000)
    unit: str = Field(default="", max_length=32)
    value_divisor: float = Field(default=1.0, gt=0)


class TroubleshootingMetric(BaseModel):
    """One LLM-selected Grafana metric retrieved for the application report."""

    name: str
    label: str
    value: float | None = None
    unit: str = ""
    # No longer rendered: the UI and the PDF both draw ``points``. Kept so
    # the contract matches Athena's.
    plot_data_url: str | None = Field(default=None, max_length=100_000)
    points: list[TroubleshootingMetricPoint] = Field(
        default_factory=list,
        max_length=120,
    )


class TroubleshootingReportResponse(BaseModel):
    """Complete non-streaming troubleshooting report returned to the UI."""

    generated_at: datetime
    window: TroubleshootingWindow
    severity: TroubleshootingSeverity
    summary: str
    most_likely_root_cause: str
    issue_started: datetime | None = None
    most_critical_event: TroubleshootingEvent | None = None
    recommended_actions: list[str] = Field(default_factory=list)
    relevant_events: list[TroubleshootingEvent] = Field(default_factory=list)
    cluster_events: list[TroubleshootingEvent] = Field(
        default_factory=list,
        max_length=10,
    )
    application_metrics: list[TroubleshootingMetric] = Field(
        default_factory=list,
        max_length=4,
    )
    analyzed_log_lines: int = Field(ge=0, le=100)
    data_sources: list[Literal["application", "kubernetes"]] = Field(
        default_factory=list
    )


class TroubleshootingJobError(BaseModel):
    """Safe error details exposed when a job fails."""

    code: str
    message: str


class TroubleshootingJobResponse(BaseModel):
    """Polling representation for an asynchronous troubleshooting job."""

    job_id: str
    status: TroubleshootingJobStatus
    created_at: datetime
    updated_at: datetime
    report: TroubleshootingReportResponse | None = None
    error: TroubleshootingJobError | None = None


class TroubleshootingModelOutput(BaseModel):
    """Constrained model output; event details are hydrated by the backend."""

    severity: TroubleshootingSeverity
    summary: str = Field(min_length=1, max_length=4_000)
    most_likely_root_cause: str = Field(min_length=1, max_length=4_000)
    issue_started_event_id: str | None = None
    most_critical_event_id: str | None = None
    recommended_actions: list[str] = Field(min_length=4, max_length=4)
    recommended_metric_query_ids: list[str] = Field(
        default_factory=list,
        max_length=4,
    )
    relevant_event_ids: list[str] = Field(default_factory=list, max_length=20)
    relevant_cluster_event_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("summary", "most_likely_root_cause", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("recommended_actions", mode="before")
    @classmethod
    def normalize_actions(cls, value: object) -> object:
        """Trim actions before enforcing the exact four-item contract."""

        if not isinstance(value, list):
            return value
        return [
            action.strip()
            for action in value
            if isinstance(action, str) and action.strip()
        ][:4]

    @field_validator("recommended_metric_query_ids", mode="before")
    @classmethod
    def normalize_metric_query_ids(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        selected: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                continue
            query_id = raw.strip()
            if query_id and query_id not in seen:
                seen.add(query_id)
                selected.append(query_id)
            if len(selected) == 4:
                break
        return selected

    @field_validator("relevant_event_ids", mode="before")
    @classmethod
    def normalize_relevant_event_ids(cls, value: object) -> object:
        """Deduplicate and cap model-selected evidence instead of failing the job."""

        if not isinstance(value, list):
            return value
        selected: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                continue
            event_id = raw.strip()
            if event_id and event_id not in seen:
                seen.add(event_id)
                selected.append(event_id)
            if len(selected) == 20:
                break
        return selected

    @field_validator("relevant_cluster_event_ids", mode="before")
    @classmethod
    def normalize_relevant_cluster_event_ids(cls, value: object) -> object:
        """Deduplicate and cap model-selected Kubernetes event IDs."""

        if not isinstance(value, list):
            return value
        selected: list[str] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, str):
                continue
            event_id = raw.strip()
            if event_id and event_id not in seen:
                seen.add(event_id)
                selected.append(event_id)
            if len(selected) == 10:
                break
        return selected

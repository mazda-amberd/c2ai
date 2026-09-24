"""Integration boundary for operational data supplied by DevOps APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.schemas.troubleshooting import (
    TroubleshootingEvent,
    TroubleshootingMetric,
    TroubleshootingMetricQuery,
)
from c2ai.services.troubleshooting_grafana import (
    get_grafana_troubleshooting_data_provider,
    is_grafana_troubleshooting_configured,
)


class TroubleshootingDataProvider(Protocol):
    """Contract the troubleshooting backend expects from a data adapter."""

    async def collect_events(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[TroubleshootingEvent]:
        """Return normalized application and Kubernetes evidence."""

    async def collect_metrics(
        self,
        *,
        queries: list[TroubleshootingMetricQuery],
        subdomain: str,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetric]:
        """Execute trusted model-selected PromQL queries."""

    async def list_metric_queries(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
    ) -> list[TroubleshootingMetricQuery]:
        """Return trusted PromQL options present for the application window."""


class UnconfiguredTroubleshootingDataProvider:
    """Placeholder used until the DevOps retrieval APIs are connected."""

    async def collect_events(
        self,
        *,
        subdomain: str,
        deployment: str,
        tier: int | None,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[TroubleshootingEvent]:
        _ = (subdomain, deployment, tier, start, end, limit)
        raise ServiceUnavailableError(
            "Troubleshooting data retrieval is not configured yet.",
            code="TroubleshootingDataUnavailable",
        )


_unconfigured_provider = UnconfiguredTroubleshootingDataProvider()


def get_troubleshooting_data_provider() -> TroubleshootingDataProvider:
    """Dependency hook to replace when the DevOps APIs become available."""

    if is_grafana_troubleshooting_configured():
        return get_grafana_troubleshooting_data_provider()
    return _unconfigured_provider

"""Tests for Grafana-backed troubleshooting evidence collection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from c2ai.core.exceptions import ServiceUnavailableError
from c2ai.services.troubleshooting_grafana import (
    GrafanaTroubleshootingDataProvider,
    build_application_logs_query,
    build_cluster_events_query,
    build_namespace_events_query,
    build_system_events_query,
)


def _loki_payload(
    timestamp_ns: int,
    line: str,
    labels: dict[str, str],
) -> dict:
    return {
        "results": {
            "A": {
                "status": 200,
                "frames": [
                    {
                        "schema": {
                            "refId": "A",
                            "fields": [
                                {"name": "Time", "type": "time"},
                                {
                                    "name": "Line",
                                    "type": "string",
                                    "labels": {
                                        "namespace": labels.get("namespace", ""),
                                    },
                                },
                                {"name": "labels", "type": "other"},
                            ],
                        },
                        "data": {
                            "values": [[timestamp_ns], [line], [labels]],
                        },
                    }
                ],
            }
        }
    }


class FakeGrafanaClient:
    def __init__(self, *, fail_all: bool = False, fail_application: bool = False):
        self.fail_all = fail_all
        self.fail_application = fail_application
        self.calls: list[dict] = []

    async def query_loki_range(
        self,
        logql: str,
        start_ms: int,
        end_ms: int,
        max_lines: int | None = None,
        direction: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "logql": logql,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "max_lines": max_lines,
                "direction": direction,
            }
        )
        if self.fail_all:
            raise RuntimeError("Grafana unavailable")
        timestamp_ns = end_ms * 1_000_000 - len(self.calls)
        if 'app="my-app"' in logql:
            if self.fail_application:
                raise RuntimeError("Application logs unavailable")
            return _loki_payload(
                timestamp_ns,
                '{"level":"error","message":"database timeout"}',
                {
                    "namespace": "amberd-acme-ada",
                    "app": "my-app",
                    "pod": "my-app-abc",
                    "detected_level": "error",
                },
            )
        if 'namespace="amberd-acme-ada"' in logql:
            return _loki_payload(
                timestamp_ns,
                "Warning | BackOff | Pod/my-app-abc | container restart backoff",
                {"namespace": "amberd-acme-ada", "job": "kubernetes-event"},
            )
        if 'namespace="kube-system"' in logql:
            return _loki_payload(
                timestamp_ns,
                "Warning | FailedMount | Pod/coredns | ns=kube-system | volume unavailable",
                {"namespace": "kube-system", "job": "kubernetes-event"},
            )
        return _loki_payload(
            timestamp_ns,
            "Normal | NodeReady | Node/worker-1 | ns= | node is ready",
            {"job": "kubernetes-event"},
        )


def test_query_builders_match_dashboard_contract():
    assert build_application_logs_query('amberd-acme-"ada', "my-app") == (
        '{namespace="amberd-acme-\\"ada", app="my-app"}'
    )
    assert 'namespace="amberd-acme-ada"' in build_namespace_events_query(
        "amberd-acme-ada"
    )
    assert "kubernetes.*event|eventhandler" in build_namespace_events_query(
        "amberd-acme-ada"
    )
    assert 'kind=~"Node|PersistentVolume|VolumeAttachment|CSINode"' in (
        build_cluster_events_query()
    )
    assert 'namespace="kube-system"' in build_system_events_query()
    assert 'type="Warning"' in build_system_events_query()


@pytest.mark.asyncio
async def test_collects_and_normalizes_all_dashboard_evidence_sources():
    client = FakeGrafanaClient()
    provider = GrafanaTroubleshootingDataProvider(client=client)
    end = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    start = end - timedelta(hours=4)

    events = await provider.collect_events(
        subdomain="amberd-acme-ada",
        deployment="my-app",
        tier=1,
        start=start,
        end=end,
        limit=100,
    )

    assert len(client.calls) == 4
    assert [call["max_lines"] for call in client.calls] == [70, 20, 5, 5]
    assert all(call["direction"] == "backward" for call in client.calls)
    assert all(
        call["start_ms"] == int(start.timestamp() * 1000) for call in client.calls
    )
    assert all(call["end_ms"] == int(end.timestamp() * 1000) for call in client.calls)
    assert len(events) == 4
    assert {event.source for event in events} == {"application", "kubernetes"}

    application = next(event for event in events if event.source == "application")
    assert application.severity == "error"
    assert application.resource == "my-app-abc"
    assert application.message == "database timeout"

    backoff = next(event for event in events if event.reason == "BackOff")
    assert backoff.event_type == "Warning"
    assert backoff.severity == "warning"
    assert backoff.resource == "Pod/my-app-abc"
    assert backoff.attributes["grafana_dashboard_uid"] == "adwq7wr"


@pytest.mark.asyncio
async def test_uses_available_events_when_one_grafana_query_fails():
    provider = GrafanaTroubleshootingDataProvider(
        client=FakeGrafanaClient(fail_application=True)
    )
    end = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    events = await provider.collect_events(
        subdomain="amberd-acme-ada",
        deployment="my-app",
        tier=1,
        start=end - timedelta(hours=4),
        end=end,
        limit=100,
    )

    assert events
    assert all(event.source == "kubernetes" for event in events)


@pytest.mark.asyncio
async def test_fails_when_every_grafana_query_fails():
    provider = GrafanaTroubleshootingDataProvider(
        client=FakeGrafanaClient(fail_all=True)
    )
    end = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    with pytest.raises(ServiceUnavailableError) as raised:
        await provider.collect_events(
            subdomain="amberd-acme-ada",
            deployment="my-app",
            tier=1,
            start=end - timedelta(hours=4),
            end=end,
            limit=100,
        )

    assert raised.value.code == "TroubleshootingDataUnavailable"

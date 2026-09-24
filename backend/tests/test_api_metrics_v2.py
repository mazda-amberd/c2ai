"""Tests for the level-based metrics API (`GET /api/v2/metrics`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from c2ai.app import app
from c2ai.db.session import get_db_session
from c2ai.services.instance_metadata import InstanceMetadata
from c2ai.services.metrics import clear_cache

ENDPOINT = "/api/v2/metrics"
APPLICATION_ENDPOINT = "/api/v2/metrics/application"


def _frame(ref: str, value: float, **labels) -> dict:
    """One instant frame in Grafana's raw dataframe shape."""
    field = {"name": "Value", "type": "number"}
    if labels:
        field["labels"] = labels
    return {
        "schema": {"refId": ref, "fields": [{"name": "Time", "type": "time"}, field]},
        "data": {"values": [[1768233053154], [value]]},
    }


def _fake_grafana(value=1.0, per_app=None, fail_refs=(), empty=False, bodies=None,
                  only_refs=None):
    """Stub returning one frame per refId, or labelled frames at application level.

    ``only_refs`` limits which refIds return data, mimicking metrics no app emits.
    """

    async def _fetch(body):
        if bodies is not None:
            bodies.append(body)
        results = {}
        for query in body["queries"]:
            ref = query["refId"]
            if only_refs is not None and ref not in only_refs:
                results[ref] = {"status": 200, "frames": []}
            elif ref in fail_refs:
                results[ref] = {"status": 500, "error": "upstream exploded", "frames": []}
            elif empty:
                results[ref] = {"status": 200, "frames": []}
            elif per_app is not None:
                results[ref] = {
                    "status": 200,
                    "frames": [
                        _frame(ref, value, namespace=ns, deployment=dep)
                        for ns, dep in per_app
                    ],
                }
            else:
                results[ref] = {"status": 200, "frames": [_frame(ref, value)]}
        return {"results": results}

    client = AsyncMock()
    client.fetch_grafana_query_raw.side_effect = _fetch
    return client


@pytest.fixture
def metrics_client(deploy_auth_client):
    """Authenticated client with the DB dependency stubbed and the cache cleared."""

    async def _no_db():
        yield None

    app.dependency_overrides[get_db_session] = _no_db
    clear_cache()
    yield deploy_auth_client
    app.dependency_overrides.pop(get_db_session, None)
    clear_cache()


def _get(client, grafana, metadata=None, **params):
    with (
        patch("c2ai.api.metrics.get_metrics_grafana_client", return_value=grafana),
        patch(
            "c2ai.api.metrics.load_instance_metadata_map",
            new_callable=AsyncMock,
            return_value=metadata or {},
        ),
    ):
        return client.get(ENDPOINT, params=params)


def _get_application(client, grafana, metadata=None, **params):
    with (
        patch("c2ai.api.metrics.get_metrics_grafana_client", return_value=grafana),
        patch(
            "c2ai.api.metrics.load_instance_metadata_map",
            new_callable=AsyncMock,
            return_value=metadata or {},
        ),
    ):
        return client.get(APPLICATION_ENDPOINT, params=params)


def _metrics(body, index=0):
    return body["series"][index]["metrics"]


class TestValidation:
    def test_level_is_required(self, metrics_client):
        assert metrics_client.get(ENDPOINT).status_code == 422

    def test_unknown_level_rejected(self, metrics_client):
        assert metrics_client.get(ENDPOINT, params={"level": "galaxy"}).status_code == 422

    def test_unknown_range_rejected(self, metrics_client):
        response = _get(metrics_client, _fake_grafana(), level="cluster", range="99y")
        assert response.status_code == 422
        assert "Unknown range" in str(response.json()["detail"])

    def test_range_and_custom_window_are_mutually_exclusive(self, metrics_client):
        response = _get(
            metrics_client, _fake_grafana(), level="cluster", range="1h",
            **{"from": "2026-08-05T09:00:00Z", "to": "2026-08-05T12:00:00Z"},
        )
        assert response.status_code == 422
        assert "not both" in str(response.json()["detail"])

    def test_future_window_rejected(self, metrics_client):
        """A future window would otherwise return zeros that look like an idle cluster."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        response = _get(
            metrics_client, _fake_grafana(), level="cluster",
            **{"from": future.isoformat(), "to": (future + timedelta(hours=1)).isoformat()},
        )
        assert response.status_code == 422
        assert "future" in str(response.json()["detail"])

    def test_the_two_422_shapes_carry_different_codes(self, metrics_client):
        """The frontend switches on `code`, so both paths must stay distinguishable."""
        fastapi_error = metrics_client.get(ENDPOINT, params={"level": "cluster", "tier": 9})
        domain_error = _get(metrics_client, _fake_grafana(), level="cluster", range="99y")

        assert fastapi_error.json()["code"] == "RequestValidationError"
        assert isinstance(fastapi_error.json()["detail"], list)
        assert domain_error.json()["code"] == "ValidationFailed"
        assert isinstance(domain_error.json()["detail"], str)


class TestWindowIsSentToGrafana:
    def test_custom_window_drives_the_evaluation_time(self, metrics_client):
        """A custom from/to must be the Grafana eval range, not `now`."""
        start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
        bodies = []
        _get(
            metrics_client, _fake_grafana(bodies=bodies), level="cluster",
            **{"from": start.isoformat(), "to": end.isoformat()},
        )
        assert bodies
        assert bodies[0]["from"] == str(int(start.timestamp() * 1000))
        assert bodies[0]["to"] == str(int(end.timestamp() * 1000))

    def test_range_macro_is_substituted_from_the_window(self, metrics_client):
        """`$__range` becomes the window length; nothing may reach Grafana unsubstituted."""
        bodies = []
        _get(metrics_client, _fake_grafana(bodies=bodies), level="cluster", range="6h")
        exprs = [q["expr"] for q in bodies[0]["queries"]]

        assert not any("$__" in expr for expr in exprs)
        assert any("[21600s]" in expr for expr in exprs)

    def test_rate_interval_follows_grafana_formula(self, metrics_client):
        """max(step + scrape, 4 * scrape) — a shorter lookback drifts from the panel."""
        bodies = []
        _get(metrics_client, _fake_grafana(bodies=bodies), level="cluster", range="1h")
        exprs = " ".join(q["expr"] for q in bodies[0]["queries"])
        # step=60, scrape=60 -> max(120, 240) = 240
        assert "[240s]" in exprs

        bodies.clear()
        _get(metrics_client, _fake_grafana(bodies=bodies), level="cluster", range="24h")
        exprs = " ".join(q["expr"] for q in bodies[0]["queries"])
        # step=900, scrape=60 -> max(960, 240) = 960
        assert "[960s]" in exprs

    def test_all_queries_for_a_level_go_in_one_request(self, metrics_client):
        """Batching keeps a 13-metric level to a single upstream round-trip."""
        bodies = []
        _get(metrics_client, _fake_grafana(bodies=bodies), level="cluster", range="1h")
        assert len(bodies) == 1
        assert len(bodies[0]["queries"]) == 13


class TestClusterLevel:
    def test_returns_the_story_1_2_metric_set(self, metrics_client):
        response = _get(metrics_client, _fake_grafana(value=5.0), level="cluster")
        assert response.status_code == 200
        body = response.json()

        assert (body["level"], body["degraded"]) == ("cluster", False)
        assert len(body["series"]) == 1
        assert body["series"][0]["scope"]["id"] == "cluster"

        assert set(_metrics(body)) == {
            "nodes_ready", "nodes_not_ready", "pods_running", "pods_pending", "pods_failed",
            "pod_restarts", "network_receive_bps", "network_transmit_bps",
            "disk_read_bps", "disk_write_bps",
            "cpu_allocated_percent", "memory_allocated_percent", "pod_capacity_percent",
        }

    def test_units_match_the_panel_definitions(self, metrics_client):
        body = _get(metrics_client, _fake_grafana(), level="cluster").json()
        metrics = _metrics(body)
        assert metrics["nodes_ready"]["unit"] == "count"
        assert metrics["network_receive_bps"]["unit"] == "bytes_per_second"
        assert metrics["cpu_allocated_percent"]["unit"] == "percent"

    def test_tier_filter_is_ignored_for_cluster(self, metrics_client):
        body = _get(metrics_client, _fake_grafana(), level="cluster", tier=1).json()
        assert len(body["series"]) == 1


class TestTierLevel:
    def test_one_series_per_tier_namespace(self, metrics_client):
        body = _get(metrics_client, _fake_grafana(), level="tier").json()
        assert [s["scope"]["id"] for s in body["series"]] == [
            "tier-1", "tier-2", "tier-3", "tier-4"
        ]

    def test_returns_the_story_1_3_metric_set(self, metrics_client):
        body = _get(metrics_client, _fake_grafana(), level="tier", tier=1).json()
        assert set(_metrics(body)) == {
            "gpus_available", "gpus_allocated", "active_deployments",
            "gpu_utilization_percent", "gpu_temperature_celsius",
            "gpu_power_watts", "gpu_memory_percent",
        }

    def test_tier_filter_narrows_and_targets_the_right_namespace(self, metrics_client):
        bodies = []
        body = _get(metrics_client, _fake_grafana(bodies=bodies), level="tier", tier=3).json()
        assert len(body["series"]) == 1
        assert body["series"][0]["scope"]["tier"] == 3
        assert all('namespace=~"tier3"' in q["expr"] for q in bodies[0]["queries"])

    def test_gpu_units_are_not_percent(self, metrics_client):
        body = _get(metrics_client, _fake_grafana(), level="tier", tier=1).json()
        metrics = _metrics(body)
        assert metrics["gpus_allocated"]["unit"] == "gpus"
        assert metrics["gpu_temperature_celsius"]["unit"] == "celsius"
        assert metrics["gpu_power_watts"]["unit"] == "watts"


class TestApplicationLevel:
    def test_one_series_per_workload_in_absolute_units(self, metrics_client):
        grafana = _fake_grafana(value=2.5, per_app=[("amberd-acme-prod", "ada")])
        body = _get(metrics_client, grafana, level="application").json()

        assert len(body["series"]) == 1
        metrics = _metrics(body)
        assert metrics["cpu_cores"] == {
            "value": 2.5, "unit": "cores", "status": None, "available": True
        }
        assert metrics["memory_bytes"]["unit"] == "bytes"
        assert metrics["total_tokens_per_second"]["unit"] == "ops"
        assert metrics["uptime_seconds"]["unit"] == "seconds"

    def test_every_workload_carries_every_key(self, metrics_client):
        """An app emitting no LLM/HTTP metrics reports them unavailable, not absent."""
        grafana = _fake_grafana(per_app=[("amberd-acme-prod", "ada")], only_refs={"q0", "q4"})
        body = _get(metrics_client, grafana, level="application").json()

        metrics = _metrics(body)
        assert len(metrics) == 12
        assert metrics["health"]["available"] is True
        assert metrics["request_error_percent"] == {
            "value": None, "unit": "percent", "status": None, "available": False
        }

    def test_scope_id_is_unique_per_workload(self, metrics_client):
        """Two workloads in one namespace must not collide — the id keys the UI list."""
        grafana = _fake_grafana(
            per_app=[("amberd-acme-prod", "ada"), ("amberd-acme-prod", "worker")]
        )
        body = _get(metrics_client, grafana, level="application").json()

        ids = [s["scope"]["id"] for s in body["series"]]
        assert ids == ["amberd-acme-prod/ada", "amberd-acme-prod/worker"]
        assert {s["scope"]["subdomain"] for s in body["series"]} == {"amberd-acme-prod"}

    def test_scope_carries_client_and_instance_names(self, metrics_client):
        grafana = _fake_grafana(per_app=[("amberd-acme-prod", "ada")])
        body = _get(metrics_client, grafana, level="application").json()
        scope = body["series"][0]["scope"]
        assert (scope["client_name"], scope["instance_name"]) == ("acme", "prod")

    def test_db_metadata_wins_over_label_parsing(self, metrics_client):
        grafana = _fake_grafana(per_app=[("amberd-rljones-prod", "ada")])
        metadata = {
            "amberd-rljones-prod": InstanceMetadata(
                client_name="RL Jones", instance_name="Production"
            )
        }
        body = _get(metrics_client, grafana, metadata=metadata, level="application").json()
        scope = body["series"][0]["scope"]
        assert (scope["client_name"], scope["instance_name"]) == ("RL Jones", "Production")

    def test_excluded_deployments_are_dropped(self, metrics_client):
        grafana = _fake_grafana(
            per_app=[("amberd-acme-prod", "ada"), ("amberd-acme-prod", "nginx")]
        )
        body = _get(metrics_client, grafana, level="application").json()
        assert [s["scope"]["name"] for s in body["series"]] == ["ada"]


class TestSpecificApplicationEndpoint:
    def test_returns_only_the_requested_application(self, metrics_client):
        grafana = _fake_grafana(
            per_app=[("amberd-acme-prod", "ada"), ("amberd-acme-prod", "worker")]
        )
        body = _get_application(
            metrics_client,
            grafana,
            application="amberd-acme-prod/worker",
        ).json()

        assert [item["scope"]["id"] for item in body["series"]] == [
            "amberd-acme-prod/worker"
        ]

    def test_unknown_application_returns_empty_series(self, metrics_client):
        grafana = _fake_grafana(per_app=[("amberd-acme-prod", "ada")])
        response = _get_application(
            metrics_client,
            grafana,
            application="amberd-acme-prod/missing",
        )

        assert response.status_code == 200
        assert response.json()["series"] == []

    def test_application_filter_is_required(self, metrics_client):
        assert metrics_client.get(APPLICATION_ENDPOINT).status_code == 422

    def test_requires_authentication(self, test_client):
        response = test_client.get(
            APPLICATION_ENDPOINT,
            params={"application": "amberd-acme-prod/ada"},
        )
        assert response.status_code == 401


class TestFailureHandling:
    def test_one_failing_metric_degrades_but_still_returns_200(self, metrics_client):
        response = _get(metrics_client, _fake_grafana(fail_refs={"q0"}), level="cluster")

        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is True
        assert [e["metric"] for e in body["errors"]] == ["nodes_ready"]

    def test_a_failed_metric_keeps_its_key_as_unavailable(self, metrics_client):
        """Dropping the key would hide the failure from a frontend iterating metrics."""
        body = _get(metrics_client, _fake_grafana(fail_refs={"q0"}), level="cluster").json()
        assert _metrics(body)["nodes_ready"] == {
            "value": None, "unit": "count", "status": None, "available": False
        }

    def test_error_message_does_not_leak_upstream_detail(self, metrics_client):
        response = _get(metrics_client, _fake_grafana(fail_refs={"q0"}), level="cluster")
        assert "upstream exploded" not in response.text

    def test_empty_grafana_result_is_null_not_zero(self, metrics_client):
        """No data must not render as an idle cluster."""
        body = _get(metrics_client, _fake_grafana(empty=True), level="cluster").json()
        cpu = _metrics(body)["cpu_allocated_percent"]
        assert cpu["value"] is None
        assert cpu["available"] is False

    def test_transport_failure_returns_503(self, metrics_client):
        async def _always_fail(body):
            raise httpx.ConnectError("grafana down")

        grafana = AsyncMock()
        grafana.fetch_grafana_query_raw.side_effect = _always_fail

        response = _get(metrics_client, grafana, level="cluster")
        assert response.status_code == 503
        assert response.json()["code"] == "MetricsUnavailable"


class TestCaching:
    def test_second_call_is_served_from_cache(self, metrics_client):
        grafana = _fake_grafana()
        first = _get(metrics_client, grafana, level="cluster", range="1h")
        calls = grafana.fetch_grafana_query_raw.call_count
        second = _get(metrics_client, grafana, level="cluster", range="1h")

        assert grafana.fetch_grafana_query_raw.call_count == calls
        assert first.json()["generated_at"] == second.json()["generated_at"]

    def test_cluster_ignores_tier_in_the_cache_key(self, metrics_client):
        """?tier= doesn't change cluster output, so it must not cost a second fetch."""
        grafana = _fake_grafana()
        _get(metrics_client, grafana, level="cluster", range="1h")
        calls = grafana.fetch_grafana_query_raw.call_count

        _get(metrics_client, grafana, level="cluster", range="1h", tier=2)
        assert grafana.fetch_grafana_query_raw.call_count == calls

    def test_different_level_is_cached_separately(self, metrics_client):
        grafana = _fake_grafana()
        _get(metrics_client, grafana, level="cluster", range="1h")
        calls = grafana.fetch_grafana_query_raw.call_count

        _get(metrics_client, grafana, level="tier", range="1h")
        assert grafana.fetch_grafana_query_raw.call_count > calls

    def test_expired_entries_are_evicted_on_write(self, metrics_client):
        """Sliding custom windows must not grow the cache without bound."""
        from c2ai.services import metrics as metrics_service

        metrics_service._cache[("stale", "key", None)] = (0.0, None)
        _get(metrics_client, _fake_grafana(), level="cluster", range="1h")

        assert ("stale", "key", None) not in metrics_service._cache
        assert len(metrics_service._cache) == 1


class TestAuth:
    def test_requires_authentication(self, test_client):
        """No JWT override here — the endpoint must not be public."""
        assert test_client.get(ENDPOINT, params={"level": "cluster"}).status_code == 401

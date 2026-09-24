"""Tests for deployment logs API (Grafana Loki)."""

from __future__ import annotations

import httpx
import pytest

from c2ai.clients.grafana import GrafanaClient

LOKI_FIXTURE = {
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
                                "labels": {"namespace": "amberd-acme-ada"},
                            },
                            {"name": "labels", "type": "other"},
                        ],
                    },
                    "data": {
                        "values": [
                            [1_704_067_200_000_000_000],
                            ['{"level":"error","message":"boom"}'],
                            [{"pod": "my-pod-abc", "container": "worker"}],
                        ]
                    },
                }
            ],
        }
    }
}


@pytest.fixture
def loki_env(monkeypatch):
    monkeypatch.setenv("GRAFANA_API_URL", "https://grafana.example/api/ds/query")
    monkeypatch.setenv("GRAFANA_LOKI_DATASOURCE_UID", "loki")


@pytest.fixture
def mock_loki_query(monkeypatch, loki_env):
    async def _query(
        self: GrafanaClient,
        logql: str,
        start_ms: int,
        end_ms: int,
        max_lines: int | None = None,
        direction: str | None = None,
    ) -> dict:
        _ = max_lines
        _ = direction
        if start_ms < 946684800000:  # before 2000-01-01 UTC
            return {"results": {"A": {"status": 200, "frames": []}}}
        # Second page of forward-paged tests: cursor advances into this ms bucket → empty.
        if direction == "forward" and start_ms >= 1704067200001:
            return {"results": {"A": {"status": 200, "frames": []}}}
        if direction == "backward" and end_ms < 1704067200000:
            return {"results": {"A": {"status": 200, "frames": []}}}
        assert "amberd-acme-ada" in logql
        assert "my-app" in logql
        return LOKI_FIXTURE

    monkeypatch.setattr(GrafanaClient, "query_loki_range", _query)


class TestDeploymentLogs:
    def test_returns_loki_entries(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "tier": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "loki"
        assert len(data["entries"]) == 1
        row = data["entries"][0]
        assert row["level"] == "error"
        assert "boom" in row["message"]
        assert row["id"]
        assert row["labels"].get("namespace") == "amberd-acme-ada"
        assert row["labels"].get("pod") == "my-pod-abc"

    def test_pagination_forward_and_cursor(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        r1 = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2024-01-01T00:00:00Z",
                "to": "2024-01-02T00:00:00Z",
                "limit": 1,
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["has_more"] is True
        assert d1["next_cursor"]
        assert len(d1["entries"]) == 1

        r2 = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2024-01-01T00:00:00Z",
                "to": "2024-01-02T00:00:00Z",
                "limit": 1,
                "cursor": d1["next_cursor"],
            },
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["entries"] == []
        assert d2["has_more"] is False
        assert d2["next_cursor"] is None

    def test_limit_without_from_to_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "limit": 100,
            },
        )
        assert response.status_code == 422

    def test_tail_limit_without_from_to_ok(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "limit": 100,
                "tail": "true",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "loki"
        assert len(data["entries"]) >= 1

    def test_tail_pagination_cursor(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        r1 = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2024-01-01T00:00:00Z",
                "to": "2024-01-02T00:00:00Z",
                "limit": 1,
                "tail": "true",
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["has_more"] is True
        assert d1["next_cursor"]
        r2 = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2024-01-01T00:00:00Z",
                "to": "2024-01-02T00:00:00Z",
                "limit": 1,
                "tail": "true",
                "cursor": d1["next_cursor"],
            },
        )
        assert r2.status_code == 200
        assert r2.json()["entries"] == []

    def test_invalid_cursor_422(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2024-01-01T00:00:00Z",
                "to": "2024-01-02T00:00:00Z",
                "limit": 10,
                "cursor": "not-a-valid-cursor",
            },
        )
        assert response.status_code == 422

    def test_invalid_subdomain_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "INVALID", "deployment": "my-app"},
        )
        assert response.status_code == 422

    def test_missing_deployment_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "amberd-acme-ada"},
        )
        assert response.status_code == 422

    def test_invalid_deployment_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "amberd-acme-ada", "deployment": "bad name!"},
        )
        assert response.status_code == 422

    def test_tier_out_of_range_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "amberd-acme-ada", "deployment": "x", "tier": 9},
        )
        assert response.status_code == 422

    def test_from_to_historical_empty(self, deploy_auth_client, mock_loki_query):
        import c2ai.api.logs as logs_mod

        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "1999-06-01T00:00:00Z",
                "to": "1999-06-01T01:00:00Z",
            },
        )
        assert response.status_code == 200
        assert response.json()["entries"] == []

    def test_invalid_from_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "not-a-date",
                "to": "2020-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 422

    def test_from_after_to_422(self, deploy_auth_client, mock_loki_query):
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={
                "subdomain": "amberd-acme-ada",
                "deployment": "my-app",
                "from": "2020-01-02T00:00:00Z",
                "to": "2020-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 422

    def test_503_when_loki_uid_missing(self, deploy_auth_client, monkeypatch, loki_env):
        import c2ai.api.logs as logs_mod

        monkeypatch.delenv("GRAFANA_LOKI_DATASOURCE_UID", raising=False)
        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
        )
        assert response.status_code == 503

    def test_503_when_grafana_loki_uid_wrong(self, deploy_auth_client, monkeypatch, loki_env):
        """Grafana returns 404 Data source not found when UID does not exist."""
        import c2ai.api.logs as logs_mod

        req = httpx.Request("POST", "https://grafana.example/api/ds/query")
        resp = httpx.Response(
            404,
            json={"message": "Data source not found"},
            request=req,
        )

        async def _boom(
            self: GrafanaClient,
            logql: str,
            start_ms: int,
            end_ms: int,
            max_lines: int | None = None,
            direction: str | None = None,
        ) -> dict:
            raise httpx.HTTPStatusError("not found", request=req, response=resp)

        monkeypatch.setattr(GrafanaClient, "query_loki_range", _boom)
        logs_mod._grafana_client = None  # type: ignore[attr-defined]
        response = deploy_auth_client.get(
            "/api/logs/deployment",
            params={"subdomain": "amberd-acme-ada", "deployment": "my-app"},
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert isinstance(detail, str)
        assert "GRAFANA_LOKI_DATASOURCE_UID" in detail

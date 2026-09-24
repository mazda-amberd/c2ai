"""
Tests for the metrics API endpoints.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from c2ai.app import app
from c2ai.schemas.grafana import Instance, Status


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_check(self, test_client):
        """Test that health check returns ok status."""
        response = test_client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestMetricsEndpoint:
    """Tests for the /api/metrics endpoint."""

    def test_get_metrics_success(self, deploy_auth_client):
        """Test successful metrics retrieval — response includes tiers and tier_gpu_totals."""
        mock_tiers = {
            "Tier 1": [
                Instance(
                    id=1,
                    name="app1",
                    nodename="amberd-acme-ada",
                    cpu=10.0,
                    memory=500.0,
                    gpu=5.5,
                    status=Status.HEALTHY,
                )
            ],
            "Tier 2": [],
            "Tier 3": [],
            "Tier 4": None,
        }
        mock_gpu_totals = {"Tier 1": 8.9, "Tier 2": 0.0, "Tier 3": 0.0, "Tier 4": None}

        with (
            patch("c2ai.api.grafana.get_grafana_client") as mock_get_client,
            patch(
                "c2ai.api.grafana.replace_application_instances_for_tiers",
                new_callable=AsyncMock,
            ) as mock_persist,
            patch(
                "c2ai.clients.version.InstanceVersionClient.fetch_instance_version",
                new_callable=AsyncMock,
                return_value="26.02.abc1234",
            ),
        ):
            mock_client = AsyncMock()
            mock_client.get_all_metrics.return_value = (mock_tiers, mock_gpu_totals)
            mock_get_client.return_value = mock_client

            response = deploy_auth_client.get("/api/metrics")

            mock_persist.assert_awaited_once()

            assert response.status_code == 200
            data = response.json()
            assert "tiers" in data
            assert "tier_gpu_totals" in data
            assert "Tier 1" in data["tiers"]
            assert len(data["tiers"]["Tier 1"]) == 1
            assert data["tiers"]["Tier 1"][0]["name"] == "app1"
            assert data["tiers"]["Tier 1"][0]["client_name"] == "acme"
            assert data["tiers"]["Tier 1"][0]["instance_name"] == "ada"
            assert data["tiers"]["Tier 1"][0]["version"] == "26.02.abc1234"
            assert abs(data["tier_gpu_totals"]["Tier 1"] - 8.9) < 0.01

    def test_get_metrics_with_tier_filter(self, deploy_auth_client):
        """Test metrics retrieval with tier filter."""
        mock_tiers = {
            "Tier 2": [
                Instance(
                    id=1,
                    name="app2",
                    nodename="node2",
                    cpu=50.0,
                    memory=1000.0,
                    gpu=3.0,
                    status=Status.WARNING,
                )
            ],
        }
        mock_gpu_totals = {"Tier 2": 6.5}

        with (
            patch("c2ai.api.grafana.get_grafana_client") as mock_get_client,
            patch(
                "c2ai.api.grafana.replace_application_instances_for_tiers",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.clients.version.InstanceVersionClient.fetch_instance_version",
                new_callable=AsyncMock,
                return_value="26.02.abc1234",
            ),
        ):
            mock_client = AsyncMock()
            mock_client.get_all_metrics.return_value = (mock_tiers, mock_gpu_totals)
            mock_get_client.return_value = mock_client

            response = deploy_auth_client.get("/api/metrics?tier=2")

            assert response.status_code == 200
            mock_client.get_all_metrics.assert_called_once_with(tier=2)

    def test_get_metrics_version_lookup_failure_is_non_fatal(self, deploy_auth_client):
        """A failed per-instance version lookup should not fail the full metrics response."""
        mock_tiers = {
            "Tier 1": [
                Instance(
                    id=1,
                    name="app1",
                    nodename="node1",
                    cpu=10.0,
                    memory=500.0,
                    gpu=20.0,
                    status=Status.HEALTHY,
                ),
                Instance(
                    id=2,
                    name="app2",
                    nodename="node2",
                    cpu=20.0,
                    memory=600.0,
                    gpu=30.0,
                    status=Status.WARNING,
                ),
            ],
            "Tier 2": [],
            "Tier 3": [],
            "Tier 4": None,
        }

        async def version_side_effect(_client, subdomain, **_kwargs):
            if subdomain == "node1":
                return "26.02.abc1234"
            return None

        with (
            patch("c2ai.api.grafana.get_grafana_client") as mock_get_client,
            patch(
                "c2ai.api.grafana.replace_application_instances_for_tiers",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.clients.version.InstanceVersionClient.fetch_instance_version",
                new_callable=AsyncMock,
                side_effect=version_side_effect,
            ),
        ):
            mock_client = AsyncMock()
            mock_client.get_all_metrics.return_value = (
                mock_tiers,
                {"Tier 1": 0.0, "Tier 2": 0.0, "Tier 3": 0.0, "Tier 4": None},
            )
            mock_get_client.return_value = mock_client

            response = deploy_auth_client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["tiers"]["Tier 1"][0]["version"] == "26.02.abc1234"
            assert data["tiers"]["Tier 1"][1]["version"] is None

    def test_get_metrics_invalid_tier(self, deploy_auth_client):
        """Test that invalid tier values are rejected."""
        response = deploy_auth_client.get("/api/metrics?tier=5")
        
        assert response.status_code == 422  # Validation error

    def test_get_metrics_tier_zero(self, deploy_auth_client):
        """Test that tier=0 is rejected."""
        response = deploy_auth_client.get("/api/metrics?tier=0")
        
        assert response.status_code == 422  # Validation error

    def test_get_metrics_error_handling(self, deploy_auth_client):
        """Test error handling when Grafana API fails."""
        with (
            patch("c2ai.api.grafana.get_grafana_client") as mock_get_client,
            patch(
                "c2ai.api.grafana.replace_application_instances_for_tiers",
                new_callable=AsyncMock,
            ),
        ):
            mock_client = AsyncMock()
            mock_client.get_all_metrics.side_effect = Exception("Connection failed")
            mock_get_client.return_value = mock_client

            response = deploy_auth_client.get("/api/metrics")

            assert response.status_code == 500
            data = response.json()
            assert "detail" in data
            assert data["detail"]["error"] == "Grafana request failed"

    def test_tier_gpu_totals_null_for_tier4(self, deploy_auth_client):
        """Tier 4 has no GPU — tier_gpu_totals entry is null in JSON."""
        mock_tiers = {"Tier 1": [], "Tier 2": [], "Tier 3": [], "Tier 4": None}
        mock_gpu_totals = {"Tier 1": 0.0, "Tier 2": 0.0, "Tier 3": 0.0, "Tier 4": None}

        with (
            patch("c2ai.api.grafana.get_grafana_client") as mock_get_client,
            patch(
                "c2ai.api.grafana.replace_application_instances_for_tiers",
                new_callable=AsyncMock,
            ),
        ):
            mock_client = AsyncMock()
            mock_client.get_all_metrics.return_value = (mock_tiers, mock_gpu_totals)
            mock_get_client.return_value = mock_client

            response = deploy_auth_client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["tier_gpu_totals"]["Tier 4"] is None


# CORS tests removed - CORS middleware has been removed from the application

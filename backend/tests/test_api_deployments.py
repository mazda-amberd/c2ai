"""Tests for deployment/pipeline API routes (auth bypassed via deploy_auth_client)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from c2ai.schemas.deployment import PipelineStatusOut


# ---------------------------------------------------------------------------
# Fake ORM objects
# ---------------------------------------------------------------------------

class FakePipelineRun:
    """Stand-in for the PipelineRun ORM model."""

    def __init__(
        self,
        *,
        id: str = "aaaaaaaa-0000-0000-0000-000000000001",
        subdomain: str = "amberd-acme-ada",
        operation: str = "deploy",
        event_type: str = "ada-deploy.yaml",
        triggered_by: str = "test-user",
        run_id: int | None = None,
        tier: int | None = 1,
        branch: str | None = "main",
        dispatched_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.subdomain = subdomain
        self.operation = operation
        self.event_type = event_type
        self.triggered_by = triggered_by
        self.run_id = run_id
        self.tier = tier
        self.branch = branch
        self.dispatched_at = dispatched_at or datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.ended_at = ended_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subdomain": self.subdomain,
            "operation": self.operation,
            "event_type": self.event_type,
            "triggered_by": self.triggered_by,
            "run_id": self.run_id,
            "tier": self.tier,
            "branch": self.branch,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@pytest.fixture(autouse=True)
def release_mock():
    """A failed dispatch releases its run; never let that reach a real database."""

    with patch(
        "c2ai.api.deployments.crud_pipeline.mark_run_ended", new_callable=AsyncMock
    ) as mock:
        yield mock


@pytest.fixture
def deploy_body() -> dict:
    return {
        "branch": "main",
        "subdomain": "amberd-acme-ada",
        "customer_name": "acme",
        "domain": "amberd.ai",
        "env_instance": "ada",
        "tier": 1,
    }


# ---------------------------------------------------------------------------
# POST /api/deploy/move-tier
# ---------------------------------------------------------------------------

class TestMoveDeploymentToTier:
    """Coverage for ``POST /api/deploy/move-tier``."""

    def test_move_tier_creates_run_and_dispatches(self, deploy_auth_client):
        """
        Verify move-tier creates a pipeline run and dispatches the DevOps workflow.

        Args:
            deploy_auth_client: Authenticated test client fixture.

        Returns:
            None: This test asserts the response and dispatch behavior.
        """
        fake = FakePipelineRun(operation="migration", event_type="ada-move-to-tier.yaml", tier=2)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "c2ai.api.deployments._guard_instance_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_move_tier_workflow",
                new_callable=AsyncMock,
            ) as mock_dispatch,
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post(
                "/api/deploy/move-tier",
                json={"subdomain": "amberd-acme-ada", "tier": 2},
            )

        assert response.status_code == 201
        assert response.json()["operation"] == "migration"
        mock_dispatch.assert_awaited_once()
        assert mock_dispatch.await_args.kwargs["tier"] == 2

    def test_move_tier_409_when_active_run(self, deploy_auth_client):
        """
        Verify move-tier is rejected when another operation is already active.

        Args:
            deploy_auth_client: Authenticated test client fixture.

        Returns:
            None: This test asserts the conflict response.
        """
        active = FakePipelineRun(operation="update")
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
            new_callable=AsyncMock,
            return_value=active,
        ):
            response = deploy_auth_client.post(
                "/api/deploy/move-tier",
                json={"subdomain": "amberd-acme-ada", "tier": 2},
            )
        assert response.status_code == 409

    def test_move_tier_503_when_pat_missing(self, deploy_auth_client, monkeypatch):
        """
        Verify move-tier returns a service error when GitHub credentials are missing.

        Args:
            deploy_auth_client: Authenticated test client fixture.
            monkeypatch: Pytest monkeypatch fixture for environment setup.

        Returns:
            None: This test asserts the service-unavailable response.
        """
        monkeypatch.delenv("GITHUB_PAT", raising=False)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=FakePipelineRun(operation="migration", event_type="ada-move-to-tier.yaml"),
            ),
        ):
            response = deploy_auth_client.post(
                "/api/deploy/move-tier",
                json={"subdomain": "amberd-acme-ada", "tier": 2},
            )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/deploy
# ---------------------------------------------------------------------------

class TestTriggerDeployment:
    def test_trigger_creates_run_and_dispatches(self, deploy_auth_client, deploy_body):
        fake = FakePipelineRun(operation="deploy", tier=1, branch="main")

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "c2ai.api.deployments._guard_instance_not_running",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments._guard_branch_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_workflow",
                new_callable=AsyncMock,
            ) as mock_dispatch,
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post("/api/deploy", json=deploy_body)

        assert response.status_code == 201
        data = response.json()
        assert data["subdomain"] == "amberd-acme-ada"
        assert data["operation"] == "deploy"
        assert data["triggered_by"] == "test-user"
        mock_dispatch.assert_awaited_once()

    def test_trigger_409_when_active_run_exists(self, deploy_auth_client, deploy_body):
        active = FakePipelineRun(operation="deploy")
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
            new_callable=AsyncMock,
            return_value=active,
        ):
            response = deploy_auth_client.post("/api/deploy", json=deploy_body)

        assert response.status_code == 409

    def test_trigger_503_when_github_pat_missing(
        self, deploy_auth_client, deploy_body, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_PAT", raising=False)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "c2ai.api.deployments._guard_instance_not_running",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments._guard_branch_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=FakePipelineRun(),
            ),
        ):
            response = deploy_auth_client.post("/api/deploy", json=deploy_body)

        assert response.status_code == 503
        assert "GITHUB_PAT" in response.json()["detail"]

    def test_trigger_422_invalid_subdomain(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "INVALID_SUBDOMAIN!!!"
        response = deploy_auth_client.post("/api/deploy", json=deploy_body)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/deploy/update
# ---------------------------------------------------------------------------

class TestTriggerDeploymentUpdate:
    def test_update_creates_run_and_dispatches(self, deploy_auth_client, deploy_body):
        fake = FakePipelineRun(operation="update")

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "c2ai.api.deployments._guard_instance_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments._guard_branch_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_update_workflow",
                new_callable=AsyncMock,
            ) as mock_dispatch,
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post("/api/deploy/update", json=deploy_body)

        assert response.status_code == 201
        assert response.json()["operation"] == "update"
        mock_dispatch.assert_awaited_once()

    def test_update_503_when_pat_missing(
        self, deploy_auth_client, deploy_body, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_PAT", raising=False)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_exists", new_callable=AsyncMock),
            patch("c2ai.api.deployments._guard_branch_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=FakePipelineRun(operation="update"),
            ),
        ):
            response = deploy_auth_client.post("/api/deploy/update", json=deploy_body)

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/deploy/terminate
# ---------------------------------------------------------------------------

class TestTerminateDeployment:
    def test_terminate_creates_run_and_dispatches(self, deploy_auth_client):
        fake = FakePipelineRun(operation="terminate")

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "c2ai.api.deployments._guard_instance_exists",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_terminate_workflow",
                new_callable=AsyncMock,
            ) as mock_term,
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post(
                "/api/deploy/terminate",
                json={"subdomain": "amberd-acme-ada"},
            )

        assert response.status_code == 201
        assert response.json()["operation"] == "terminate"
        mock_term.assert_awaited_once()

    def test_terminate_409_when_active_run(self, deploy_auth_client):
        active = FakePipelineRun(operation="terminate")
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
            new_callable=AsyncMock,
            return_value=active,
        ):
            response = deploy_auth_client.post(
                "/api/deploy/terminate",
                json={"subdomain": "amberd-acme-ada"},
            )
        assert response.status_code == 409

    def test_terminate_503_when_pat_missing(self, deploy_auth_client, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=FakePipelineRun(operation="terminate"),
            ),
        ):
            response = deploy_auth_client.post(
                "/api/deploy/terminate",
                json={"subdomain": "amberd-acme-ada"},
            )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/pipeline/active
# ---------------------------------------------------------------------------

class TestListActivePipelines:
    def test_returns_active_runs(self, deploy_auth_client):
        runs = [
            FakePipelineRun(subdomain="amberd-a-ada", operation="deploy"),
            FakePipelineRun(
                id="bbbbbbbb-0000-0000-0000-000000000002",
                subdomain="amberd-b-ada",
                operation="update",
            ),
        ]

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_all_active_runs",
                new_callable=AsyncMock,
                return_value=runs,
            ),
            patch(
                "c2ai.api.deployments._writeback_ended_if_complete",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.get_run_status_cached",
                new_callable=AsyncMock,
                return_value={"gh_status": "in_progress", "gh_conclusion": None},
            ),
            patch(
                "c2ai.api.deployments.list_active_registered_pipeline_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = deploy_auth_client.get("/api/pipeline/active")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["operation"] == "deploy"
        assert body[1]["operation"] == "update"

    def test_includes_registered_application_deployments(self, deploy_auth_client):
        """Registered deployments live in another table but share this contract."""
        registered = PipelineStatusOut(
            id="cccccccc-0000-0000-0000-000000000003",
            subdomain="amberd-acme-ada",
            operation="deploy",
            event_type="ada-deploy.yaml",
            triggered_by="test-user",
            tier=2,
            gh_status="in_progress",
            active_job="Deploy",
            current_step="Applying resources",
        )

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_all_active_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "c2ai.api.deployments.list_active_registered_pipeline_statuses",
                new_callable=AsyncMock,
                return_value=[registered],
            ),
        ):
            response = deploy_auth_client.get("/api/pipeline/active")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == "cccccccc-0000-0000-0000-000000000003"
        assert body[0]["current_step"] == "Applying resources"

    @pytest.mark.parametrize("operation", ["deploy", "update", "terminate"])
    def test_removes_successfully_completed_direct_actions(
        self,
        deploy_auth_client,
        operation,
    ):
        run = FakePipelineRun(operation=operation, run_id=12345)
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_all_active_runs",
                new_callable=AsyncMock,
                return_value=[run],
            ),
            patch(
                "c2ai.api.deployments._writeback_ended_if_complete",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.get_run_status_cached",
                new_callable=AsyncMock,
                return_value={
                    "gh_status": "completed",
                    "gh_conclusion": "success",
                },
            ),
            patch(
                "c2ai.api.deployments.list_active_registered_pipeline_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            response = deploy_auth_client.get("/api/pipeline/active")

        assert response.status_code == 200
        assert response.json() == []

    def test_removes_successfully_completed_registered_actions(
        self,
        deploy_auth_client,
    ):
        registered = PipelineStatusOut(
            id="cccccccc-0000-0000-0000-000000000003",
            subdomain="containerized-test-tier-1",
            operation="update",
            event_type="container-upgrade",
            triggered_by="test-user",
            tier=1,
            gh_status="completed",
            gh_conclusion="success",
        )
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_all_active_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "c2ai.api.deployments.list_active_registered_pipeline_statuses",
                new_callable=AsyncMock,
                return_value=[registered],
            ),
        ):
            response = deploy_auth_client.get("/api/pipeline/active")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# GET /api/pipeline/status
# ---------------------------------------------------------------------------

class TestGetPipelineStatus:
    def test_returns_status_with_gh_data(self, deploy_auth_client):
        fake = FakePipelineRun(run_id=12345)

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_latest_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments._writeback_ended_if_complete",
                new_callable=AsyncMock,
            ),
            patch(
                "c2ai.api.deployments.get_run_status_cached",
                new_callable=AsyncMock,
                return_value={
                    "gh_status": "in_progress",
                    "gh_conclusion": None,
                    "run_url": "https://github.com/Inferaim/devops/actions/runs/12345",
                    "active_job": "deploy",
                    "current_step": "Helm deploy",
                },
            ),
        ):
            response = deploy_auth_client.get(
                "/api/pipeline/status?subdomain=amberd-acme-ada"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["gh_status"] == "in_progress"
        assert data["active_job"] == "deploy"
        assert "run_url" in data

    def test_returns_null_when_no_runs(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_latest_run_for_subdomain",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = deploy_auth_client.get(
                "/api/pipeline/status?subdomain=amberd-nonexistent"
            )
        assert response.status_code == 200
        assert response.json() is None


# ---------------------------------------------------------------------------
# GET /api/github/branches
# ---------------------------------------------------------------------------

class TestGithubBranchesEndpoint:
    def test_lists_branches(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.list_repo_branches",
            new_callable=AsyncMock,
            return_value=["main", "develop"],
        ) as mock_list:
            response = deploy_auth_client.get(
                "/api/github/branches",
                params={"repo": "devops"},
            )

        assert response.status_code == 200
        assert response.json() == ["main", "develop"]
        mock_list.assert_awaited_once_with("amberd-ai", "devops")

    def test_branches_503_when_pat_missing(self, deploy_auth_client, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        response = deploy_auth_client.get(
            "/api/github/branches",
            params={"repo": "devops"},
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/github/tags
# ---------------------------------------------------------------------------

class TestGithubTagsEndpoint:
    def test_lists_tags(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.list_repo_tags",
            new_callable=AsyncMock,
            return_value=["v1.0.0", "v1.1.0"],
        ) as mock_list:
            response = deploy_auth_client.get(
                "/api/github/tags",
                params={"repo": "dealership_new"},
            )

        assert response.status_code == 200
        assert response.json() == ["v1.0.0", "v1.1.0"]
        mock_list.assert_awaited_once_with("amberd-ai", "dealership_new")

    def test_tags_503_when_pat_missing(self, deploy_auth_client, monkeypatch):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        response = deploy_auth_client.get(
            "/api/github/tags",
            params={"repo": "dealership_new"},
        )
        assert response.status_code == 503

    def test_tags_invalid_repo_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.get("/api/github/tags?repo=../../etc/passwd")
        assert response.status_code == 422

    def test_tags_valid_repo_passes(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.list_repo_tags",
            new_callable=AsyncMock,
            return_value=["v1.0.0"],
        ):
            response = deploy_auth_client.get("/api/github/tags?repo=dealership_new")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Input validation — POST body fields (DeployRequest / TerminateRequest)
# ---------------------------------------------------------------------------

class TestDeployRequestValidation:
    """422 responses for every invalid body field."""

    def test_invalid_subdomain_uppercase(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "INVALID_SUBDOMAIN!!!"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_subdomain_leading_hyphen(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "-amberd-acme"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_subdomain_trailing_hyphen(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "amberd-acme-"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_subdomain_too_short(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "ab"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_subdomain_too_long(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "a" * 64
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_branch_control_char(self, deploy_auth_client, deploy_body):
        deploy_body["branch"] = "main\x00evil"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_branch_whitespace_only(self, deploy_auth_client, deploy_body):
        deploy_body["branch"] = "   "
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_branch_too_long(self, deploy_auth_client, deploy_body):
        deploy_body["branch"] = "x" * 256
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_customer_name_too_long(self, deploy_auth_client, deploy_body):
        deploy_body["customer_name"] = "a" * 57
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_customer_name_empty_after_strip(self, deploy_auth_client, deploy_body):
        deploy_body["customer_name"] = "   "
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_domain_no_dot(self, deploy_auth_client, deploy_body):
        deploy_body["domain"] = "nodot"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_domain_trailing_dot(self, deploy_auth_client, deploy_body):
        deploy_body["domain"] = "amberd.ai."
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_env_instance_uppercase_chars(self, deploy_auth_client, deploy_body):
        # env_instance is lowercased internally; "ADA" is valid, "@#$" is not
        deploy_body["env_instance"] = "@#$"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_env_instance_empty(self, deploy_auth_client, deploy_body):
        deploy_body["env_instance"] = ""
        deploy_body["subdomain"] = "amberd-acme-ada"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_invalid_env_instance_whitespace_only(self, deploy_auth_client, deploy_body):
        deploy_body["env_instance"] = "   "
        deploy_body["subdomain"] = "amberd-acme-ada"
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_env_instance_uppercase_accepted(self, deploy_body):
        # Validate at schema level: "ADA" is valid (validator accepts it after lowercasing)
        from c2ai.schemas.deployment import DeployRequest
        deploy_body["env_instance"] = "ADA"
        req = DeployRequest(**deploy_body)
        assert req.env_instance == "ADA"


class TestDeploySubdomainCustomerConsistency:
    """POST /api/deploy rejects subdomain that doesn't match workflow_prepare_subdomain(customer, env)."""

    def test_mismatch_returns_422(self, deploy_auth_client, deploy_body):
        deploy_body["subdomain"] = "amberd-acme"
        deploy_body["customer_name"] = "other-customer"
        response = deploy_auth_client.post("/api/deploy", json=deploy_body)
        assert response.status_code == 422

    def test_sanitised_match_is_accepted(self, deploy_auth_client, deploy_body):
        # "acme" + "ada" → workflow_prepare_subdomain → "amberd-acme-ada" which matches
        fake = FakePipelineRun(operation="deploy", tier=1, branch="main")
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_not_running", new_callable=AsyncMock),
            patch("c2ai.api.deployments._guard_branch_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch("c2ai.api.deployments.dispatch_github_workflow", new_callable=AsyncMock),
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post("/api/deploy", json=deploy_body)
        assert response.status_code == 201

    def test_update_does_not_enforce_consistency(self, deploy_auth_client, deploy_body):
        # /api/deploy/update uses the same DeployRequest but must NOT enforce the equality guard
        deploy_body["subdomain"] = "amberd-acme"
        deploy_body["customer_name"] = "other-customer"
        fake = FakePipelineRun(operation="update")
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_exists", new_callable=AsyncMock),
            patch("c2ai.api.deployments._guard_branch_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=fake,
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_update_workflow",
                new_callable=AsyncMock,
            ),
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post("/api/deploy/update", json=deploy_body)
        assert response.status_code == 201


class TestCancelPipeline:
    def test_cancel_calls_github_and_marks_ended(self, deploy_auth_client):
        fake = FakePipelineRun(run_id=99_888, triggered_by="test-user")
        ended = FakePipelineRun(
            id=fake.id,
            run_id=99_888,
            triggered_by="test-user",
            ended_at=datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc),
        )

        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_pipeline_run_by_id",
                new_callable=AsyncMock,
                side_effect=[fake, ended],
            ),
            patch(
                "c2ai.api.deployments.cancel_workflow_run",
                new_callable=AsyncMock,
            ) as mock_cancel,
            patch(
                "c2ai.api.deployments.crud_pipeline.mark_run_ended",
                new_callable=AsyncMock,
            ),
        ):
            response = deploy_auth_client.post(
                "/api/pipeline/cancel",
                json={"pipeline_run_id": fake.id},
            )
        assert response.status_code == 200
        mock_cancel.assert_called_once_with(99_888)
        assert response.json()["ended_at"] is not None

    def test_cancel_wrong_user_403(self, deploy_auth_client):
        fake = FakePipelineRun(run_id=1, triggered_by="someone-else")
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_pipeline_run_by_id",
            new_callable=AsyncMock,
            return_value=fake,
        ):
            response = deploy_auth_client.post(
                "/api/pipeline/cancel",
                json={"pipeline_run_id": fake.id},
            )
        assert response.status_code == 403

    def test_cancel_no_run_id_409(self, deploy_auth_client):
        fake = FakePipelineRun(run_id=None, triggered_by="test-user")
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_pipeline_run_by_id",
            new_callable=AsyncMock,
            return_value=fake,
        ):
            response = deploy_auth_client.post(
                "/api/pipeline/cancel",
                json={"pipeline_run_id": fake.id},
            )
        assert response.status_code == 409

    def test_cancel_not_found(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_pipeline_run_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = deploy_auth_client.post(
                "/api/pipeline/cancel",
                json={"pipeline_run_id": "aaaaaaaa-0000-0000-0000-000000009999"},
            )
        assert response.status_code == 404


class TestTerminateRequestValidation:
    def test_invalid_subdomain_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.post(
            "/api/deploy/terminate", json={"subdomain": "INVALID!!!"}
        )
        assert response.status_code == 422

    def test_empty_subdomain_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.post(
            "/api/deploy/terminate", json={"subdomain": ""}
        )
        assert response.status_code == 422


class TestMoveTierRequestValidation:
    """Validation coverage for ``MoveTierRequest`` and its API route."""

    def test_invalid_subdomain_returns_422(self, deploy_auth_client):
        """
        Verify move-tier rejects invalid subdomain values.

        Args:
            deploy_auth_client: Authenticated test client fixture.

        Returns:
            None: This test asserts the validation response.
        """
        response = deploy_auth_client.post(
            "/api/deploy/move-tier",
            json={"subdomain": "INVALID!!!", "tier": 2},
        )
        assert response.status_code == 422

    def test_invalid_tier_returns_422(self, deploy_auth_client):
        """
        Verify move-tier rejects unsupported numeric tier values.

        Args:
            deploy_auth_client: Authenticated test client fixture.

        Returns:
            None: This test asserts the validation response.
        """
        response = deploy_auth_client.post(
            "/api/deploy/move-tier",
            json={"subdomain": "amberd-acme-ada", "tier": 4},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Input validation — GET query parameters
# ---------------------------------------------------------------------------

class TestQueryParamValidation:
    def test_status_invalid_subdomain_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.get(
            "/api/pipeline/status?subdomain=INVALID!!!"
        )
        assert response.status_code == 422

    def test_status_subdomain_leading_hyphen_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.get(
            "/api/pipeline/status?subdomain=-amberd-acme"
        )
        assert response.status_code == 422

    def test_history_invalid_subdomain_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.get(
            "/api/pipeline/history?subdomain=bad_subdomain"
        )
        assert response.status_code == 422

    def test_history_valid_subdomain_passes(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.crud_pipeline.get_runs_for_subdomain",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = deploy_auth_client.get(
                "/api/pipeline/history?subdomain=amberd-acme-ada"
            )
        assert response.status_code == 200

    def test_branches_invalid_repo_returns_422(self, deploy_auth_client):
        response = deploy_auth_client.get(
            "/api/github/branches?repo=../../etc/passwd"
        )
        assert response.status_code == 422

    def test_branches_valid_repo_passes(self, deploy_auth_client):
        with patch(
            "c2ai.api.deployments.list_repo_branches",
            new_callable=AsyncMock,
            return_value=["main"],
        ):
            response = deploy_auth_client.get(
                "/api/github/branches?repo=dealership_new"
            )
        assert response.status_code == 200


class TestDispatchFailureReleasesRun:
    """A committed run whose dispatch fails must not block the subdomain."""

    def test_failed_deploy_dispatch_ends_the_run(
        self, deploy_auth_client, deploy_body, release_mock, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        run = FakePipelineRun(id="run-to-release")
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_not_running", new_callable=AsyncMock),
            patch("c2ai.api.deployments._guard_branch_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=run,
            ) as create_mock,
            patch("c2ai.api.deployments._schedule_resolve") as schedule_mock,
        ):
            response = deploy_auth_client.post("/api/deploy", json=deploy_body)

        assert response.status_code == 503
        assert release_mock.await_args.args[1] == "run-to-release"
        schedule_mock.assert_not_called()
        # The deployment request metadata is recorded with the run.
        assert create_mock.await_args.kwargs["deployment_metadata"] == {
            "customer_name": deploy_body["customer_name"],
            "env_instance": deploy_body["env_instance"],
            "domain": deploy_body["domain"],
        }

    def test_successful_dispatch_does_not_end_the_run(
        self, deploy_auth_client, release_mock
    ):
        with (
            patch(
                "c2ai.api.deployments.crud_pipeline.get_active_run_for_subdomain",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("c2ai.api.deployments._guard_instance_exists", new_callable=AsyncMock),
            patch(
                "c2ai.api.deployments.crud_pipeline.create_pipeline_run",
                new_callable=AsyncMock,
                return_value=FakePipelineRun(operation="terminate"),
            ),
            patch(
                "c2ai.api.deployments.dispatch_github_terminate_workflow",
                new_callable=AsyncMock,
            ),
            patch("c2ai.api.deployments._schedule_resolve", side_effect=lambda c: c.close()),
        ):
            response = deploy_auth_client.post(
                "/api/deploy/terminate", json={"subdomain": "amberd-acme-ada"}
            )

        assert response.status_code == 201
        release_mock.assert_not_awaited()


async def test_background_tasks_are_retained_until_finished():
    import asyncio

    from c2ai.core import background

    gate = asyncio.Event()

    async def work():
        await gate.wait()

    task = background.spawn(work())
    assert task in background._tasks  # strong reference held while running
    gate.set()
    await task
    await asyncio.sleep(0)
    assert task not in background._tasks

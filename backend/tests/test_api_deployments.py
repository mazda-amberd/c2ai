"""Request validation and GitHub ref listing for the tier pages' deployment API.

The operations themselves (deploy, update, move-tier, terminate, cancel,
status) run against PostgreSQL in tests/integration/test_deployments_db.py.
"""

from __future__ import annotations

import httpx
import pytest

from c2ai.schemas.deployment import DeployRequest


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


@pytest.fixture
def github_pat(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")


class TestDeployRequestValidation:
    """422 responses for every invalid body field."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("subdomain", "INVALID_SUBDOMAIN!!!"),
            ("subdomain", "-amberd-acme"),
            ("subdomain", "amberd-acme-"),
            ("subdomain", "ab"),
            ("subdomain", "a" * 64),
            ("branch", "main\x00evil"),
            ("branch", "   "),
            ("branch", "x" * 256),
            ("customer_name", "a" * 57),
            ("customer_name", "   "),
            ("domain", "nodot"),
            ("domain", "amberd.ai."),
            ("env_instance", "@#$"),
            ("env_instance", ""),
            ("env_instance", "   "),
            ("tier", 5),
        ],
    )
    def test_invalid_field_is_rejected(self, deploy_auth_client, deploy_body, field, value):
        deploy_body[field] = value
        assert deploy_auth_client.post("/api/deploy", json=deploy_body).status_code == 422

    def test_env_instance_uppercase_accepted(self, deploy_body):
        deploy_body["env_instance"] = "ADA"
        assert DeployRequest(**deploy_body).env_instance == "ADA"

    def test_subdomain_must_match_customer_and_environment(
        self, deploy_auth_client, deploy_body
    ):
        deploy_body["subdomain"] = "amberd-acme"
        deploy_body["customer_name"] = "other-customer"
        response = deploy_auth_client.post("/api/deploy", json=deploy_body)
        assert response.status_code == 422
        assert "amberd-other-customer-ada" in response.json()["detail"]


class TestOtherRequestValidation:
    @pytest.mark.parametrize("subdomain", ["INVALID!!!", ""])
    def test_terminate_rejects_bad_subdomain(self, deploy_auth_client, subdomain):
        response = deploy_auth_client.post("/api/deploy/terminate", json={"subdomain": subdomain})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [{"subdomain": "INVALID!!!", "tier": 2}, {"subdomain": "amberd-acme-ada", "tier": 5}],
    )
    def test_move_tier_rejects_bad_input(self, deploy_auth_client, body):
        assert deploy_auth_client.post("/api/deploy/move-tier", json=body).status_code == 422

    @pytest.mark.parametrize(
        "url",
        [
            "/api/pipeline/status?subdomain=INVALID!!!",
            "/api/pipeline/status?subdomain=-amberd-acme",
            "/api/pipeline/history?subdomain=bad_subdomain",
            "/api/github/branches?repo=../../etc/passwd",
            "/api/github/tags?repo=../../etc/passwd",
        ],
    )
    def test_query_validation(self, deploy_auth_client, url):
        assert deploy_auth_client.get(url).status_code == 422

    def test_endpoints_require_authentication(self, test_client):
        assert test_client.get("/api/pipeline/active").status_code == 401
        assert test_client.post("/api/deploy/terminate", json={"subdomain": "amberd-a-b"}).status_code == 401


class TestGitHubRefs:
    def test_lists_sorted_branches_of_the_configured_owner(
        self, deploy_auth_client, github_pat, http_mock
    ):
        sent = http_mock(
            lambda request: httpx.Response(200, json=[{"name": "main"}, {"name": "develop"}])
        )
        response = deploy_auth_client.get("/api/github/branches", params={"repo": "devops"})
        assert response.status_code == 200
        assert response.json() == ["develop", "main"]
        assert sent[0].url.path == "/repos/amberd-ai/devops/branches"
        assert sent[0].headers["Authorization"] == "Bearer ghp_test"

    def test_lists_tags(self, deploy_auth_client, github_pat, http_mock):
        sent = http_mock(lambda request: httpx.Response(200, json=[{"name": "v1.1.0"}, {"name": "v1.0.0"}]))
        response = deploy_auth_client.get("/api/github/tags", params={"repo": "dealership_new"})
        assert response.json() == ["v1.0.0", "v1.1.0"]
        assert sent[0].url.path == "/repos/amberd-ai/dealership_new/tags"

    def test_github_outage_returns_an_empty_list(self, deploy_auth_client, github_pat, http_mock):
        http_mock(lambda request: httpx.Response(502))
        response = deploy_auth_client.get("/api/github/branches", params={"repo": "devops"})
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.parametrize("path", ["/api/github/branches", "/api/github/tags"])
    def test_503_when_github_is_not_configured(self, deploy_auth_client, monkeypatch, path):
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        response = deploy_auth_client.get(path, params={"repo": "devops"})
        assert response.status_code == 503

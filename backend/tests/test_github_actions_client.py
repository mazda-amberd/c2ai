"""Tests for registered-application GitHub Actions dispatch requests."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from c2ai.clients.github_actions import GitHubActionsClient


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["branches", "tags"])
@pytest.mark.parametrize("count", [0, 2, 125])
async def test_repository_refs_are_paginated_and_authenticated(kind, count):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.path == f"/repos/amberd-ai/code/{kind}"
        start = (int(request.url.params["page"]) - 1) * 100
        return httpx.Response(200, json=[{"name": f"v{i}"} for i in range(start, min(start + 100, count))])

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    client = GitHubActionsClient(repo_owner="amberd-ai", repo_name="code", github_token="test-token")
    with patch("c2ai.clients.github_actions.http_client", return_value=http_client):
        refs = await getattr(client, f"list_repository_{kind}")()
    assert refs == [f"v{i}" for i in range(count)]
    assert len(requests) == (2 if count > 100 else 1)


@pytest.mark.asyncio
async def test_workflow_dispatch_uses_filename_when_registration_stores_full_path():
    response = MagicMock(status_code=204)
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)
    http_context = MagicMock()
    http_context.__aenter__ = AsyncMock(return_value=http_client)
    http_context.__aexit__ = AsyncMock(return_value=None)

    client = GitHubActionsClient(
        repo_owner="amberd-ai",
        repo_name="dealership_new",
        github_token="test-token",
    )

    with patch(
        "c2ai.clients.github_actions.http_client",
        return_value=http_context,
    ):
        reference = await client.trigger_workflow(
            ".github/workflows/ada-deploy.yaml",
            "main",
            {"app_name": "ada-test"},
        )

    http_client.post.assert_awaited_once_with(
        "https://api.github.com/repos/amberd-ai/dealership_new/"
        "actions/workflows/ada-deploy.yaml/dispatches",
        json={
            "ref": "main",
            "inputs": {"app_name": "ada-test"},
            "return_run_details": True,
        },
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer test-token",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
        },
    )
    assert reference["workflow_id"] == "ada-deploy.yaml"


@pytest.mark.asyncio
async def test_workflow_dispatch_preserves_numeric_workflow_id():
    response = MagicMock(status_code=204)
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)
    http_context = MagicMock()
    http_context.__aenter__ = AsyncMock(return_value=http_client)
    http_context.__aexit__ = AsyncMock(return_value=None)

    client = GitHubActionsClient(
        repo_owner="amberd-ai",
        repo_name="dealership_new",
        github_token="test-token",
    )

    with patch(
        "c2ai.clients.github_actions.http_client",
        return_value=http_context,
    ):
        reference = await client.trigger_workflow("123456", "main", {})

    dispatched_url = http_client.post.await_args.args[0]
    assert dispatched_url.endswith("/actions/workflows/123456/dispatches")
    assert reference["workflow_id"] == "123456"


@pytest.mark.asyncio
async def test_workflow_dispatch_captures_returned_run_details():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "workflow_run_id": 891,
        "run_url": "https://api.github.com/repos/amberd-ai/devops/actions/runs/891",
        "html_url": "https://github.com/amberd-ai/devops/actions/runs/891",
    }
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)
    http_context = MagicMock()
    http_context.__aenter__ = AsyncMock(return_value=http_client)
    http_context.__aexit__ = AsyncMock(return_value=None)
    client = GitHubActionsClient(
        repo_owner="amberd-ai",
        repo_name="devops",
        github_token="test-token",
    )

    with patch(
        "c2ai.clients.github_actions.http_client",
        return_value=http_context,
    ):
        reference = await client.trigger_workflow("deploy.yml", "main", {})

    assert reference["run_id"] == 891
    assert reference["html_url"].endswith("/actions/runs/891")


@pytest.mark.asyncio
async def test_workflow_progress_contains_jobs_and_nested_steps():
    client = GitHubActionsClient(
        repo_owner="amberd-ai",
        repo_name="devops",
        github_token="test-token",
    )
    run = {
        "id": 891,
        "run_number": 23,
        "name": "Ada Build and Deploy",
        "display_title": "ada-deploy | example",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/amberd-ai/devops/actions/runs/891",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "created_at": "2026-09-03T12:00:00Z",
        "updated_at": "2026-09-03T12:04:00Z",
    }
    jobs = [
        {
            "id": 50,
            "name": "deploy",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-09-03T12:01:00Z",
            "completed_at": "2026-09-03T12:04:00Z",
            "html_url": "https://github.com/job/50",
            "steps": [
                {
                    "number": 1,
                    "name": "Set up job",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 2,
                    "name": "Deploy application",
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
        }
    ]
    with (
        patch.object(client, "get_workflow_run", AsyncMock(return_value=run)),
        patch.object(client, "get_workflow_run_jobs", AsyncMock(return_value=jobs)),
    ):
        progress = await client.get_workflow_progress(
            {"run_id": 891},
            instance_name="example",
            deployment_id="deployment-1",
        )

    assert progress is not None
    assert progress["jobs"][0]["name"] == "deploy"
    assert [step["name"] for step in progress["jobs"][0]["steps"]] == [
        "Set up job",
        "Deploy application",
    ]

"""Picking repositories, branches and workflow files from what a connection can see."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from c2ai.api.registered_applications.repositories import github_connections_repository
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.github_actions import GitHubActionsClient
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.db.session import get_db_session
from c2ai.registration import discovery

# The server's own token: not a saved connection.
SERVER_TOKEN = SimpleNamespace(resolve_github_connection=AsyncMock(return_value=None))

WORKFLOWS = {
    ".github/workflows/deploy.yml": "name: Deploy\non:\n  workflow_dispatch:\n    inputs: {}\njobs: {}\n",
    ".github/workflows/ci.yaml": "name: CI\non: [push, pull_request]\njobs: {}\n",
    ".github/workflows/broken.yml": "name: [unclosed\n",
    ".github/workflows/events.yml": "on:\n  repository_dispatch: {}\njobs: {}\n",
}


class FakeGitHub:
    def __init__(self, repositories: int = 3):
        self.repositories = [
            {"full_name": f"amberd-ai/repo-{n:03d}", "default_branch": "main", "private": n % 2 == 0}
            for n in range(repositories)
        ]
        self.installation_token = False
        self.token_refused = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if self.token_refused:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if path == "/user/repos":
            if self.installation_token:
                return httpx.Response(403, json={"message": "Resource not accessible by integration"})
            page, per_page = int(params["page"]), int(params["per_page"])
            return httpx.Response(200, json=self.repositories[(page - 1) * per_page : page * per_page])
        if path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": self.repositories[:1]})
        if path == "/repos/amberd-ai/app":
            return httpx.Response(200, json={"default_branch": "develop"})
        if path == "/repos/amberd-ai/app/branches":
            return httpx.Response(200, json=[{"name": "develop"}, {"name": "main"}])
        if path == "/repos/amberd-ai/app/tags":
            return httpx.Response(200, json=[{"name": "v1.0.0"}])
        if path == "/repos/amberd-ai/app/contents/.github/workflows":
            listing = [
                {"type": "file", "name": p.rsplit("/", 1)[1], "path": p} for p in WORKFLOWS
            ] + [
                {"type": "file", "name": "README.md", "path": ".github/workflows/README.md"},
                {"type": "dir", "name": "shared", "path": ".github/workflows/shared"},
            ]
            return httpx.Response(200, json=listing)
        if path.startswith("/repos/amberd-ai/app/contents/"):
            text = WORKFLOWS.get(path.removeprefix("/repos/amberd-ai/app/contents/"))
            if text is None:
                return httpx.Response(404, json={})
            content = base64.b64encode(text.encode()).decode()
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "content": content})
        if path.startswith("/repos/amberd-ai/empty/"):
            return httpx.Response(404, json={})
        return httpx.Response(404, json={"message": f"unexpected {path}"})


@pytest.fixture
def github(http_mock, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_environment")
    fake = FakeGitHub()
    http_mock(fake)
    return fake


async def test_repositories_are_listed_page_by_page(github):
    github.repositories = FakeGitHub(150).repositories
    listed = await discovery.list_repositories(None, "athena-environment", connections=SERVER_TOKEN)
    assert len(listed.items) == 150 and listed.truncated is False
    assert listed.items[0].full_name == "amberd-ai/repo-000"
    assert (listed.items[0].private, listed.items[1].private) == (True, False)

    capped, truncated = await GitHubActionsClient(github_token="t").list_accessible_repositories(limit=120)
    assert (len(capped), truncated) == (120, True)


async def test_an_app_installation_token_lists_its_installation(github):
    github.installation_token = True
    listed = await discovery.list_repositories(None, "athena-environment", connections=SERVER_TOKEN)
    assert [item.full_name for item in listed.items] == ["amberd-ai/repo-000"]


async def test_branches_and_tags_start_from_the_default_branch(github):
    refs = await discovery.list_refs(None, "athena-environment", "amberd-ai/app", connections=SERVER_TOKEN)
    assert (refs.default_branch, refs.branches, refs.tags) == ("develop", ["develop", "main"], ["v1.0.0"])


async def test_workflow_files_say_what_starts_them(github):
    listed = await discovery.list_workflows(
        None, "athena-environment", "amberd-ai/app", "develop", connections=SERVER_TOKEN
    )
    found = {item.path.rsplit("/", 1)[1]: item for item in listed.items}
    assert list(found) == ["broken.yml", "ci.yaml", "deploy.yml", "events.yml"]  # not README.md
    assert (found["deploy.yml"].name, [t.value for t in found["deploy.yml"].triggers]) == (
        "Deploy", ["workflow_dispatch"]
    )
    assert [t.value for t in found["events.yml"].triggers] == ["repository_dispatch"]
    assert (found["ci.yaml"].triggers, found["ci.yaml"].readable) == ([], True)  # push only
    assert found["broken.yml"].readable is False


async def test_a_repository_without_workflows_lists_none(github):
    listed = await discovery.list_workflows(
        None, "athena-environment", "amberd-ai/empty", "main", connections=SERVER_TOKEN
    )
    assert listed.items == []


@pytest.mark.parametrize(
    ("setup", "connection", "code"),
    [
        (lambda g, m: setattr(g, "token_refused", True), "athena-environment", "GitHubTokenRefused"),
        (lambda g, m: None, str(uuid4()), "GitHubConnectionNotFound"),
        (lambda g, m: m.setenv("GITHUB_PAT", ""), "athena-environment", "GitHubTokenMissing"),
    ],
)
async def test_what_stops_a_listing_is_said(github, monkeypatch, setup, connection, code):
    setup(github, monkeypatch)
    with pytest.raises(UnprocessableEntityError) as refused:
        await discovery.list_repositories(None, connection, connections=SERVER_TOKEN)
    assert refused.value.code == code


def test_the_workflow_listing_route_is_wired(github):
    async def no_session():
        yield None

    overrides = {
        require_admin: lambda: AthenaTokenUser(identifier="admin"),
        get_db_session: no_session,
        github_connections_repository: lambda: SERVER_TOKEN,
    }
    app.dependency_overrides.update(overrides)
    try:
        client = TestClient(app)
        response = client.get(
            "/api/registered-applications/github/workflows",
            params={"connection": "athena-environment", "repository": "amberd-ai/app", "ref": "develop"},
        )
        bad = client.get(
            "/api/registered-applications/github/refs",
            params={"connection": "athena-environment", "repository": "not a repo"},
        )
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 4
    assert bad.status_code == 422

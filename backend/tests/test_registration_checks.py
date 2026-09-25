"""Checks made while registering: reading a workflow, looking an image up."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from c2ai.api.registered_applications import clients
from c2ai.api.registered_applications.repositories import (
    applications_repository,
    credentials_repository,
    github_connections_repository,
)
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients import github_actions
from c2ai.clients.container_registry import ContainerRegistryTag
from c2ai.core.exceptions import ContainerImageTagNotFound
from c2ai.db.session import get_db_session
from c2ai.registration.checks import check_container_image, inspect_workflow, parse_workflow
from c2ai.schemas.registered_application import (
    ContainerImageCheckRequest,
    GitHubWorkflowInspectRequest,
)

WORKFLOW = """
name: Deploy
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      customer_name:
        description: Who the instance is for
        required: true
      dry_run:
        type: boolean
        default: false
      size:
        type: choice
        options: [small, large]
        default: large
      replicas:
        type: number
        default: "2"
      target:
        type: environment
      "bad name!":
        type: string
jobs: {}
"""


class FakeRepository:
    """amberd-ai/app on GitHub, with a deploy workflow on main."""

    def __init__(self):
        self.repository = {"default_branch": "main", "permissions": {"pull": True, "push": True}}
        self.repository_status = 200
        self.files = {".github/workflows/deploy.yml": WORKFLOW}
        self.workflow: dict | None = {"state": "active"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        prefix = "/repos/amberd-ai/app"
        path = request.url.path
        assert path.startswith(prefix), path
        rest = path[len(prefix):]
        if rest == "":
            return httpx.Response(self.repository_status, json=self.repository)
        if rest == "/branches/main":
            return httpx.Response(200, json={"name": "main"})
        if rest.startswith("/branches/") or rest.startswith("/git/ref/tags/"):
            return httpx.Response(404, json={})
        if rest.startswith("/contents/"):
            text = self.files.get(rest[len("/contents/"):])
            if text is None or request.url.params.get("ref") != "main":
                return httpx.Response(404, json={})
            content = base64.b64encode(text.encode()).decode()
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "content": content})
        if rest == "/actions/workflows/deploy.yml":
            if self.workflow is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.workflow)
        return httpx.Response(404, json={"message": f"unexpected {path}"})


@pytest.fixture
def github(http_mock, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_environment")
    github_actions._ref_cache.clear()
    fake = FakeRepository()
    http_mock(fake)
    return fake


# The server's own token: not a saved connection.
SERVER_TOKEN = SimpleNamespace(resolve_github_connection=AsyncMock(return_value=None))


def _request(**changes):
    return GitHubWorkflowInspectRequest.model_validate(
        {
            "github_connection": "athena-environment",
            "repository": "amberd-ai/app",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
            **changes,
        }
    )


def _checks(inspection):
    return {check.name: (check.ok, check.detail) for check in inspection.checks}


async def test_a_workflow_is_read_for_its_inputs_and_checked(github):
    inspection = await inspect_workflow(None, _request(), connections=SERVER_TOKEN)

    checks = _checks(inspection)
    assert [check.name for check in inspection.checks] == [
        "Repository", "Branch / Ref", "Workflow file", "Trigger", "GitHub Actions", "Permission"
    ]
    assert all(ok for ok, _ in checks.values()), checks
    assert "declaring 5 inputs (not importable: bad name!)" in checks["Workflow file"][1]
    assert [trigger.value for trigger in inspection.triggers] == ["workflow_dispatch"]

    inputs = {parameter.key: parameter for parameter in inspection.inputs}
    assert list(inputs) == ["customer_name", "dry_run", "size", "replicas", "target"]
    customer = inputs["customer_name"]
    assert (customer.parameter_type.value, customer.required, customer.description) == (
        "text", True, "Who the instance is for"
    )
    assert (inputs["dry_run"].parameter_type.value, inputs["dry_run"].default) == ("boolean", False)
    assert inputs["dry_run"].required is False
    assert (inputs["size"].options, inputs["size"].default) == (["small", "large"], "large")
    assert (inputs["replicas"].parameter_type.value, inputs["replicas"].default) == ("number", 2)
    assert inputs["target"].parameter_type.value == "text"


@pytest.mark.parametrize(
    ("change", "name", "detail"),
    [
        (lambda g: setattr(g, "workflow", None), "GitHub Actions", "default branch (main)"),
        (lambda g: setattr(g, "workflow", {"state": "disabled_manually"}), "GitHub Actions",
         "disabled"),
        (lambda g: g.repository.update(permissions={"pull": True}), "Permission", "only read"),
    ],
)
async def test_what_would_stop_c2ai_starting_it(github, change, name, detail):
    change(github)
    checks = _checks(await inspect_workflow(None, _request(), connections=SERVER_TOKEN))
    assert checks[name][0] is False
    assert detail in checks[name][1]


async def test_the_trigger_must_be_one_the_workflow_listens_for(github):
    inspection = await inspect_workflow(
        None, _request(trigger_method="repository_dispatch"), connections=SERVER_TOKEN
    )
    ok, detail = _checks(inspection)["Trigger"]
    assert ok is False and "started by workflow_dispatch, not repository_dispatch" in detail


@pytest.mark.parametrize(
    ("change", "request_changes", "last", "detail"),
    [
        (lambda g: setattr(g, "repository_status", 404), {}, "Repository", "cannot see it"),
        (lambda g: setattr(g, "repository_status", 401), {}, "Repository", "refused"),
        (lambda g: None, {"ref": "nope"}, "Branch / Ref", "no branch or tag 'nope'"),
        (lambda g: None, {"workflow_file_path": ".github/workflows/other.yml"}, "Workflow file",
         "There is no file"),
        (lambda g: g.files.update({".github/workflows/deploy.yml": "just: text"}), {},
         "Workflow file", "no `on:` section"),
    ],
)
async def test_checking_stops_at_the_first_thing_missing(github, change, request_changes, last, detail):
    change(github)
    inspection = await inspect_workflow(None, _request(**request_changes), connections=SERVER_TOKEN)
    assert inspection.checks[-1].name == last
    assert inspection.checks[-1].ok is False
    assert detail in inspection.checks[-1].detail
    assert inspection.inputs == []


async def test_a_deleted_saved_connection_is_reported(github):
    inspection = await inspect_workflow(
        None, _request(github_connection=str(uuid4())), connections=SERVER_TOKEN
    )
    assert _checks(inspection) == {
        "Connection": (False, "The selected GitHub connection no longer exists.")
    }


@pytest.mark.parametrize(
    ("on", "triggers"),
    [
        ("on: workflow_dispatch", ["workflow_dispatch"]),
        ("on: [push, repository_dispatch]", ["repository_dispatch"]),
        ('"on":\n  repository_dispatch: {}\n  workflow_dispatch:', ["workflow_dispatch",
                                                                   "repository_dispatch"]),
        ("on: push", []),
    ],
)
def test_on_is_read_in_each_of_its_forms(on, triggers):
    assert parse_workflow(f"name: x\n{on}\njobs: {{}}\n")[0] == triggers


def test_the_inspect_route_is_wired(github):
    async def no_session():
        yield None

    overrides = {
        require_admin: lambda: AthenaTokenUser(identifier="admin"),
        get_db_session: no_session,
        github_connections_repository: lambda: SERVER_TOKEN,
    }
    app.dependency_overrides.update(overrides)
    try:
        response = TestClient(app).post(
            "/api/registered-applications/github/inspect",
            json=json.loads(_request().model_dump_json(by_alias=True)),
        )
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
    assert response.status_code == 200, response.text
    assert [i["key"] for i in response.json()["inputs"]][:2] == ["customer_name", "dry_run"]


# --- Looking an image up --------------------------------------------------------


class FakeRegistry:
    def __init__(self):
        self.calls = []

    async def get_tag(self, *, registry, repository, tag, credential_id, username, password):
        self.calls.append((username, password))
        if tag != "1.2.3":
            raise ContainerImageTagNotFound(repository, tag)
        return ContainerRegistryTag(name=tag, digest="sha256:abc", last_updated=None)


def _stored_login(username):
    version = SimpleNamespace(
        id=uuid4(), container_configuration=SimpleNamespace(registry_username=username)
    )
    applications = SimpleNamespace(
        get_current_registered_application_version=AsyncMock(return_value=version)
    )
    credentials = SimpleNamespace(
        resolve_container_registry_credentials=AsyncMock(
            return_value=SimpleNamespace(username=username, password="stored-secret")
        )
    )
    return applications, credentials


def _image(**changes):
    return ContainerImageCheckRequest.model_validate(
        {"registry": "Docker Hub", "image_registry": "amberd/chat", "tag": "1.2.3", **changes}
    )


async def test_an_image_is_found_or_not_in_its_registry():
    registry = FakeRegistry()
    applications, credentials = _stored_login("amberd")
    found = await check_container_image(
        None, _image(), registry_client=registry, applications=applications, credentials=credentials
    )
    assert (found.ok, found.detail, found.digest) == (
        True, "Found docker.io/amberd/chat:1.2.3.", "sha256:abc"
    )
    assert registry.calls == [(None, None)]  # a public image: no login

    missing = await check_container_image(
        None, _image(tag="9.9"), registry_client=registry, applications=applications,
        credentials=credentials,
    )
    assert missing.ok is False
    assert missing.detail == "Image tag '9.9' was not found for repository 'amberd/chat'."


async def test_an_edit_lends_the_stored_password_for_the_same_username_only():
    registry = FakeRegistry()
    applications, credentials = _stored_login("amberd")
    lent = await check_container_image(
        None, _image(registry_username="amberd", application_id=str(uuid4())),
        registry_client=registry, applications=applications, credentials=credentials,
    )
    assert lent.ok is True
    assert registry.calls[-1] == ("amberd", "stored-secret")

    other = await check_container_image(
        None, _image(registry_username="someone-else", application_id=str(uuid4())),
        registry_client=registry, applications=applications, credentials=credentials,
    )
    assert (other.ok, other.detail) == (
        False, "Enter the registry password or token to look up a private image."
    )
    assert len(registry.calls) == 1


def test_the_image_check_route_is_wired():
    async def no_session():
        yield None

    registry = FakeRegistry()
    applications, credentials = _stored_login("amberd")
    overrides = {
        require_admin: lambda: AthenaTokenUser(identifier="admin"),
        get_db_session: no_session,
        clients.container_registry_client: lambda: registry,
        applications_repository: lambda: applications,
        credentials_repository: lambda: credentials,
    }
    app.dependency_overrides.update(overrides)
    try:
        response = TestClient(app).post(
            "/api/registered-applications/container/check",
            json={"registry": "Docker Hub", "image_registry": "amberd/chat", "tag": "1.2.3"},
        )
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

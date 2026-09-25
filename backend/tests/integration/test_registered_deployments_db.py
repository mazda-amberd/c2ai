"""Registered-application deployments end to end on PostgreSQL (GitHub and the
container registry faked): registration, dispatch payloads and credentials,
upgrade/rollback/terminate, dispatch failures, callbacks, and the outbox."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from c2ai.api.registered_applications import clients
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, get_current_user_token, require_admin
from c2ai.clients.container_registry import ContainerRegistryTag
from c2ai.config import get_settings
from c2ai.core.exceptions import ContainerImageTagNotFound
from c2ai.db.session import get_db_session
from c2ai.deployments import operations, repository as instances
from c2ai.deployments.service import DISPATCH_JOB, DispatchRequest
from c2ai.jobs import PostgresJobStore, Worker, get_job_store
from c2ai.jobs.handlers.deployments import track_open_operations
from c2ai.jobs.worker import registered_handlers
from c2ai.services.instance_metadata import load_instance_metadata_map
from tests.integration.conftest import _SERVER_URL
from tests.integration.test_deployments_db import FakeGitHub

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

BASE = "/api/registered-applications"


class FakeRegistry:
    """The container registry: known tags, and the credentials it was asked with."""

    def __init__(self):
        self.tags = {"1.2.3", "2.0.0"}
        self.calls: list[dict] = []

    async def get_tag(self, *, registry, repository, tag, credential_id, username, password):
        self.calls.append({"tag": tag, "username": username, "password": password})
        if tag not in self.tags:
            raise ContainerImageTagNotFound(repository, tag)
        return ContainerRegistryTag(name=tag, digest=None, last_updated=None)


@pytest.fixture
async def env(session_factory, http_mock, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_environment")
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "test passphrase")
    monkeypatch.setenv("DEPLOYMENT_CALLBACK_TOKEN", "callback-secret")
    async with session_factory() as db:
        await db.execute(
            text(
                "TRUNCATE pipeline_runs, deployment_instance_events, deployment_instances,"
                " jobs, github_connections CASCADE"
            )
        )
        await db.execute(
            text(
                "DELETE FROM registered_applications"
                " WHERE id <> 'ada00000-0000-4000-8000-000000000001'"
            )
        )
        await db.commit()
    github, registry = FakeGitHub(), FakeRegistry()
    http_mock(github)

    async def _session():
        async with session_factory() as session:
            yield session

    overrides = {
        get_db_session: _session,
        require_admin: lambda: AthenaTokenUser(identifier="admin", metadata={"user_type": "Admin"}),
        get_job_store: lambda: PostgresJobStore(session_factory),
        clients.container_registry_client: lambda: registry,
    }
    app.dependency_overrides.update(overrides)
    try:
        yield TestClient(app), github, registry, session_factory
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


def _register_github(client, *, parameters=("customer_name", "env_instance", "branch"),
                     connection="github-app-1", name="example-chatbot"):
    response = client.post(
        f"{BASE}/github",
        json={
            "application_type": "github_workflow",
            "name": name,
            "github": {
                "github_connection": connection,
                "trigger_method": "workflow_dispatch",
                "repository": "amberd-ai/example-chatbot",
                "workflow_file_path": ".github/workflows/deploy.yml",
                "ref": "main",
            },
            "parameters": [{"key": key, "type": "text"} for key in parameters],
            "llm": {
                "endpoint": "https://llm.example.com/v1",
                "api_token": "llm-secret",
                "model_name": "qwen3-coder-next",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _register_container(client):
    response = client.post(
        f"{BASE}/container",
        json={
            "application_type": "containerized",
            "name": "chat-service",
            "container": {
                "registry": "Docker Hub",
                "image_registry": "amberd/chat-service",
                "registry_username": "amberd",
                "registry_password": "registry-secret",
                "tag": "1.2.3",
                "pull_policy": "IfNotPresent",
                "port": 8080,
                "expose_public_service": True,
                "cpu_request": "500m",
                "memory_request": "512Mi",
                "scaling": "1",
            },
            "parameters": [{"key": "LOG_LEVEL", "value": "info"}],
            "llm": {
                "endpoint": "https://amberd-llm-gateway:8010",
                "api_token": "llm-secret",
                "model_name": "qwen3-6",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _deploy_container(client, application_id, *, name="chat-prod", tier=2, version="1.2.3"):
    return client.post(
        f"{BASE}/{application_id}/tiers/{tier}/deployments",
        json={"instance_name": name, "version": version},
    )


def _report(client, deployment_id, token, step, status_, failure_reason=None):
    """A pipeline progress callback, authenticated with its operation's token."""

    body = {"current_step": step, "status": status_}
    if failure_reason:
        body["failure_reason"] = failure_reason
    return client.post(
        f"{BASE}/deployments/{deployment_id}/progress",
        headers={"X-Athena-Deployment-Token": token},
        json=body,
    )


def _complete_container_deploy(client, github, deployment):
    """The container pipeline reports its way to a running instance."""

    token = github.events[-1][1]["deployment"]["callback_token"]
    for step in ("applying_resources", "configuring_dns"):
        assert _report(client, deployment["id"], token, step, "deploying").status_code == 200
    completed = _report(client, deployment["id"], token, "completed", "running")
    assert completed.status_code == 200, completed.text
    return token


async def _history(session_factory, instance_id):
    async with session_factory() as db:
        rows = await db.execute(
            text(
                "SELECT operation, kind, dispatch_state, conclusion FROM pipeline_runs"
                " WHERE deployment_instance_id = :id ORDER BY dispatched_at"
            ),
            {"id": instance_id},
        )
        return [tuple(row) for row in rows]


# --- GitHub Workflow applications -----------------------------------------------


async def test_github_deploy_names_the_instance_and_uses_the_saved_connection(env):
    client, github, _registry, session_factory = env
    connection = client.post(
        "/api/github-connections",
        json={
            "connection_name": "Chatbot repo",
            "repository_url": "https://github.com/amberd-ai/example-chatbot",
            "access_token": "ghp_saved_connection",
        },
    )
    assert connection.status_code == 201, connection.text
    application_id = _register_github(client, connection=connection.json()["id"])

    response = client.post(
        f"{BASE}/{application_id}/deployments",
        json={"tier": 2, "version": "main",
              "parameters": {"customer_name": "acme", "env_instance": "prod"}},
    )
    assert response.status_code == 201, response.text
    deployment = response.json()
    # The workflow derives its host label from the parameters.
    assert deployment["instance_name"] == "amberd-acme-prod"
    assert deployment["status"] == "deploying"
    workflow, inputs = github.dispatches[-1]
    assert workflow == "deploy.yml"
    assert inputs["customer_name"] == "acme" and inputs["provider"] == "tier2"
    assert inputs["deployment_id"] == deployment["id"]
    assert github.dispatch_auth[-1] == "Bearer ghp_saved_connection"
    assert await _history(session_factory, deployment["id"]) == [
        ("deploy", "deploy", "dispatched", None)
    ]


async def test_github_deploy_falls_back_to_a_generated_name(env):
    client, _github, _registry, _sf = env
    application_id = _register_github(client, parameters=("region",), name="Billing Sync")
    response = client.post(
        f"{BASE}/{application_id}/deployments",
        json={"tier": 3, "parameters": {"region": "eu"}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["instance_name"] == "billing-sync-tier-3"


async def test_the_deploy_forms_customer_is_recorded_but_not_sent_when_undeclared(env):
    """The Deploy Application form always sends customer_name and env_instance.

    A workflow that does not declare them must not receive them (workflow_dispatch
    rejects undeclared inputs), and they must not rename the host label; the
    customer is still recorded and shown with the instance.
    """

    client, github, _registry, session_factory = env
    application_id = _register_github(client, parameters=("region",), name="Billing Sync")
    response = client.post(
        f"{BASE}/{application_id}/deployments",
        json={
            "tier": 3,
            "instance_name": "billing-sync-tier-3",
            "parameters": {
                "region": "eu",
                "customer_name": "Acme Corp",
                "env_instance": "billing-sync-tier-3",
            },
        },
    )
    assert response.status_code == 201, response.text
    deployment = response.json()
    assert deployment["instance_name"] == "billing-sync-tier-3"
    _workflow, inputs = github.dispatches[-1]
    assert "customer_name" not in inputs and "env_instance" not in inputs
    assert inputs["region"] == "eu"
    assert deployment["configuration"]["customer_name"] == "Acme Corp"
    assert "customer_name" not in deployment["configuration"]["parameters"]

    async with session_factory() as db:
        metadata = await load_instance_metadata_map(db, ["billing-sync-tier-3"])
    assert metadata["billing-sync-tier-3"].client_name == "Acme Corp"
    assert metadata["billing-sync-tier-3"].instance_name == "billing-sync-tier-3"

    # Pending cards are titled with the registered application's name.
    app.dependency_overrides[get_current_user_token] = lambda: AthenaTokenUser(identifier="admin")
    try:
        active = client.get("/api/pipeline/active")
    finally:
        app.dependency_overrides.pop(get_current_user_token, None)
    assert active.status_code == 200, active.text
    assert [(row["subdomain"], row["application_name"]) for row in active.json()] == [
        ("billing-sync-tier-3", "Billing Sync")
    ]


async def test_declared_customer_parameters_still_reach_the_workflow(env):
    client, github, _registry, _sf = env
    application_id = _register_github(client)
    response = client.post(
        f"{BASE}/{application_id}/deployments",
        json={"tier": 2, "version": "main", "instance_name": "chat-prod",
              "parameters": {"customer_name": "acme", "env_instance": "chat-prod"}},
    )
    assert response.status_code == 201, response.text
    _workflow, inputs = github.dispatches[-1]
    assert (inputs["customer_name"], inputs["env_instance"]) == ("acme", "chat-prod")
    assert response.json()["instance_name"] == "amberd-acme-chat-prod"


async def test_undeclared_customer_name_must_be_text(env):
    client, _github, _registry, _sf = env
    application_id = _register_github(client, parameters=("region",))
    response = client.post(
        f"{BASE}/{application_id}/deployments",
        json={"tier": 3, "parameters": {"region": "eu", "customer_name": 42}},
    )
    assert response.status_code == 422
    assert "customer_name" in response.text


async def test_github_upgrade_and_retry_of_a_failed_upgrade(env):
    client, github, _registry, session_factory = env
    application_id = _register_github(client)
    deployment = client.post(
        f"{BASE}/{application_id}/deployments",
        json={"tier": 1, "version": "v1",
              "parameters": {"customer_name": "acme", "env_instance": "qa"}},
    ).json()
    github.finish(github.last_run_id())
    await track_open_operations(session_factory)

    upgrade = client.post(f"{BASE}/deployments/{deployment['id']}/upgrade", json={"version": "v2"})
    assert upgrade.status_code == 202, upgrade.text
    assert github.dispatches[-1][0] == "ada-update.yaml"
    assert github.dispatches[-1][1]["branch"] == "v2"
    assert github.dispatches[-1][1]["subdomain"] == "amberd-acme-qa"
    github.finish(github.last_run_id(), "failure")
    await track_open_operations(session_factory)
    assert client.get(f"{BASE}/deployments/{deployment['id']}").json()["status"] == "failed"

    retry = client.post(f"{BASE}/deployments/{deployment['id']}/upgrade", json={"version": "v2"})
    assert retry.status_code == 202, retry.text


# --- Containerized applications -------------------------------------------------


async def test_container_deploy_validates_the_tag_and_sends_credentials(env):
    client, github, registry, _sf = env
    application_id = _register_container(client)

    missing = _deploy_container(client, application_id, version="9.9.9")
    assert missing.status_code == 422
    assert missing.json()["code"] == "ContainerImageTagNotFound"

    response = _deploy_container(client, application_id)
    assert response.status_code == 201, response.text
    deployment = response.json()
    assert (deployment["tier"], deployment["hostname"]) == (2, "chat-prod.amberd.ai")
    # The registry was asked with the stored (decrypted) credential.
    assert registry.calls[-1] == {"tag": "1.2.3", "username": "amberd", "password": "registry-secret"}
    event_type, payload = github.events[-1]
    sent = payload["deployment"]
    assert event_type == "containerized-deploy"
    assert (sent["tier"], sent["registry_username"], sent["registry_token"]) == (
        "tier2", "amberd", "registry-secret"
    )
    assert sent["llm_api_token"] == "llm-secret"
    assert len(sent["callback_token"]) == 64  # per-operation HMAC
    assert "registry-secret" not in response.text


async def test_container_deploy_records_the_customer(env):
    client, github, _registry, _sf = env
    application_id = _register_container(client)
    response = client.post(
        f"{BASE}/{application_id}/tiers/2/deployments",
        json={"instance_name": "chat-prod", "version": "1.2.3", "customer_name": "Acme Corp"},
    )
    assert response.status_code == 201, response.text
    configuration = response.json()["configuration"]
    assert configuration["customer_name"] == "Acme Corp"
    # Not a container environment variable.
    assert "customer_name" not in configuration["container"]["environment_variables"]
    # Recorded only: the container pipeline's contract has no customer field.
    assert "Acme Corp" not in str(github.events[-1][1])

    blank = client.post(
        f"{BASE}/{application_id}/tiers/2/deployments",
        json={"instance_name": "chat-dev", "version": "1.2.3", "customer_name": "  "},
    )
    assert blank.status_code == 422


async def test_container_upgrade_rollback_and_callbacks(env):
    client, github, _registry, session_factory = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    deploy_token = _complete_container_deploy(client, github, deployment)
    assert client.get(f"{BASE}/deployments/{deployment['id']}").json()["status"] == "running"

    missing = client.post(
        f"{BASE}/deployments/{deployment['id']}/upgrade", json={"version": "3.0.0"}
    )
    assert missing.status_code == 422
    assert [row[0] for row in await _history(session_factory, deployment["id"])] == ["deploy"]

    upgrade = client.post(f"{BASE}/deployments/{deployment['id']}/upgrade", json={"version": "2.0.0"})
    assert upgrade.status_code == 202, upgrade.text
    workflow, inputs = github.dispatches[-1]
    assert (workflow, inputs["default_image_tag"], inputs["app_name"]) == (
        "containerized-app-update.yaml", "2.0.0", "chat-prod"
    )
    # The finished deploy's token no longer authenticates anything.
    stale = _report(client, deployment["id"], deploy_token, "completed", "running")
    assert stale.status_code == 401
    github.finish(github.last_run_id())
    await track_open_operations(session_factory)

    rollback = client.post(f"{BASE}/deployments/{deployment['id']}/rollback")
    assert rollback.status_code == 202, rollback.text
    workflow, inputs = github.dispatches[-1]
    assert (workflow, inputs["default_image_tag"]) == ("containerized-app-update.yaml", "1.2.3")
    assert rollback.json()["configuration"]["container"]["image_tag"] == "1.2.3"
    assert [row[:2] for row in await _history(session_factory, deployment["id"])] == [
        ("deploy", "deploy"), ("update", "upgrade"), ("update", "rollback")
    ]


async def test_failed_upgrade_dispatch_puts_the_instance_back(env):
    client, github, _registry, session_factory = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    _complete_container_deploy(client, github, deployment)
    github.reject_dispatches = True

    response = client.post(
        f"{BASE}/deployments/{deployment['id']}/upgrade", json={"version": "2.0.0"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "The upgrade pipeline could not be triggered."
    after = client.get(f"{BASE}/deployments/{deployment['id']}").json()
    assert after["status"] == "running"
    assert after["configuration"]["container"]["image_tag"] == "1.2.3"
    assert "could not be triggered" in after["events"][-1]["message"]
    assert (await _history(session_factory, deployment["id"]))[-1] == (
        "update", "upgrade", "failed", "failure"
    )


async def test_failed_first_deploy_is_retried_by_rollback_with_credentials(env):
    client, github, _registry, _session_factory = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    token = github.events[-1][1]["deployment"]["callback_token"]
    failed = _report(
        client, deployment["id"], token, "failed", "failed", "ImagePullBackOff"
    )
    assert failed.status_code == 200, failed.text

    retry = client.post(f"{BASE}/deployments/{deployment['id']}/rollback")
    assert retry.status_code == 202, retry.text
    sent = github.events[-1][1]["deployment"]
    assert (sent["registry_token"], sent["llm_api_token"]) == ("registry-secret", "llm-secret")
    assert retry.json()["rollback_count"] == 1


async def test_termination_requires_the_exact_name(env):
    client, github, _registry, _sf = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    _complete_container_deploy(client, github, deployment)

    url = f"{BASE}/deployments/{deployment['id']}/terminate"
    wrong = client.post(url, json={"confirmation": "chat"})
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "DeploymentTerminationConfirmationMismatch"
    response = client.post(url, json={"confirmation": "chat-prod"})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "terminating"
    assert github.dispatches[-1][0] == "containerized-app-terminate.yaml"


# --- Editing a template --------------------------------------------------------------


def _github_edit(name="example-chatbot"):
    """_register_github's body as an edit: the LLM token left out, so it is kept."""

    return {
        "application_type": "github_workflow",
        "name": name,
        "github": {
            "github_connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/example-chatbot",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
        },
        "parameters": [{"key": "customer_name", "type": "text"}],
        "llm": {"endpoint": "https://llm.example.com/v1", "model_name": "qwen3-coder-next"},
    }


def _container_edit():
    """_register_container's body as an edit: both secrets left out, so they are kept."""

    return {
        "application_type": "containerized",
        "name": "chat-service",
        "container": {
            "registry": "Docker Hub",
            "image_registry": "amberd/chat-service",
            "registry_username": "amberd",
            "tag": "1.2.3",
            "pull_policy": "IfNotPresent",
            "port": 8080,
            "expose_public_service": True,
            "cpu_request": "500m",
            "memory_request": "512Mi",
            "scaling": "1",
        },
        "parameters": [{"key": "LOG_LEVEL", "value": "info"}],
        "llm": {"endpoint": "https://amberd-llm-gateway:8010", "model_name": "qwen3-6"},
    }


def _catalog_entry(client, application_id):
    app.dependency_overrides[get_current_user_token] = lambda: AthenaTokenUser(identifier="admin")
    try:
        listed = client.get(BASE)
    finally:
        app.dependency_overrides.pop(get_current_user_token, None)
    assert listed.status_code == 200, listed.text
    return next(item for item in listed.json()["items"] if item["id"] == application_id)


async def test_an_edit_is_the_next_version_and_keeps_the_secrets_left_out(env):
    client, github, registry, session_factory = env
    application_id = _register_container(client)
    body = _container_edit()
    body["name"] = "chat-service-2"
    body["container"]["tag"] = "2.0.0"
    body["parameters"] = [{"key": "LOG_LEVEL", "value": "debug"}, {"key": "REGION", "value": "us"}]

    response = client.put(f"{BASE}/{application_id}", json=body)
    assert response.status_code == 200, response.text
    edited = response.json()
    assert (edited["version"], edited["name"], edited["container"]["tag"]) == (
        2, "chat-service-2", "2.0.0"
    )
    entry = _catalog_entry(client, application_id)
    assert (entry["name"], entry["current_version"], entry["can_edit"]) == ("chat-service-2", 2, True)

    deployed = _deploy_container(client, application_id, version="2.0.0")
    assert deployed.status_code == 201, deployed.text
    deployment = deployed.json()
    assert deployment["application_version"] == 2
    parameters = deployment["configuration"]["parameters"]
    assert (parameters["LOG_LEVEL"], parameters["REGION"]) == ("debug", "us")
    # The secrets the edit left out are the ones registered.
    assert registry.calls[-1]["password"] == "registry-secret"
    sent = github.events[-1][1]["deployment"]
    assert (sent["registry_token"], sent["llm_api_token"]) == ("registry-secret", "llm-secret")

    # Version 1 stays, for whatever ran it.
    async with session_factory() as db:
        versions = await db.execute(
            text(
                "SELECT version FROM registered_application_versions"
                " WHERE application_id = :id ORDER BY version"
            ),
            {"id": application_id},
        )
        assert list(versions.scalars()) == [1, 2]


async def test_an_edit_replaces_a_secret_it_supplies(env):
    client, github, registry, _sf = env
    application_id = _register_container(client)
    body = _container_edit()
    body["container"]["registry_password"] = "new-registry-secret"
    body["llm"]["api_token"] = "new-llm-secret"

    response = client.put(f"{BASE}/{application_id}", json=body)
    assert response.status_code == 200, response.text
    assert "new-registry-secret" not in response.text and "new-llm-secret" not in response.text

    assert _deploy_container(client, application_id).status_code == 201
    assert registry.calls[-1]["password"] == "new-registry-secret"
    sent = github.events[-1][1]["deployment"]
    assert (sent["registry_token"], sent["llm_api_token"]) == (
        "new-registry-secret", "new-llm-secret"
    )


async def test_an_edit_waits_until_nothing_of_the_template_is_live(env, monkeypatch):
    # So the termination pipeline is given a callback token to finish with.
    monkeypatch.setenv("CONTAINER_WORKFLOWS_ACCEPT_CALLBACK_TOKEN", "true")
    get_settings.cache_clear()
    client, github, _registry, _sf = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    _complete_container_deploy(client, github, deployment)
    assert _catalog_entry(client, application_id)["can_edit"] is False

    refused = client.put(f"{BASE}/{application_id}", json=_container_edit())
    assert refused.status_code == 409
    assert refused.json()["code"] == "RegisteredApplicationHasRunningInstances"
    assert "cannot be edited" in refused.json()["detail"]
    assert "'chat-prod' (Tier 2)" in refused.json()["detail"]

    terminate = client.post(
        f"{BASE}/deployments/{deployment['id']}/terminate", json={"confirmation": "chat-prod"}
    )
    assert terminate.status_code == 202, terminate.text
    # Terminating is not finished.
    assert client.put(f"{BASE}/{application_id}", json=_container_edit()).status_code == 409

    token = github.dispatches[-1][1]["callback_token"]
    dns = _report(client, deployment["id"], token, "configuring_dns", "terminating")
    assert dns.status_code == 200, dns.text
    terminated = _report(client, deployment["id"], token, "completed", "terminated")
    assert terminated.status_code == 200, terminated.text
    assert _catalog_entry(client, application_id)["can_edit"] is True
    edited = client.put(f"{BASE}/{application_id}", json=_container_edit())
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2


async def test_an_edit_keeps_the_type_and_needs_a_free_name(env):
    client, _github, _registry, _sf = env
    application_id = _register_github(client)
    _register_container(client)

    other_type = client.put(f"{BASE}/{application_id}", json=_container_edit())
    assert (other_type.status_code, other_type.json()["code"]) == (
        422, "RegisteredApplicationTypeChange"
    )
    taken = client.put(f"{BASE}/{application_id}", json=_github_edit(name="Chat-Service"))
    assert (taken.status_code, taken.json()["code"]) == (409, "DuplicateRegisteredApplication")
    missing = client.put(f"{BASE}/{uuid4()}", json=_github_edit())
    assert missing.status_code == 404

    # Its own name, recased, is not taken; and a parameter can be dropped.
    recased = client.put(f"{BASE}/{application_id}", json=_github_edit(name="Example-Chatbot"))
    assert recased.status_code == 200, recased.text
    assert (recased.json()["name"], recased.json()["version"]) == ("Example-Chatbot", 2)
    assert recased.json()["parameters"] == [{"key": "customer_name", "type": "text"}]


# --- The outbox -------------------------------------------------------------------


async def test_a_staged_operation_is_dispatched_by_the_worker(env):
    """The request that staged an operation died before dispatching it."""

    client, github, _registry, session_factory = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    _complete_container_deploy(client, github, deployment)

    store = PostgresJobStore(session_factory)
    async with session_factory() as db:  # phase 1 only, as a crashed request leaves it
        instance = await instances.prepare_registered_application_upgrade(
            db, deployment["id"], target_version="2.0.0", triggered_by="admin"
        )
        run = await operations.active_operation(db, instance.id)
        request = DispatchRequest(
            kind="upgrade", event_message="Upgrade dispatched.", triggered_by="admin",
            target_version="2.0.0",
        )
        await store.enqueue(DISPATCH_JOB, request.payload(instance, run.id), session=db)
        await db.commit()
    dispatched_before = len(github.dispatches)

    worker = Worker(
        store,
        worker_id="test-worker",
        handlers={DISPATCH_JOB: registered_handlers()[DISPATCH_JOB]},
        schedules=[],
        session_factory=session_factory,
    )
    assert await worker.run_once() == 1
    assert len(github.dispatches) == dispatched_before + 1
    assert (await _history(session_factory, deployment["id"]))[-1][2] == "dispatched"


async def test_an_operation_whose_dispatch_never_ran_is_undone(env):
    client, github, _registry, session_factory = env
    application_id = _register_container(client)
    deployment = _deploy_container(client, application_id).json()
    _complete_container_deploy(client, github, deployment)

    async with session_factory() as db:  # staged, then nothing ever dispatched it
        await instances.prepare_registered_application_upgrade(
            db, deployment["id"], target_version="2.0.0", triggered_by="admin"
        )
        await db.execute(
            text(
                "UPDATE pipeline_runs SET dispatched_at = now() - interval '10 minutes'"
                " WHERE dispatch_state = 'pending'"
            )
        )
        await db.commit()

    summary = await track_open_operations(session_factory)
    assert summary["restored"] == 1
    after = client.get(f"{BASE}/deployments/{deployment['id']}").json()
    assert (after["status"], after["configuration"]["container"]["image_tag"]) == ("running", "1.2.3")

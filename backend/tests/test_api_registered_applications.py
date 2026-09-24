"""API tests for GitHub Workflow application registration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.container_registry import (
    ContainerRegistryTag,
    ContainerRegistryTagPage,
)
from c2ai.core.exceptions import (
    ContainerImageTagNotFound,
    ContainerSecretsNotSupported,
    DuplicateRegisteredApplication,
    RegisteredApplicationHasRunningInstances,
)
from c2ai.crud.registered_application import (
    ContainerApplicationSecretPage,
    ContainerRegistryRuntime,
    RegisteredApplicationCatalogPage,
    RegisteredApplicationCatalogRecord,
    RegisteredApplicationDeploymentPage,
)
from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ApplicationSecretReference,
    ContainerApplicationConfiguration,
    ContainerApplicationSecret,
    DeploymentInstance,
    DeploymentInstanceEvent,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)


def _request_body() -> dict:
    return {
        "application_type": "github_workflow",
        "name": "example-chatbot",
        "description": "Customer support workflow",
        "github": {
            "github_connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/example-chatbot",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
        },
        "parameters": [
            {
                "key": "tier",
                "type": "text",
            }
        ],
        "llm": {
            "endpoint": "https://llm.example.com/v1",
            "api_token": "write-only-token",
            "model_name": "qwen3-coder-next",
        },
    }


def _container_request_body() -> dict:
    return {
        "application_type": "containerized",
        "name": "chat-service",
        "description": "Managed chat API",
        "container": {
            "registry": "Docker Hub",
            "image_registry": "amberd/chat-service",
            "registry_username": "amberd",
            "registry_password": "write-only-registry-password",
            "tag": "1.2.3",
            "pull_policy": "IfNotPresent",
            "port": 8080,
            "expose_public_service": True,
            "gpu_request": "1",
            "cpu_request": "500m",
            "memory_request": "512Mi",
            "scaling": "3",
            "storage": "10Gi",
        },
        "parameters": [{"key": "LOG_LEVEL", "value": "info"}],
        "llm": {
            "endpoint": "https://amberd-llm-gateway:8010",
            "api_token": "write-only-llm-token",
            "model_name": "qwen3-6",
        },
    }


def _persisted_version() -> RegisteredApplicationVersion:
    created_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    application = RegisteredApplication(
        id=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        name="example-chatbot",
        application_type="github_workflow",
        status="active",
        current_version=1,
        created_by="admin-user",
        created_at=created_at,
        updated_at=created_at,
    )
    version = RegisteredApplicationVersion(
        id=UUID("bbbbbbbb-0000-0000-0000-000000000001"),
        version=1,
        description="Customer support workflow",
        created_by="admin-user",
        created_at=created_at,
    )
    version.github_configuration = GitHubApplicationConfiguration(
        github_connection_id="github-app-1",
        trigger_method="workflow_dispatch",
        repository="amberd-ai/example-chatbot",
        workflow_file_path=".github/workflows/deploy.yml",
        ref="main",
    )
    version.llm_configuration = ApplicationLLMConfiguration(
        endpoint="https://llm.example.com/v1",
        api_token_encrypted=b"encrypted-token",
        model_name="qwen3-coder-next",
    )
    version.parameters = [
        ApplicationParameterDefinition(
            position=0,
            label="Target Tier",
            key="tier",
            parameter_type="select",
            required=True,
            options=["Tier 1", "Tier 2"],
        )
    ]
    version.secret_references = [
        ApplicationSecretReference(
            position=0,
            label="API Key",
            key="API_KEY",
            required=True,
            secret_reference="vault://athena/example-chatbot/api-key",
        )
    ]
    application.versions.append(version)
    return version


def _persisted_container_version() -> RegisteredApplicationVersion:
    created_at = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)
    application = RegisteredApplication(
        id=UUID("cccccccc-0000-0000-0000-000000000001"),
        name="chat-service",
        application_type="containerized",
        status="active",
        current_version=1,
        created_by="admin-user",
        created_at=created_at,
        updated_at=created_at,
    )
    version = RegisteredApplicationVersion(
        id=UUID("dddddddd-0000-0000-0000-000000000001"),
        version=1,
        description="Managed chat API",
        created_by="admin-user",
        created_at=created_at,
    )
    version.container_configuration = ContainerApplicationConfiguration(
        registry="Docker Hub",
        registry_credential_id=None,
        registry_username="amberd",
        registry_password_encrypted=b"encrypted-registry-password",
        image_repository="amberd/chat-service",
        default_image_tag="1.2.3",
        image_pull_policy="IfNotPresent",
        container_port=8080,
        expose_public_service=True,
        gpu_request="1",
        cpu_request="500m",
        memory_request="512Mi",
        scaling="3",
        storage="10Gi",
        environment_variables=[{"key": "LOG_LEVEL", "value": "info"}],
    )
    version.llm_configuration = ApplicationLLMConfiguration(
        endpoint="https://amberd-llm-gateway:8010",
        api_token_encrypted=b"encrypted-llm-token",
        model_name="qwen3-6",
    )
    version.parameters = [
        ApplicationParameterDefinition(
            position=0,
            label="LOG_LEVEL",
            key="LOG_LEVEL",
            parameter_type="text",
            required=True,
            default_value="info",
            options=[],
        )
    ]
    application.versions.append(version)
    return version


def _tracked_instance(
    version: RegisteredApplicationVersion,
    *,
    deployment_status: str = "deploying",
    current_step: str = "creating_namespace",
) -> DeploymentInstance:
    created_at = datetime(2026, 8, 13, 13, 0, tzinfo=UTC)
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000010"),
        application=version.application,
        application_version=version,
        instance_name="example-prod",
        tier=2,
        status=deployment_status,
        current_step=current_step,
        configuration={"parameters": {"tier": "Tier 2"}, "secrets": []},
        triggered_by="admin-user",
        dispatch_reference={"trigger_method": "workflow_dispatch"},
        rollback_count=0,
        created_at=created_at,
        updated_at=created_at,
    )
    instance.events.append(
        DeploymentInstanceEvent(
            id=UUID("ffffffff-0000-0000-0000-000000000010"),
            step=current_step,
            status=deployment_status,
            message="Namespace created.",
            created_by="deployment-pipeline",
            created_at=created_at,
        )
    )
    return instance


def _upgradeable_container_instance() -> DeploymentInstance:
    instance = _tracked_instance(
        _persisted_container_version(),
        deployment_status="running",
        current_step="completed",
    )
    instance.configuration = {
        "parameters": {"replicas": 3},
        "secrets": [],
        "container": {
            "registry": "Docker Hub",
            "image_repository": "amberd/chat-service",
            "image_tag": "1.2.3",
            "image_pull_policy": "IfNotPresent",
            "container_port": 8080,
            "replica_count": 3,
            "environment_variables": {"LOG_LEVEL": "info"},
            "persistent_volume_size": "10Gi",
            "service_type": "Ingress",
            "host": "chat-prod.amberd.ai",
            "tls_issuer": "letsencrypt",
            "image_pull_secret": "dockerhub-credential",
        },
        "dns": {
            "subdomain": "chat-prod",
            "hostname": "chat-prod.amberd.ai",
            "managed_by": "athena",
        },
        "managed_secrets": [],
    }
    instance.subdomain = "chat-prod"
    instance.hostname = "chat-prod.amberd.ai"
    instance.dns_status = "active"
    return instance


def _upgradeable_github_instance() -> DeploymentInstance:
    instance = _tracked_instance(
        _persisted_version(),
        deployment_status="running",
        current_step="completed",
    )
    instance.configuration = {
        "parameters": {"environment": "production", "tier": "Tier 2"},
        "secrets": [{"key": "API_KEY", "reference": "vault://release/api-key"}],
        "github": {
            "connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/example-chatbot",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
        },
    }
    return instance


@pytest.fixture(autouse=True)
def _no_managed_secrets():
    """Container deploys look up managed secrets; none exist unless a test says so."""

    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_active_container_application_secrets",
        new_callable=AsyncMock,
        return_value=[],
    ) as secrets_mock:
        yield secrets_mock


@pytest.fixture
def registered_applications_admin_client(test_client):
    def _admin_override():
        return AthenaTokenUser(
            identifier="admin-user",
            service="athena",
            metadata={"user_type": "Admin"},
        )

    app.dependency_overrides[require_admin] = _admin_override
    yield test_client
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def container_secret_admin_client(registered_applications_admin_client):
    provider = MagicMock()
    provider.upsert_secret = AsyncMock(
        return_value="vault://athena/chat-service/chatbot-api-key"
    )
    provider.delete_secret = AsyncMock()
    with patch(
        "c2ai.api.registered_applications._get_container_secret_provider",
        return_value=provider,
    ):
        yield registered_applications_admin_client, provider


def _managed_secret() -> ContainerApplicationSecret:
    created_at = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
    return ContainerApplicationSecret(
        id=UUID("99999999-0000-0000-0000-000000000001"),
        application_id=UUID("cccccccc-0000-0000-0000-000000000001"),
        name="chatbot-api-key",
        environment_variable="CHATBOT_API_KEY",
        secret_reference="vault://athena/chat-service/chatbot-api-key",
        created_by="admin-user",
        updated_by="admin-user",
        created_at=created_at,
        updated_at=created_at,
    )


def test_llm_model_catalog_lists_private_and_supported_public_models(
    registered_applications_admin_client,
):
    response = registered_applications_admin_client.get(
        "/api/registered-applications/llm-models",
    )

    assert response.status_code == 200
    body = response.json()
    names = [item["model_name"] for item in body["items"]]
    assert names == [
        "qwen3-coder-next",
        "qwen3-6",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ]
    assert body["total"] == len(names)
    assert all(item["pricing_available"] for item in body["items"])
    assert all(item["message"] is None for item in body["items"])
    assert body["items"][0]["provider"] == "vllm"


def test_llm_model_pricing_reports_a_supported_public_model(
    registered_applications_admin_client,
):
    response = registered_applications_admin_client.get(
        "/api/registered-applications/llm-models/pricing",
        params={"model_name": "gpt-4o"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "model_name": "gpt-4o",
        "provider": "openai",
        "pricing_available": True,
        "message": None,
    }


def test_llm_model_pricing_explains_an_unsupported_model(
    registered_applications_admin_client,
):
    response = registered_applications_admin_client.get(
        "/api/registered-applications/llm-models/pricing",
        params={"model_name": "minstal"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pricing_available"] is False
    assert body["provider"] is None
    assert body["message"].startswith(
        "Pricing not available for the model 'minstal'."
    )


def test_llm_model_pricing_reports_private_models_as_supported(
    registered_applications_admin_client,
):
    response = registered_applications_admin_client.get(
        "/api/registered-applications/llm-models/pricing",
        params={"model_name": "qwen3-6"},
    )

    assert response.status_code == 200
    assert response.json()["pricing_available"] is True
    assert response.json()["provider"] == "vllm"


def test_register_github_application_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/github",
        json=_request_body(),
    )

    assert response.status_code == 401


def test_deploy_registered_application_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/deployments",
        json={
            "instance_name": "example-prod",
            "tier": 2,
            "parameters": {"tier": "Tier 2"},
        },
    )

    assert response.status_code == 401


def test_deploy_registered_container_application_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/"
        "tiers/2/deployments",
        json={"instance_name": "chat-service-tier-2", "version": "2.0.0"},
    )

    assert response.status_code == 401


def test_upgrade_registered_application_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/deployments/"
        "eeeeeeee-0000-0000-0000-000000000010/upgrade",
        json={"version": "2.0.0"},
    )

    assert response.status_code == 401


def test_terminate_registered_application_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/deployments/"
        "eeeeeeee-0000-0000-0000-000000000010/terminate",
        json={"confirmation": "chat-prod"},
    )

    assert response.status_code == 401


def test_container_secret_management_requires_authentication(test_client):
    response = test_client.post(
        "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/secrets",
        json={
            "name": "chatbot-api-key",
            "environment_variable": "CHATBOT_API_KEY",
            "secret_value": "super-sensitive",
        },
    )

    assert response.status_code == 401


def test_create_container_secret_writes_value_to_provider_but_never_returns_it(
    container_secret_admin_client,
):
    client, provider = container_secret_admin_client
    secret = _managed_secret()

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_container_application_secret_create",
            new_callable=AsyncMock,
            return_value=secret,
        ) as prepare_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_container_application_secret_write",
            new_callable=AsyncMock,
            return_value=secret,
        ) as complete_mock,
    ):
        response = client.post(
            "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/secrets",
            json={
                "name": "chatbot-api-key",
                "environment_variable": "CHATBOT_API_KEY",
                "secret_value": "super-sensitive",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("vault://")
    assert "secret_value" not in body
    assert "value" not in body
    assert "super-sensitive" not in response.text
    assert provider.upsert_secret.await_args.kwargs["secret_value"] == "super-sensitive"
    assert prepare_mock.await_args.kwargs["created_by"] == "admin-user"
    assert complete_mock.await_args.kwargs["reference"].startswith("vault://")


def test_list_update_and_delete_container_secret_metadata(
    container_secret_admin_client,
):
    client, provider = container_secret_admin_client
    secret = _managed_secret()
    page = ContainerApplicationSecretPage(items=[secret], total=1)
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_container_application_secrets",
        new_callable=AsyncMock,
        return_value=page,
    ):
        list_response = client.get(
            "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/secrets"
        )

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["name"] == "chatbot-api-key"
    assert "secret_value" not in list_response.text

    provider.upsert_secret.return_value = "vault://athena/chat-service/rotated"
    updated_secret = _managed_secret()
    updated_secret.environment_variable = "ROTATED_API_KEY"
    updated_secret.secret_reference = "vault://athena/chat-service/rotated"
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_container_application_secret_update",
            new_callable=AsyncMock,
            return_value=updated_secret,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_container_application_secret_write",
            new_callable=AsyncMock,
            return_value=updated_secret,
        ),
    ):
        update_response = client.patch(
            "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/"
            "secrets/99999999-0000-0000-0000-000000000001",
            json={
                "environment_variable": "ROTATED_API_KEY",
                "secret_value": "rotated-sensitive",
            },
        )

    assert update_response.status_code == 200
    assert update_response.json()["environment_variable"] == "ROTATED_API_KEY"
    assert "rotated-sensitive" not in update_response.text
    assert provider.upsert_secret.await_args.kwargs["secret_value"] == "rotated-sensitive"

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_container_application_secret_delete",
            new_callable=AsyncMock,
            return_value=updated_secret,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_container_application_secret_delete",
            new_callable=AsyncMock,
        ) as complete_delete,
    ):
        delete_response = client.delete(
            "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/"
            "secrets/99999999-0000-0000-0000-000000000001"
        )

    assert delete_response.status_code == 204
    provider.delete_secret.assert_awaited_once_with(
        secret_id=updated_secret.id,
        reference="vault://athena/chat-service/rotated",
    )
    complete_delete.assert_awaited_once()


def test_github_application_rejects_managed_secret_endpoint(
    container_secret_admin_client,
):
    client, provider = container_secret_admin_client
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "prepare_container_application_secret_create",
        new_callable=AsyncMock,
        side_effect=ContainerSecretsNotSupported(),
    ):
        response = client.post(
            "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/secrets",
            json={
                "name": "github-secret",
                "environment_variable": "GITHUB_SECRET",
                "secret_value": "super-sensitive",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ContainerSecretsNotSupported"
    provider.upsert_secret.assert_not_awaited()


def test_container_secret_validation_never_echoes_submitted_value(
    container_secret_admin_client,
):
    client, provider = container_secret_admin_client
    response = client.post(
        "/api/registered-applications/cccccccc-0000-0000-0000-000000000001/secrets",
        json={
            "name": "INVALID-NAME",
            "environment_variable": "CHATBOT_API_KEY",
            "secret_value": "must-never-be-echoed",
        },
    )

    assert response.status_code == 422
    assert "must-never-be-echoed" not in response.text
    provider.upsert_secret.assert_not_awaited()


def test_register_github_application_returns_created_template(
    registered_applications_admin_client,
):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_github_registered_application",
        new_callable=AsyncMock,
        return_value=_persisted_version(),
    ) as create_mock:
        response = registered_applications_admin_client.post(
            "/api/registered-applications/github",
            json=_request_body(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert body["application_type"] == "github_workflow"
    assert body["version"] == 1
    assert body["github"]["github_connection"] == "github-app-1"
    assert body["parameters"] == [{"key": "tier", "type": "select"}]
    assert body["llm"] == {
        "endpoint": "https://llm.example.com/v1",
        "model_name": "qwen3-coder-next",
    }
    assert "api_token" not in body["llm"]
    assert "write-only-token" not in response.text
    assert create_mock.await_args.kwargs["created_by"] == "admin-user"


def test_deploy_registered_application_dispatches_and_returns_instance(
    registered_applications_admin_client,
):
    version = _persisted_version()
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000001"),
        application_id=version.application.id,
        application_version_id=version.id,
        instance_name="example-prod",
        tier=2,
        status="deploying",
        configuration={"parameters": {"tier": "Tier 2"}, "secrets": []},
        triggered_by="admin-user",
        dispatch_reference={"trigger_method": "workflow_dispatch"},
        created_at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )
    request = {
        "instance_name": "example-prod",
        "tier": 2,
        "parameters": {},
    }
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ) as create_mock,
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            return_value={"trigger_method": "workflow_dispatch"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/deployments",
            json=request,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "eeeeeeee-0000-0000-0000-000000000001"
    assert body["application_type"] == "github_workflow"
    assert body["tier"] == 2
    assert body["status"] == "deploying"
    assert create_mock.await_args.kwargs["triggered_by"] == "admin-user"
    dispatch_mock.assert_awaited_once()


def test_deploy_registered_application_names_the_instance_the_workflow_creates(
    registered_applications_admin_client,
):
    version = _persisted_version()
    version.parameters.extend(
        [
            ApplicationParameterDefinition(
                position=1,
                label="Customer",
                key="customer_name",
                parameter_type="text",
                required=True,
            ),
            ApplicationParameterDefinition(
                position=2,
                label="Environment Instance",
                key="env_instance",
                parameter_type="text",
                required=True,
            ),
            ApplicationParameterDefinition(
                position=3,
                label="Slack User",
                key="slack_user",
                parameter_type="text",
                required=True,
            ),
        ]
    )
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000004"),
        application_id=version.application.id,
        application_version_id=version.id,
        instance_name="amberd-test-deploy",
        tier=1,
        status="deploying",
        configuration={"parameters": {}, "secrets": []},
        triggered_by="admin-user",
        dispatch_reference={"trigger_method": "workflow_dispatch"},
        created_at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ) as create_mock,
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            return_value={"trigger_method": "workflow_dispatch"},
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/deployments",
            json={
                "tier": 1,
                "parameters": {"customer_name": "test", "env_instance": "deploy"},
            },
        )

    assert response.status_code == 201
    assert create_mock.await_args.kwargs["instance_name"] == "amberd-test-deploy"
    # slack_user is filled from the caller instead of being asked for.
    assert create_mock.await_args.kwargs["configuration"]["parameters"][
        "slack_user"
    ] == "admin-user"


def test_deploy_registered_application_falls_back_to_a_generated_instance_name(
    registered_applications_admin_client,
):
    version = _persisted_version()
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000005"),
        application_id=version.application.id,
        application_version_id=version.id,
        instance_name="example-chatbot-tier-2",
        tier=2,
        status="deploying",
        configuration={"parameters": {}, "secrets": []},
        triggered_by="admin-user",
        dispatch_reference={"trigger_method": "workflow_dispatch"},
        created_at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ) as create_mock,
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            return_value={"trigger_method": "workflow_dispatch"},
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/deployments",
            json={"tier": 2, "parameters": {}},
        )

    assert response.status_code == 201
    assert create_mock.await_args.kwargs["instance_name"] == "example-chatbot-tier-2"


def test_deploy_registered_application_reports_pipeline_failure(
    registered_applications_admin_client,
):
    version = _persisted_version()
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000002"),
        application_id=version.application.id,
        application_version_id=version.id,
        instance_name="example-prod",
        tier=2,
        status="pending",
        configuration={"parameters": {"tier": "Tier 2"}, "secrets": []},
        triggered_by="admin-user",
        created_at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            side_effect=RuntimeError("GitHub is unavailable"),
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/deployments",
            json={
                "instance_name": "example-prod",
                "tier": 2,
                "parameters": {},
            },
        )

    assert response.status_code == 503
    assert response.json()["code"] == "ServiceUnavailableError"
    complete_mock.assert_not_awaited()


def test_deploy_registered_container_uses_path_tier_and_stored_template(
    registered_applications_admin_client,
):
    version = _persisted_container_version()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock(
        return_value=ContainerRegistryTag(
            name="2.0.0",
            digest="sha256:release",
            last_updated=None,
        )
    )
    configuration = {
        "parameters": {"LOG_LEVEL": "info"},
        "llm": {
            "endpoint": "https://amberd-llm-gateway.tier3.svc:8010",
            "model_name": "qwen3-6",
        },
        "container": {
            "registry": "Docker Hub",
            "image_repository": "amberd/chat-service",
            "image_tag": "2.0.0",
            "image_pull_policy": "IfNotPresent",
            "container_port": 8080,
            "gpu_request": "1",
            "cpu_request": "500m",
            "memory_request": "512Mi",
            "scaling": "3",
            "replica_count": 3,
            "storage": "10Gi",
            "persistent_volume_size": "10Gi",
            "environment_variables": {
                "LOG_LEVEL": "info",
                "llm_endpoint": "https://amberd-llm-gateway.tier3.svc:8010",
                "llm_model_name": "qwen3-6",
            },
            "service_type": "Ingress",
            "host": "chat-service-tier-3.amberd.ai",
            "image_pull_secret": None,
            "registry_credentials_configured": True,
        },
        "dns": {
            "subdomain": "chat-service-tier-3",
            "hostname": "chat-service-tier-3.amberd.ai",
            "managed_by": "athena",
        },
        "managed_secrets": [],
    }
    instance = DeploymentInstance(
        id=UUID("eeeeeeee-0000-0000-0000-000000000003"),
        application_id=version.application.id,
        application_version_id=version.id,
        instance_name="chat-service-tier-3",
        tier=3,
        status="deploying",
        current_step="validating_configuration",
        configuration=configuration,
        triggered_by="admin-user",
        dispatch_reference={"pipeline": "container"},
        subdomain="chat-service-tier-3",
        hostname="chat-service-tier-3.amberd.ai",
        dns_status="pending",
        created_at=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_llm_api_token",
            new_callable=AsyncMock,
            return_value="write-only-llm-token",
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ) as create_mock,
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            return_value={"pipeline": "container"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/{version.application.id}/tiers/3/deployments",
            json={
                "instance_name": "chat-service-tier-3",
                "version": "2.0.0",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["tier"] == 3
    assert body["configuration"] == configuration
    assert body["subdomain"] == "chat-service-tier-3"
    assert "write-only-registry-password" not in response.text
    assert "encrypted-registry-password" not in response.text
    registry_client.get_tag.assert_awaited_once_with(
        registry="Docker Hub",
        repository="amberd/chat-service",
        tag="2.0.0",
        credential_id=None,
        username="amberd",
        password="write-only-registry-password",
    )
    assert create_mock.await_args.kwargs["tier"] == 3
    assert create_mock.await_args.kwargs["configuration"] == configuration
    assert dispatch_mock.await_args.kwargs["tier"] == 3
    dispatched_configuration = dispatch_mock.await_args.kwargs["configuration"]
    assert "write-only-registry-password" not in str(dispatched_configuration)


def test_deploy_registered_container_rejects_an_unknown_registry_tag(
    registered_applications_admin_client,
):
    version = _persisted_container_version()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock(
        side_effect=ContainerImageTagNotFound("amberd/chat-service", "missing")
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_llm_api_token",
            new_callable=AsyncMock,
            return_value="write-only-llm-token",
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "create_registered_application_deployment",
            new_callable=AsyncMock,
        ) as create_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/{version.application.id}/tiers/1/deployments",
            json={"instance_name": "chat-service", "version": "missing"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ContainerImageTagNotFound"
    create_mock.assert_not_awaited()


def test_container_deployment_endpoint_rejects_github_templates(
    registered_applications_admin_client,
):
    version = _persisted_version()
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=version,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/{version.application.id}/tiers/1/deployments",
            json={"instance_name": "release-tier-1", "version": "v2"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ContainerDeploymentNotSupported"


def test_generic_deployment_endpoint_rejects_container_templates(
    registered_applications_admin_client,
):
    version = _persisted_container_version()
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=version,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/{version.application.id}/deployments",
            json={
                "instance_name": "chat-service-tier-1",
                "tier": 1,
                "parameters": {},
            },
        )

    assert response.status_code == 422
    assert "Tier-scoped container deployment endpoint" in response.json()["detail"]


def test_list_and_get_registered_deployment_history(
    registered_applications_admin_client,
):
    instance = _tracked_instance(_persisted_version())
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_registered_application_deployments",
        new_callable=AsyncMock,
        return_value=RegisteredApplicationDeploymentPage(
            items=[instance],
            total=1,
        ),
    ) as list_mock:
        response = registered_applications_admin_client.get(
            "/api/registered-applications/deployments",
            params={"tier": 2, "instance": "amberd-acme-prod"},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["current_step"] == "creating_namespace"
    assert response.json()["items"][0]["application_version"] == 1
    assert list_mock.await_args.kwargs["tier"] == 2
    assert list_mock.await_args.kwargs["instance"] == "amberd-acme-prod"

    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/deployments/{instance.id}"
        )

    assert response.status_code == 200
    assert response.json()["events"][0]["message"] == "Namespace created."


def test_progress_callback_requires_shared_token_and_returns_updated_history(
    registered_applications_admin_client,
):
    instance = _tracked_instance(_persisted_version())
    url = f"/api/registered-applications/deployments/{instance.id}/progress"
    payload = {
        "current_step": "creating_namespace",
        "status": "deploying",
        "message": "Namespace created.",
    }
    with patch.dict("os.environ", {"DEPLOYMENT_CALLBACK_TOKEN": "callback-secret"}):
        response = registered_applications_admin_client.post(url, json=payload)
        assert response.status_code == 401

        with patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "update_registered_application_deployment_progress",
            new_callable=AsyncMock,
            return_value=instance,
        ) as update_mock:
            response = registered_applications_admin_client.post(
                url,
                json=payload,
                headers={"X-Athena-Deployment-Token": "callback-secret"},
            )

    assert response.status_code == 200
    assert response.json()["events"][0]["step"] == "creating_namespace"
    update_mock.assert_awaited_once()


def test_rollback_redispatches_stored_configuration(
    registered_applications_admin_client,
):
    instance = _tracked_instance(
        _persisted_version(),
        deployment_status="deploying",
        current_step="validating_configuration",
    )
    instance.rollback_count = 1
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_rollback",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.deployments.dispatch."
            "dispatch_registered_application_deployment",
            new_callable=AsyncMock,
            return_value={"trigger_method": "workflow_dispatch"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/rollback"
        )

    assert response.status_code == 202
    assert response.json()["rollback_count"] == 1
    assert dispatch_mock.await_args.kwargs["configuration"] == instance.configuration
    assert "Rollback #1" in complete_mock.await_args.kwargs["event_message"]


def test_upgrade_container_deployment_validates_tag_and_dispatches(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock(
        return_value=ContainerRegistryTag(
            name="2.0.0",
            digest="sha256:next",
            last_updated=None,
        )
    )

    async def prepare_upgrade(*_args, **_kwargs):
        instance.configuration["container"]["image_tag"] = "2.0.0"
        instance.status = "updating"
        instance.current_step = "validating_configuration"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_upgrade",
            new_callable=AsyncMock,
            side_effect=prepare_upgrade,
        ) as prepare_mock,
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_upgrade",
            new_callable=AsyncMock,
            return_value={"pipeline": "container-upgrade"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_upgrade_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/upgrade",
            json={"version": "2.0.0"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "updating"
    assert body["configuration"]["container"]["image_tag"] == "2.0.0"
    assert body["configuration"]["container"]["replica_count"] == 3
    assert body["configuration"]["dns"]["hostname"] == "chat-prod.amberd.ai"
    # A private registry needs the stored credential on the tag check too.
    registry_client.get_tag.assert_awaited_once_with(
        registry="Docker Hub",
        repository="amberd/chat-service",
        tag="2.0.0",
        credential_id="dockerhub-credential",
        username="amberd",
        password="write-only-registry-password",
    )
    assert prepare_mock.await_args.kwargs["triggered_by"] == "admin-user"
    assert dispatch_mock.await_args.kwargs["target_version"] == "2.0.0"
    assert dispatch_mock.await_args.kwargs["configuration"] == instance.configuration
    complete_mock.assert_awaited_once()


def test_upgrade_github_deployment_uses_same_endpoint_without_registry_lookup(
    registered_applications_admin_client,
):
    instance = _upgradeable_github_instance()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock()

    async def prepare_upgrade(*_args, **_kwargs):
        instance.configuration["github"]["version"] = "2.0.0"
        instance.status = "updating"
        instance.current_step = "validating_configuration"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_upgrade",
            new_callable=AsyncMock,
            side_effect=prepare_upgrade,
        ),
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_upgrade",
            new_callable=AsyncMock,
            return_value={"pipeline": "github-upgrade"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_upgrade_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/upgrade",
            json={"version": "2.0.0"},
        )

    assert response.status_code == 202
    assert response.json()["configuration"]["github"]["version"] == "2.0.0"
    registry_client.get_tag.assert_not_awaited()
    assert dispatch_mock.await_args.kwargs["target_version"] == "2.0.0"
    assert dispatch_mock.await_args.args[0].application.application_type == (
        "github_workflow"
    )


def test_upgrade_failed_github_deployment_restarts_its_workflow(
    registered_applications_admin_client,
):
    instance = _upgradeable_github_instance()
    instance.status = "failed"
    instance.current_step = "failed"
    instance.configuration["github"]["version"] = "dev"

    async def prepare_upgrade(*_args, **_kwargs):
        instance.status = "updating"
        instance.current_step = "validating_configuration"
        instance.failure_reason = None
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_upgrade",
            new_callable=AsyncMock,
            side_effect=prepare_upgrade,
        ),
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_upgrade",
            new_callable=AsyncMock,
            return_value={"pipeline": "github-upgrade"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_upgrade_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/upgrade",
            json={"version": "dev"},
        )

    assert response.status_code == 202
    assert dispatch_mock.await_args.kwargs["target_version"] == "dev"


def test_upgrade_container_deployment_reports_missing_tag(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock(
        side_effect=ContainerImageTagNotFound("amberd/chat-service", "9.9.9")
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_upgrade",
            new_callable=AsyncMock,
        ) as prepare_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/upgrade",
            json={"version": "9.9.9"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ContainerImageTagNotFound"
    prepare_mock.assert_not_awaited()


def test_upgrade_deployment_rejects_unsupported_type_and_active_state(
    registered_applications_admin_client,
):
    unsupported_instance = _tracked_instance(
        _persisted_version(),
        deployment_status="running",
        current_step="completed",
    )
    unsupported_instance.application.application_type = "unsupported"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=unsupported_instance,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{unsupported_instance.id}/upgrade",
            json={"version": "2.0.0"},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "DeploymentUpgradeNotSupported"

    container_instance = _upgradeable_container_instance()
    container_instance.status = "updating"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=container_instance,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{container_instance.id}/upgrade",
            json={"version": "2.0.0"},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "DeploymentUpgradeNotAvailable"


def test_upgrade_container_deployment_rolls_back_when_dispatch_fails(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()
    registry_client = MagicMock()
    registry_client.get_tag = AsyncMock(
        return_value=ContainerRegistryTag(
            name="2.0.0",
            digest=None,
            last_updated=None,
        )
    )

    async def prepare_upgrade(*_args, **_kwargs):
        instance.configuration["container"]["image_tag"] = "2.0.0"
        instance.status = "updating"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_upgrade",
            new_callable=AsyncMock,
            side_effect=prepare_upgrade,
        ),
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_upgrade",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pipeline unavailable"),
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_upgrade_dispatch",
            new_callable=AsyncMock,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/upgrade",
            json={"version": "2.0.0"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "ServiceUnavailableError"
    complete_mock.assert_not_awaited()


def test_terminate_container_deployment_confirms_and_dispatches_cleanup(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()

    async def prepare_termination(*_args, **_kwargs):
        instance.status = "terminating"
        instance.current_step = "validating_configuration"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_termination",
            new_callable=AsyncMock,
            side_effect=prepare_termination,
        ) as prepare_mock,
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_termination",
            new_callable=AsyncMock,
            return_value={"pipeline": "container-termination"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_termination_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/terminate",
            json={"confirmation": instance.instance_name},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "terminating"
    assert body["configuration"]["container"]["image_tag"] == "1.2.3"
    assert body["configuration"]["dns"]["hostname"] == "chat-prod.amberd.ai"
    assert prepare_mock.await_args.kwargs["triggered_by"] == "admin-user"
    assert dispatch_mock.await_args.kwargs["configuration"] == instance.configuration
    assert dispatch_mock.await_args.kwargs["triggered_by"] == "admin-user"
    complete_mock.assert_awaited_once()


def test_terminate_github_deployment_uses_same_confirmation_and_endpoint(
    registered_applications_admin_client,
):
    instance = _upgradeable_github_instance()

    async def prepare_termination(*_args, **_kwargs):
        instance.status = "terminating"
        instance.current_step = "validating_configuration"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_termination",
            new_callable=AsyncMock,
            side_effect=prepare_termination,
        ),
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_termination",
            new_callable=AsyncMock,
            return_value={"pipeline": "github-termination"},
        ) as dispatch_mock,
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_termination_dispatch",
            new_callable=AsyncMock,
            return_value=instance,
        ),
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/terminate",
            json={"confirmation": instance.instance_name},
        )

    assert response.status_code == 202
    assert response.json()["application_type"] == "github_workflow"
    assert response.json()["status"] == "terminating"
    assert dispatch_mock.await_args.args[0].application.application_type == (
        "github_workflow"
    )


def test_terminate_container_deployment_requires_exact_confirmation(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_termination",
            new_callable=AsyncMock,
        ) as prepare_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/terminate",
            json={"confirmation": "different-instance"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "DeploymentTerminationConfirmationMismatch"
    prepare_mock.assert_not_awaited()


def test_terminate_deployment_rejects_unsupported_type_and_active_state(
    registered_applications_admin_client,
):
    unsupported_instance = _tracked_instance(
        _persisted_version(),
        deployment_status="running",
        current_step="completed",
    )
    unsupported_instance.application.application_type = "unsupported"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=unsupported_instance,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{unsupported_instance.id}/terminate",
            json={"confirmation": unsupported_instance.instance_name},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "DeploymentTerminationNotSupported"

    container_instance = _upgradeable_container_instance()
    container_instance.status = "terminating"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=container_instance,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{container_instance.id}/terminate",
            json={"confirmation": container_instance.instance_name},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "DeploymentTerminationNotAvailable"


def test_terminate_container_deployment_rolls_back_when_dispatch_fails(
    registered_applications_admin_client,
):
    instance = _upgradeable_container_instance()

    async def prepare_termination(*_args, **_kwargs):
        instance.status = "terminating"
        return instance

    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_registered_application_deployment",
            new_callable=AsyncMock,
            return_value=instance,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "prepare_registered_application_termination",
            new_callable=AsyncMock,
            side_effect=prepare_termination,
        ),
        patch(
            "c2ai.api.registered_applications."
            "dispatch_registered_application_termination",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pipeline unavailable"),
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "complete_registered_application_termination_dispatch",
            new_callable=AsyncMock,
        ) as complete_mock,
    ):
        response = registered_applications_admin_client.post(
            f"/api/registered-applications/deployments/{instance.id}/terminate",
            json={"confirmation": instance.instance_name},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "ServiceUnavailableError"
    complete_mock.assert_not_awaited()


def test_register_github_application_returns_conflict_for_duplicate_name(
    registered_applications_admin_client,
):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_github_registered_application",
        new_callable=AsyncMock,
        side_effect=DuplicateRegisteredApplication("example-chatbot"),
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/github",
            json=_request_body(),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "DuplicateRegisteredApplication"


def test_register_github_application_validates_before_persistence(
    registered_applications_admin_client,
):
    invalid_body = _request_body()
    invalid_body["github"]["workflow_file_path"] = "deploy.yml"

    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_github_registered_application",
        new_callable=AsyncMock,
    ) as create_mock:
        response = registered_applications_admin_client.post(
            "/api/registered-applications/github",
            json=invalid_body,
        )

    assert response.status_code == 422
    create_mock.assert_not_awaited()


def test_register_container_application_returns_created_template(
    registered_applications_admin_client,
):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_container_registered_application",
        new_callable=AsyncMock,
        return_value=_persisted_container_version(),
    ) as create_mock:
        response = registered_applications_admin_client.post(
            "/api/registered-applications/container",
            json=_container_request_body(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "cccccccc-0000-0000-0000-000000000001"
    assert body["application_type"] == "containerized"
    assert body["version"] == 1
    assert body["container"]["registry_username"] == "amberd"
    assert body["container"]["image_registry"] == "amberd/chat-service"
    assert body["container"]["tag"] == "1.2.3"
    assert body["container"]["port"] == 8080
    assert body["container"]["pull_policy"] == "IfNotPresent"
    assert "registry_password" not in body["container"]
    assert body["parameters"] == [{"key": "LOG_LEVEL", "value": "info"}]
    assert "secrets" not in body
    assert body["llm"] == {
        "endpoint": "https://amberd-llm-gateway:8010",
        "model_name": "qwen3-6",
    }
    assert "api_token" not in body["llm"]
    assert create_mock.await_args.kwargs["created_by"] == "admin-user"


def test_register_container_application_returns_conflict_for_duplicate_name(
    registered_applications_admin_client,
):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_container_registered_application",
        new_callable=AsyncMock,
        side_effect=DuplicateRegisteredApplication("chat-service"),
    ):
        response = registered_applications_admin_client.post(
            "/api/registered-applications/container",
            json=_container_request_body(),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "DuplicateRegisteredApplication"


def test_register_container_application_validates_before_persistence(
    registered_applications_admin_client,
):
    invalid_body = _container_request_body()
    invalid_body["container"]["port"] = 70000

    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "create_container_registered_application",
        new_callable=AsyncMock,
    ) as create_mock:
        response = registered_applications_admin_client.post(
            "/api/registered-applications/container",
            json=invalid_body,
        )

    assert response.status_code == 422
    create_mock.assert_not_awaited()


@pytest.mark.parametrize("code_repository", [None, "amberd-ai/code"])
@pytest.mark.parametrize(("branches", "tags"), [([], []), (["main", "dev"], ["v2", "v1"]), (["v2"], ["v2", "v1"])])
def test_github_tags_use_code_repository_or_workflow_fallback(
    registered_applications_admin_client, code_repository, branches, tags,
):
    version = _persisted_version()
    version.github_configuration.code_repository = code_repository
    runtime = MagicMock(token="private-token", api_base_url="https://github.example/api/v3")
    with (
        patch("c2ai.api.registered_applications.crud_registered_application.get_current_registered_application_version", new_callable=AsyncMock, return_value=version),
        patch("c2ai.api.registered_applications.crud_github_connection.resolve_github_connection", new_callable=AsyncMock, return_value=runtime),
        patch("c2ai.api.registered_applications.GitHubActionsClient") as client_class,
    ):
        client_class.return_value.list_repository_branches = AsyncMock(return_value=branches)
        client_class.return_value.list_repository_tags = AsyncMock(return_value=tags)
        response = registered_applications_admin_client.get(f"/api/registered-applications/{version.application.id}/github-tags")
    assert response.status_code == 200
    repository = code_repository or version.github_configuration.repository
    assert response.json()["repository"] == repository
    assert response.json()["branches"] == branches
    assert response.json()["tags"] == tags
    assert response.json()["items"] == list(dict.fromkeys([*branches, *tags]))
    owner, name = repository.split("/")
    client_class.assert_called_once_with(repo_owner=owner, repo_name=name, github_token="private-token", api_base_url=runtime.api_base_url)
    assert "private-token" not in response.text


def test_github_tags_access_error_is_not_reported_as_empty_tags(registered_applications_admin_client):
    version = _persisted_version()
    error = httpx.HTTPStatusError("private upstream detail", request=httpx.Request("GET", "https://api.github.com"), response=httpx.Response(404))
    with (
        patch("c2ai.api.registered_applications.crud_registered_application.get_current_registered_application_version", new_callable=AsyncMock, return_value=version),
        patch("c2ai.api.registered_applications.crud_github_connection.resolve_github_connection", new_callable=AsyncMock, return_value=None),
        patch("c2ai.api.registered_applications.GitHubActionsClient") as client_class,
    ):
        client_class.return_value.list_repository_branches = AsyncMock(side_effect=error)
        response = registered_applications_admin_client.get(f"/api/registered-applications/{version.application.id}/github-tags")
    assert response.status_code == 422
    assert "access permissions" in response.text
    assert "private upstream detail" not in response.text


def test_github_tags_requires_authentication(test_client):
    response = test_client.get("/api/registered-applications/aaaaaaaa-0000-0000-0000-000000000001/github-tags")
    assert response.status_code in (401, 403)


def test_list_container_image_tags_returns_deployment_ready_references(
    registered_applications_admin_client,
):
    version = _persisted_container_version()
    registry_client = MagicMock()
    registry_client.list_tags = AsyncMock(
        return_value=ContainerRegistryTagPage(
            items=[
                ContainerRegistryTag(
                    name="1.2.3",
                    digest="sha256:default",
                    last_updated=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
                ),
                ContainerRegistryTag(
                    name="2.0.0",
                    digest="sha256:next",
                    last_updated=None,
                ),
            ],
            total=2,
        )
    )
    with (
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "get_current_registered_application_version",
            new_callable=AsyncMock,
            return_value=version,
        ),
        patch(
            "c2ai.api.registered_applications._get_container_registry_client",
            return_value=registry_client,
        ),
        patch(
            "c2ai.api.registered_applications.crud_registered_application."
            "resolve_container_registry_credentials",
            new_callable=AsyncMock,
            return_value=ContainerRegistryRuntime(
                username="amberd",
                password="write-only-registry-password",
            ),
        ),
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/{version.application.id}/image-tags",
            params={"limit": 25},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["application_id"] == str(version.application.id)
    assert body["registry"] == "Docker Hub"
    assert body["repository"] == "amberd/chat-service"
    assert body["default_tag"] == "1.2.3"
    assert body["total"] == 2
    assert body["limit"] == 25
    assert body["items"][0] == {
        "tag": "1.2.3",
        "image_reference": "docker.io/amberd/chat-service:1.2.3",
        "digest": "sha256:default",
        "last_updated": "2026-08-15T10:00:00Z",
        "is_default": True,
    }
    assert body["items"][1]["is_default"] is False
    registry_client.list_tags.assert_awaited_once_with(
        registry="Docker Hub",
        repository="amberd/chat-service",
        credential_id=None,
        limit=25,
        username="amberd",
        password="write-only-registry-password",
    )


def test_list_container_image_tags_rejects_github_application(
    registered_applications_admin_client,
):
    version = _persisted_version()
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=version,
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/{version.application.id}/image-tags"
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ContainerImageTagsNotSupported"


def test_list_container_image_tags_returns_not_found(
    registered_applications_admin_client,
):
    application_id = "ffffffff-0000-0000-0000-000000000012"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/{application_id}/image-tags"
        )

    assert response.status_code == 404
    assert response.json()["code"] == "RegisteredApplicationNotFound"


def test_list_registered_applications_returns_catalog_page(
    registered_applications_admin_client,
):
    version = _persisted_container_version()
    page = RegisteredApplicationCatalogPage(
        items=[
            RegisteredApplicationCatalogRecord(
                application=version.application,
                description=version.description,
                total_deployed_instances=3,
                tiers_deployed_to={1: 2, 3: 1},
            )
        ],
        total=1,
    )
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_registered_applications",
        new_callable=AsyncMock,
        return_value=page,
    ) as list_mock:
        response = registered_applications_admin_client.get(
            "/api/registered-applications",
            params={
                "search": "chat",
                "application_type": "containerized",
                "status": "active",
                "tier": 1,
                "sort_by": "instances",
                "sort_order": "desc",
                "offset": 0,
                "limit": 20,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["offset"] == 0
    assert body["limit"] == 20
    assert body["items"][0]["total_deployed_instances"] == 3
    assert body["items"][0]["tiers_deployed_to"] == [
        {"tier": "Tier 1", "instances": 2},
        {"tier": "Tier 3", "instances": 1},
    ]
    assert body["items"][0]["can_delete"] is False
    assert list_mock.await_args.kwargs["application_type"] == "containerized"
    assert list_mock.await_args.kwargs["application_status"] == "active"
    assert list_mock.await_args.kwargs["tier"] == 1


def test_list_registered_applications_validates_tier_before_query(
    registered_applications_admin_client,
):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_registered_applications",
        new_callable=AsyncMock,
    ) as list_mock:
        response = registered_applications_admin_client.get(
            "/api/registered-applications",
            params={"tier": 5},
        )

    assert response.status_code == 422
    list_mock.assert_not_awaited()


def test_get_registered_application_returns_current_version(
    registered_applications_admin_client,
):
    version = _persisted_version()
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=version,
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/{version.application.id}"
        )

    assert response.status_code == 200
    assert response.json()["application_type"] == "github_workflow"
    assert response.json()["version"] == 1


def test_get_registered_application_returns_not_found(
    registered_applications_admin_client,
):
    application_id = "ffffffff-0000-0000-0000-000000000010"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "get_current_registered_application_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = registered_applications_admin_client.get(
            f"/api/registered-applications/{application_id}"
        )

    assert response.status_code == 404
    assert response.json()["code"] == "RegisteredApplicationNotFound"


def test_delete_registered_application_returns_no_content(
    registered_applications_admin_client,
):
    application_id = "aaaaaaaa-0000-0000-0000-000000000020"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "delete_registered_application",
        new_callable=AsyncMock,
    ) as delete_mock:
        response = registered_applications_admin_client.delete(
            f"/api/registered-applications/{application_id}"
        )

    assert response.status_code == 204
    assert response.content == b""
    assert delete_mock.await_args.kwargs["deleted_by"] == "admin-user"


def test_delete_registered_application_returns_conflict_for_active_instances(
    registered_applications_admin_client,
):
    application_id = "bbbbbbbb-0000-0000-0000-000000000020"
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "delete_registered_application",
        new_callable=AsyncMock,
        side_effect=RegisteredApplicationHasRunningInstances("chat-service", 2),
    ):
        response = registered_applications_admin_client.delete(
            f"/api/registered-applications/{application_id}"
        )

    assert response.status_code == 409
    assert response.json()["code"] == "RegisteredApplicationHasRunningInstances"


def test_catalog_accepts_the_frontend_sort_key(registered_applications_admin_client):
    with patch(
        "c2ai.api.registered_applications.crud_registered_application."
        "list_registered_applications",
        new_callable=AsyncMock,
        return_value=MagicMock(items=[], total=0),
    ) as list_mock:
        response = registered_applications_admin_client.get(
            "/api/registered-applications",
            params={"sort_by": "created_at", "sort_order": "desc"},
        )
    assert response.status_code == 200
    assert list_mock.await_args.kwargs["sort_by"] == "created_at"

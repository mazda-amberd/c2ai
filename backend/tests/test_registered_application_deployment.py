"""Tests for registered-application deployment validation and dispatch."""

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ApplicationParameterDefinition,
    ContainerApplicationConfiguration,
    GitHubApplicationConfiguration,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.core.exceptions import UnprocessableEntityError
from c2ai.services.registered_application_deployment import (
    build_container_deployment_configuration,
    build_container_pipeline_payload,
    build_deployment_configuration,
    default_github_instance_name,
    dispatch_registered_application_deployment,
    dispatch_registered_application_termination,
    dispatch_registered_application_upgrade,
    resolve_github_deployment_instance_name,
)
from c2ai.schemas.registered_application import (
    ContainerRegisteredApplicationDeploymentCreate,
    RegisteredApplicationDeploymentCreate,
)


def _github_version() -> RegisteredApplicationVersion:
    application = RegisteredApplication(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        name="release-workflow",
        application_type="github_workflow",
        status="active",
        current_version=1,
        created_by="admin",
    )
    version = RegisteredApplicationVersion(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        version=1,
        created_by="admin",
    )
    version.github_configuration = GitHubApplicationConfiguration(
        github_connection_id="github-app-1",
        trigger_method="workflow_dispatch",
        repository="amberd-ai/release-workflow",
        workflow_file_path=".github/workflows/deploy.yml",
        ref="main",
    )
    version.llm_configuration = ApplicationLLMConfiguration(
        endpoint="https://amberd-llm-gateway:8010",
        api_token_encrypted=b"encrypted",
        model_name="qwen3-coder-next",
    )
    version.parameters = [
        ApplicationParameterDefinition(
            position=0,
            label="Environment",
            key="environment",
            parameter_type="select",
            required=True,
            options=["staging", "production"],
        ),
        ApplicationParameterDefinition(
            position=1,
            label="Target Tier",
            key="tier",
            parameter_type="select",
            required=True,
            options=["Tier 1", "Tier 2", "Tier 3", "Tier 4"],
        ),
    ]
    version.secret_references = []
    application.versions.append(version)
    return version


def _container_version() -> RegisteredApplicationVersion:
    application = RegisteredApplication(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        name="billing-worker",
        application_type="containerized",
        status="active",
        current_version=1,
        created_by="admin",
    )
    version = RegisteredApplicationVersion(
        id=UUID("40000000-0000-0000-0000-000000000001"),
        version=1,
        created_by="admin",
    )
    version.container_configuration = ContainerApplicationConfiguration(
        registry="docker.io",
        registry_credential_id="dockerhub-credential",
        registry_username="company",
        registry_password_encrypted=b"encrypted-password",
        image_repository="company/billing-worker",
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
        api_token_encrypted=b"encrypted",
        model_name="qwen3-6",
    )
    version.parameters = [
        ApplicationParameterDefinition(
            position=0,
            label="Replicas Override",
            key="worker_count",
            parameter_type="number",
            required=True,
            default_value=3,
            options=[],
        ),
        ApplicationParameterDefinition(
            position=1,
            label="Namespace",
            key="namespace",
            parameter_type="text",
            required=True,
            options=[],
        ),
    ]
    version.secret_references = []
    application.versions.append(version)
    return version


def test_build_container_deployment_uses_registered_template_as_source_of_truth():
    configuration = build_container_deployment_configuration(
        _container_version(),
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-tier-3",
            version="2.0.0",
        ),
        tier=3,
    )

    assert configuration == {
        "parameters": {"worker_count": 3, "namespace": "billing-tier-3"},
        "llm": {
            "endpoint": "https://amberd-llm-gateway.tier3.svc:8010",
            "model_name": "qwen3-6",
        },
        "container": {
            "registry": "docker.io",
            "image_repository": "company/billing-worker",
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
                "worker_count": "3",
                "llm_endpoint": "https://amberd-llm-gateway.tier3.svc:8010",
                "llm_model_name": "qwen3-6",
            },
            "service_type": "Ingress",
            "host": "billing-tier-3.amberd.ai",
            "image_pull_secret": "dockerhub-credential",
            "registry_credentials_configured": True,
        },
        "dns": {
            "subdomain": "billing-tier-3",
            "hostname": "billing-tier-3.amberd.ai",
            "managed_by": "athena",
        },
        "managed_secrets": [],
    }
    serialized = str(configuration)
    assert "encrypted-password" not in serialized
    assert "registry_username" not in serialized


def test_container_deployment_uses_values_fixed_at_registration():
    version = _container_version()
    version.parameters.append(
        ApplicationParameterDefinition(
            position=2,
            label="LOG_LEVEL",
            key="LOG_LEVEL",
            parameter_type="text",
            required=True,
            default_value="debug",
            options=[],
        )
    )

    configuration = build_container_deployment_configuration(
        version,
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-tier-3",
            version="2.0.0",
        ),
        tier=3,
    )

    assert configuration["parameters"]["LOG_LEVEL"] == "debug"
    # The registered value reaches the container as an environment variable.
    assert configuration["container"]["environment_variables"]["LOG_LEVEL"] == "debug"


def test_container_namespace_follows_the_instance_name():
    version = _container_version()
    # A namespace fixed at registration would collide across instances.
    namespace = next(p for p in version.parameters if p.key == "namespace")
    namespace.default_value = "test-app"

    configuration = build_container_deployment_configuration(
        version,
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-tier-3",
            version="2.0.0",
        ),
        tier=3,
    )

    assert configuration["parameters"]["namespace"] == "billing-tier-3"
    # Athena-derived values are pipeline inputs, not container env vars.
    assert "namespace" not in configuration["container"]["environment_variables"]


def test_build_container_deployment_rejects_a_github_template():
    with pytest.raises(UnprocessableEntityError, match="only supported"):
        build_container_deployment_configuration(
            _github_version(),
            ContainerRegisteredApplicationDeploymentCreate(
                instance_name="release-tier-3",
                version="2.0.0",
            ),
            tier=3,
        )


def test_build_github_configuration_resolves_tier_from_registered_parameters():
    configuration = build_deployment_configuration(
        _github_version(),
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod",
            tier=2,
            parameters={"environment": "production"},
        ),
    )

    assert configuration["parameters"] == {
        "environment": "production",
        "tier": "Tier 2",
    }
    assert "secrets" not in configuration


@pytest.mark.parametrize("registered_branch", [True, False])
def test_selected_tag_sets_code_branch_without_changing_workflow_ref(registered_branch):
    version = _github_version()
    version.github_configuration.code_repository = "amberd-ai/application"
    if registered_branch:
        version.parameters.append(ApplicationParameterDefinition(
            key="branch", label="branch", parameter_type="text", required=True,
            default_value=None, position=10,
        ))
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod", tier=1, version="v2.1.0",
            parameters={"environment": "production", **({"branch": "wrong"} if registered_branch else {})},
        ),
    )
    assert configuration["parameters"]["branch"] == "v2.1.0"
    assert configuration["github"]["ref"] == "main"
    assert configuration["github"]["repository"] == "amberd-ai/release-workflow"
    assert configuration["github"]["code_repository"] == "amberd-ai/application"


def test_build_github_configuration_uses_registered_key_value_parameters():
    version = _github_version()
    version.parameters = [
        ApplicationParameterDefinition(
            position=0,
            label="tier",
            key="tier",
            parameter_type="text",
            required=False,
            default_value="dedicated-gpu",
            options=[],
        ),
        ApplicationParameterDefinition(
            position=1,
            label="provider",
            key="provider",
            parameter_type="text",
            required=False,
            default_value="tier1",
            options=[],
        ),
    ]
    version.secret_references = []

    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod",
            tier=2,
        ),
    )

    assert configuration["parameters"] == {
        "tier": "dedicated-gpu",
        "provider": "tier1",
    }


@pytest.mark.parametrize(
    ("parameters", "detail"),
    [
        ({}, "Required deployment parameter 'environment'"),
        ({"environment": "invalid"}, "configured option"),
        ({"environment": "production", "unknown": True}, "Unknown"),
        ({"environment": "production", "tier": 2}, "cannot be supplied"),
    ],
)
def test_build_configuration_rejects_invalid_dynamic_values(
    parameters,
    detail,
):
    with pytest.raises(UnprocessableEntityError, match=detail):
        build_deployment_configuration(
            _github_version(),
            RegisteredApplicationDeploymentCreate(
                instance_name="release-prod",
                tier=2,
                parameters=parameters,
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [1, 2, 3])
async def test_dispatches_stored_github_workflow_configuration(tier):
    version = _github_version()
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod",
            tier=tier,
            parameters={"environment": "production"},
        ),
    )
    with patch(
        "c2ai.services.registered_application_deployment.GitHubActionsClient"
    ) as client_class:
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        reference = await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000001"),
            instance_name="release-prod",
            tier=tier,
            configuration=configuration,
            triggered_by="admin",
            github_token="connection-token",
            github_api_base_url="https://github.enterprise.example/api/v3",
        )

    client_class.assert_called_once_with(
        repo_owner="amberd-ai",
        repo_name="release-workflow",
        github_token="connection-token",
        api_base_url="https://github.enterprise.example/api/v3",
    )
    client.trigger_workflow.assert_awaited_once()
    workflow, ref, inputs = client.trigger_workflow.await_args.args
    assert workflow == ".github/workflows/deploy.yml"
    assert ref == "main"
    assert inputs["environment"] == "production"
    assert inputs["tier"] == f"Tier {tier}"
    assert inputs["provider"] == f"tier{tier}"
    assert "llm_endpoint" not in inputs
    assert "llm_model_name" not in inputs
    assert (
        configuration["llm"]["endpoint"]
        == f"https://amberd-llm-gateway.tier{tier}.svc:8010"
    )
    assert configuration["llm"]["model_name"] == "qwen3-coder-next"
    assert inputs["deployment_id"] == "50000000-0000-0000-0000-000000000001"
    assert "secret_references" not in inputs
    assert "instance_name" not in inputs
    assert "triggered_by" not in inputs
    assert reference["connection"] == "github-app-1"


@pytest.mark.asyncio
async def test_workflow_dispatch_omits_empty_secret_and_internal_metadata_inputs():
    version = _github_version()
    version.secret_references = []
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod",
            tier=1,
            parameters={"environment": "production"},
        ),
    )
    with patch(
        "c2ai.services.registered_application_deployment.GitHubActionsClient"
    ) as client_class:
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000009"),
            instance_name="release-prod",
            tier=1,
            configuration=configuration,
            triggered_by="admin",
        )

    _, _, inputs = client.trigger_workflow.await_args.args
    assert inputs == {
        "environment": "production",
        "tier": "Tier 1",
        "provider": "tier1",
        "deployment_id": "50000000-0000-0000-0000-000000000009",
        # amberd-ai workflows always declare slack_user.
        "slack_user": "admin",
    }


@pytest.mark.asyncio
async def test_slack_user_default_is_scoped_to_configured_repository_owners():
    version = _github_version()
    version.secret_references = []
    version.github_configuration.repository = "other-org/release-workflow"
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            tier=1,
            parameters={"environment": "production"},
        ),
        instance_name="release-prod",
        triggered_by="admin",
    )
    with patch(
        "c2ai.services.registered_application_deployment.GitHubActionsClient"
    ) as client_class:
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000011"),
            instance_name="release-prod",
            tier=1,
            configuration=configuration,
            triggered_by="admin",
        )

    _, _, inputs = client.trigger_workflow.await_args.args
    assert "slack_user" not in inputs


@pytest.mark.asyncio
async def test_registered_slack_user_parameter_is_filled_from_the_caller():
    version = _github_version()
    version.secret_references = []
    version.parameters.append(
        ApplicationParameterDefinition(
            position=2,
            label="Slack User",
            key="slack_user",
            parameter_type="text",
            required=True,
        )
    )
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            tier=1,
            parameters={"environment": "production"},
        ),
        instance_name="release-prod",
        triggered_by="release-bot",
    )
    assert configuration["parameters"]["slack_user"] == "release-bot"

    with patch(
        "c2ai.services.registered_application_deployment.GitHubActionsClient"
    ) as client_class:
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000012"),
            instance_name="release-prod",
            tier=1,
            configuration=configuration,
            triggered_by="release-bot",
        )

    _, _, inputs = client.trigger_workflow.await_args.args
    assert inputs["slack_user"] == "release-bot"


def test_github_instance_name_follows_the_workflow_derived_host_label():
    configuration = {
        "parameters": {"customer_name": "test", "env_instance": "deploy"},
    }

    assert resolve_github_deployment_instance_name(
        configuration,
        application_name="ADA_code_repo",
        tier=1,
    ) == "amberd-test-deploy"


def test_github_instance_name_falls_back_to_a_generated_template_name():
    assert resolve_github_deployment_instance_name(
        {"parameters": {"environment": "production"}},
        application_name="ADA_code_repo",
        tier=1,
    ) == "ada-code-repo-tier-1"

    assert default_github_instance_name("  ", 3) == "application-tier-3"


@pytest.mark.asyncio
async def test_dispatches_registered_repository_event():
    version = _github_version()
    version.github_configuration.trigger_method = "repository_dispatch"
    configuration = build_deployment_configuration(
        version,
        RegisteredApplicationDeploymentCreate(
            instance_name="release-prod",
            tier=2,
            parameters={"environment": "production"},
        ),
    )
    with patch(
        "c2ai.services.registered_application_deployment.GitHubActionsClient"
    ) as client_class:
        client = client_class.return_value
        client.trigger_repository_dispatch = AsyncMock(
            return_value={"trigger_method": "repository_dispatch"}
        )
        reference = await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000002"),
            instance_name="release-prod",
            tier=2,
            configuration=configuration,
            triggered_by="admin",
        )

    event_type, payload = client.trigger_repository_dispatch.await_args.args
    assert event_type == "athena-deploy"
    assert payload["environment"] == "production"
    assert payload["provider"] == "tier2"
    assert "llm_endpoint" not in payload
    assert "llm_model_name" not in payload
    assert payload["ref"] == "main"
    assert payload["workflow_file_path"] == ".github/workflows/deploy.yml"
    assert reference["connection"] == "github-app-1"


@pytest.mark.asyncio
async def test_dispatches_container_configuration_to_pipeline():
    version = _container_version()
    configuration = build_container_deployment_configuration(
        version,
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-prod",
            version="2.0.0",
        ),
        tier=3,
    )
    with (
        patch.dict(
            "os.environ",
            {
                "CONTAINER_DEPLOYMENT_REPOSITORY": "platform/deployments",
                "CONTAINER_DEPLOYMENT_WORKFLOW": "container.yml",
                "DEVOPS_BRANCH": "stable",
            },
        ),
        patch(
            "c2ai.services.registered_application_deployment.GitHubActionsClient"
        ) as client_class,
    ):
        client = client_class.return_value
        client.trigger_repository_dispatch = AsyncMock(
            return_value={"trigger_method": "repository_dispatch"}
        )
        reference = await dispatch_registered_application_deployment(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000003"),
            instance_name="billing-prod",
            tier=3,
            configuration=configuration,
            triggered_by="admin",
            registry_username="company",
            registry_token="registry-password",
            llm_api_token="llm-token",
        )

    client_class.assert_called_once_with(repo_owner="platform", repo_name="deployments")
    event_type, client_payload = client.trigger_repository_dispatch.await_args.args
    assert event_type == "containerized-deploy"
    inputs = client_payload["deployment"]
    # Progress correlation resolves the run from the workflow the event starts.
    assert reference["workflow_id"] == "container.yml"
    assert reference["ref"] == "stable"
    # Every field the containerized pipeline reads is supplied.
    assert set(inputs) == {
        "triggered_by",
        "deployment_id",
        "app_name",
        "callback_base_url",
        "tier",
        "container_registry",
        "image_registry",
        "registry_username",
        "registry_token",
        "default_image_tag",
        "container_port",
        "image_pull_policy",
        "expose_public_service",
        "gpu_request",
        "cpu_request",
        "memory_request",
        "replica_count",
        "persistent_volume",
        "namespace",
        "env_vars",
        "managed_secrets",
        "llm_endpoint",
        "llm_api_token",
        "llm_model_name",
    }
    assert inputs["app_name"] == "billing-prod"
    assert inputs["tier"] == "tier3"
    assert inputs["namespace"] == "billing-prod"
    assert inputs["container_registry"] == "Docker Hub"
    assert inputs["image_registry"] == "company/billing-worker"
    assert inputs["default_image_tag"] == "2.0.0"
    assert inputs["registry_username"] == "company"
    assert inputs["registry_token"] == "registry-password"
    assert inputs["llm_api_token"] == "llm-token"
    assert inputs["expose_public_service"] is True
    assert inputs["gpu_request"] == "1"
    assert inputs["cpu_request"] == "500m"
    assert inputs["replica_count"] == "3"
    assert inputs["container_port"] == "8080"
    assert inputs["persistent_volume"] == "10Gi"
    # Registered parameters and the LLM settings both reach the container.
    assert inputs["env_vars"] == {
        "LOG_LEVEL": "info",
        "worker_count": "3",
        "llm_endpoint": "https://amberd-llm-gateway.tier3.svc:8010",
        "llm_api_token": "llm-token",
        "llm_model_name": "qwen3-6",
    }
    # The token is added at dispatch only, never to the stored snapshot.
    assert "llm-token" not in str(configuration)
    assert reference["pipeline"] == "container"


@pytest.mark.asyncio
async def test_container_pipeline_inputs_stay_json_parsable_without_resources():
    version = _container_version()
    version.container_configuration.gpu_request = None
    version.container_configuration.scaling = None
    version.container_configuration.storage = None
    configuration = build_container_deployment_configuration(
        version,
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-prod",
            version="2.0.0",
        ),
        tier=1,
    )

    inputs = build_container_pipeline_payload(
        configuration,
        deployment_id=UUID("50000000-0000-0000-0000-000000000003"),
        instance_name="billing-prod",
        tier=1,
        triggered_by="admin",
        registry_username=None,
        registry_token=None,
        llm_api_token=None,
    )

    # The pipeline feeds these to `jq --argjson`, so they must parse as JSON.
    assert json.loads(inputs["gpu_request"]) == 0
    assert json.loads(inputs["replica_count"]) == 1
    assert json.loads(inputs["container_port"]) == 8080
    assert inputs["expose_public_service"] is True
    assert inputs["persistent_volume"] == ""
    assert inputs["registry_token"] == ""


@pytest.mark.asyncio
async def test_dispatches_container_upgrade_to_designated_pipeline():
    version = _container_version()
    configuration = {
        "parameters": {"replicas": 3},
        "container": {
            "registry": "Docker Hub",
            "image_repository": "company/billing-worker",
            "image_tag": "2.0.0",
            "replica_count": 3,
        },
        "dns": {
            "subdomain": "billing-prod",
            "hostname": "billing-prod.amberd.ai",
        },
    }
    with (
        patch.dict(
            "os.environ",
            {
                "CONTAINER_UPGRADE_REPOSITORY": "platform/upgrades",
                "CONTAINER_UPGRADE_WORKFLOW": "containerized-app-update.yaml",
                "ATHENA_CALLBACK_BASE_URL": "https://athena.example.com/",
                "DEVOPS_BRANCH": "stable",
            },
        ),
        patch(
            "c2ai.services.registered_application_deployment.GitHubActionsClient"
        ) as client_class,
    ):
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        reference = await dispatch_registered_application_upgrade(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000004"),
            instance_name="billing-prod",
            tier=3,
            target_version="2.0.0",
            configuration=configuration,
            triggered_by="admin",
        )

    client_class.assert_called_once_with(repo_owner="platform", repo_name="upgrades")
    workflow, ref, inputs = client.trigger_workflow.await_args.args
    assert workflow == "containerized-app-update.yaml"
    assert ref == "stable"
    assert inputs == {
        "triggered_by": "admin",
        "deployment_id": "50000000-0000-0000-0000-000000000004",
        "app_name": "billing-prod",
        "callback_base_url": "https://athena.example.com",
        "default_image_tag": "2.0.0",
    }
    assert reference["pipeline"] == "container-upgrade"


@pytest.mark.asyncio
async def test_container_upgrade_defaults_to_amberd_devops_workflow():
    version = _container_version()
    configuration = {
        "container": {"image_tag": "2.0.0"},
        "dns": {"subdomain": "billing-prod"},
    }
    with (
        patch.dict(
            "os.environ",
            {
                "ATHENA_CALLBACK_BASE_URL": "https://athena.amberd.ai",
                "DEVOPS_BRANCH": "main",
            },
            clear=True,
        ),
        patch(
            "c2ai.services.registered_application_deployment.GitHubActionsClient"
        ) as client_class,
    ):
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        await dispatch_registered_application_upgrade(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000004"),
            instance_name="billing-prod",
            tier=3,
            target_version="2.0.0",
            configuration=configuration,
            triggered_by="admin",
        )

    client_class.assert_called_once_with(
        repo_owner="amberd-ai",
        repo_name="devops",
    )
    workflow, ref, _inputs = client.trigger_workflow.await_args.args
    assert workflow == "containerized-app-update.yaml"
    assert ref == "main"


@pytest.mark.asyncio
async def test_dispatches_github_upgrade_to_predefined_amberd_workflow():
    version = _github_version()
    with patch(
        "c2ai.services.registered_application_deployment."
        "dispatch_github_update_workflow",
        new_callable=AsyncMock,
    ) as dispatch_mock:
        reference = await dispatch_registered_application_upgrade(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000006"),
            instance_name="release-prod",
            tier=2,
            target_version="2.0.0",
            configuration={"github": {"version": "2.0.0"}},
            triggered_by="admin",
        )

    dispatch_mock.assert_awaited_once_with(
        correlation_id="50000000-0000-0000-0000-000000000006",
        branch="2.0.0",
        subdomain="release-prod",
        triggered_by="admin",
    )
    assert reference["workflow_id"] == "ada-update.yaml"
    assert reference["version"] == "2.0.0"
    assert reference["pipeline"] == "github-upgrade"


@pytest.mark.asyncio
async def test_github_upgrade_targets_the_deployed_workflow_subdomain():
    version = _github_version()
    configuration = {
        "parameters": {
            "customer_name": "test",
            "env_instance": "deploy",
            "branch": "26.06.02",
        },
        "github": {"version": "26.06.03"},
    }
    with patch(
        "c2ai.services.registered_application_deployment."
        "dispatch_github_update_workflow",
        new_callable=AsyncMock,
    ) as dispatch_mock:
        reference = await dispatch_registered_application_upgrade(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000008"),
            instance_name="ada-code-repo-tier-1",
            tier=1,
            target_version="26.06.03",
            configuration=configuration,
            triggered_by="admin",
        )

    dispatch_mock.assert_awaited_once_with(
        correlation_id="50000000-0000-0000-0000-000000000008",
        branch="26.06.03",
        subdomain="amberd-test-deploy",
        triggered_by="admin",
    )
    assert reference["subdomain"] == "amberd-test-deploy"


@pytest.mark.asyncio
async def test_github_termination_targets_the_deployed_workflow_subdomain():
    version = _github_version()
    configuration = {
        "parameters": {"subdomain": "amberd-test-deploy"},
        "github": {"version": "26.06.03"},
    }
    with patch(
        "c2ai.services.registered_application_deployment."
        "dispatch_github_terminate_workflow",
        new_callable=AsyncMock,
    ) as dispatch_mock:
        reference = await dispatch_registered_application_termination(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000009"),
            instance_name="ada-code-repo-tier-1",
            tier=1,
            configuration=configuration,
            triggered_by="admin",
        )

    dispatch_mock.assert_awaited_once_with(
        "amberd-test-deploy",
        correlation_id="50000000-0000-0000-0000-000000000009",
        triggered_by="admin",
    )
    assert reference["subdomain"] == "amberd-test-deploy"


@pytest.mark.asyncio
async def test_dispatches_container_termination_with_dns_configuration():
    version = _container_version()
    configuration = {
        "container": {
            "registry": "Docker Hub",
            "image_repository": "company/billing-worker",
            "image_tag": "2.0.0",
        },
        "dns": {
            "subdomain": "billing-prod",
            "hostname": "billing-prod.amberd.ai",
            "managed_by": "athena",
        },
    }
    with (
        patch.dict(
            "os.environ",
            {
                "CONTAINER_TERMINATION_REPOSITORY": "platform/cleanup",
                "CONTAINER_TERMINATION_WORKFLOW": "containerized-app-terminate.yaml",
                "ATHENA_CALLBACK_BASE_URL": "https://athena.example.com/",
                "DEVOPS_BRANCH": "stable",
            },
        ),
        patch(
            "c2ai.services.registered_application_deployment.GitHubActionsClient"
        ) as client_class,
    ):
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        reference = await dispatch_registered_application_termination(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000005"),
            instance_name="billing-prod",
            tier=3,
            configuration=configuration,
            triggered_by="admin",
        )

    client_class.assert_called_once_with(repo_owner="platform", repo_name="cleanup")
    workflow, ref, inputs = client.trigger_workflow.await_args.args
    assert workflow == "containerized-app-terminate.yaml"
    assert ref == "stable"
    # The pipeline declares exactly these inputs and rejects anything else.
    assert inputs == {
        "triggered_by": "admin",
        "deployment_id": "50000000-0000-0000-0000-000000000005",
        "app_name": "billing-prod",
        "callback_base_url": "https://athena.example.com",
    }
    assert reference["pipeline"] == "container-termination"


@pytest.mark.asyncio
async def test_container_termination_defaults_to_amberd_devops_workflow():
    version = _container_version()
    with (
        patch.dict(
            "os.environ",
            {
                "ATHENA_CALLBACK_BASE_URL": "https://athena.amberd.ai",
                "DEVOPS_BRANCH": "main",
            },
            clear=True,
        ),
        patch(
            "c2ai.services.registered_application_deployment.GitHubActionsClient"
        ) as client_class,
    ):
        client = client_class.return_value
        client.trigger_workflow = AsyncMock(
            return_value={"trigger_method": "workflow_dispatch"}
        )
        await dispatch_registered_application_termination(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000005"),
            instance_name="billing-prod",
            tier=3,
            configuration={"dns": {"subdomain": "billing-prod"}},
            triggered_by="admin",
        )

    client_class.assert_called_once_with(
        repo_owner="amberd-ai",
        repo_name="devops",
    )
    workflow, ref, _inputs = client.trigger_workflow.await_args.args
    assert workflow == "containerized-app-terminate.yaml"
    assert ref == "main"


@pytest.mark.asyncio
async def test_dispatches_github_termination_to_predefined_amberd_workflow():
    version = _github_version()
    with patch(
        "c2ai.services.registered_application_deployment."
        "dispatch_github_terminate_workflow",
        new_callable=AsyncMock,
    ) as dispatch_mock:
        reference = await dispatch_registered_application_termination(
            version,
            deployment_id=UUID("50000000-0000-0000-0000-000000000007"),
            instance_name="release-prod",
            tier=2,
            configuration={"github": {"version": "2.0.0"}},
            triggered_by="admin",
        )

    dispatch_mock.assert_awaited_once_with(
        "release-prod",
        correlation_id="50000000-0000-0000-0000-000000000007",
        triggered_by="admin",
    )
    assert reference["workflow_id"] == "ada-terminate.yaml"
    assert reference["pipeline"] == "github-termination"


def test_container_env_vars_carry_llm_settings_without_any_parameters():
    from c2ai.services.registered_application_deployment import (
        build_container_pipeline_payload,
    )

    version = _container_version()
    version.parameters = []
    version.container_configuration.environment_variables = []
    configuration = build_container_deployment_configuration(
        version,
        ContainerRegisteredApplicationDeploymentCreate(
            instance_name="billing-tier-1",
            version="2.0.0",
        ),
        tier=1,
    )

    payload = build_container_pipeline_payload(
        configuration,
        deployment_id=UUID("50000000-0000-0000-0000-000000000007"),
        instance_name="billing-tier-1",
        tier=1,
        triggered_by="admin",
        registry_username=None,
        registry_token=None,
        llm_api_token="llm-token",
    )

    assert payload["env_vars"] == {
        "llm_endpoint": "https://amberd-llm-gateway.tier1.svc:8010",
        "llm_api_token": "llm-token",
        "llm_model_name": "qwen3-6",
    }


def test_rollback_of_an_old_snapshot_sends_only_lowercase_llm_keys():
    from c2ai.services.registered_application_deployment import (
        build_container_pipeline_payload,
    )

    configuration = {
        "llm": {
            "endpoint": "amberd-llm-gateway.tier2.svc:8010",
            "model_name": "qwen3-6",
        },
        "container": {
            "environment_variables": {
                "ENVIRONMENT": "test",
                "LLM_ENDPOINT": "amberd-llm-gateway.tier2.svc:8010",
                "LLM_MODEL_NAME": "qwen3-6",
            },
        },
    }

    payload = build_container_pipeline_payload(
        configuration,
        deployment_id=UUID("50000000-0000-0000-0000-000000000008"),
        instance_name="billing-tier-2",
        tier=2,
        triggered_by="admin",
        registry_username=None,
        registry_token=None,
        llm_api_token="llm-token",
    )

    assert payload["env_vars"] == {
        "ENVIRONMENT": "test",
        "llm_endpoint": "amberd-llm-gateway.tier2.svc:8010",
        "llm_api_token": "llm-token",
        "llm_model_name": "qwen3-6",
    }

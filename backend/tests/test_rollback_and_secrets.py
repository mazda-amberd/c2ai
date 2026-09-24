"""Rollback returns to the previous version; redeploys keep their credentials;
managed secrets reach container deployments as references only."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from c2ai.crud.registered_application import (
    ContainerRegistryRuntime,
    prepare_registered_application_rollback,
    prepare_registered_application_upgrade,
)
from c2ai.core.exceptions import DeploymentAlreadyAtVersion
from c2ai.models.registered_application import ContainerApplicationSecret
from c2ai.schemas.registered_application import ContainerRegisteredApplicationDeploymentCreate
from c2ai.services.registered_application_deployment import (
    build_container_deployment_configuration,
    build_container_pipeline_payload,
    configured_version,
)
from tests.test_api_registered_applications import (
    _persisted_container_version,
    _upgradeable_container_instance,
    _upgradeable_github_instance,
    registered_applications_admin_client,  # noqa: F401 - fixture
)
from tests.test_registered_application_tracking import _container_instance, _db

_CRUD = "c2ai.crud.registered_application.get_registered_application_deployment"
_API = "c2ai.api.registered_applications"


def _secret(name: str, env: str, reference: str | None) -> ContainerApplicationSecret:
    return ContainerApplicationSecret(
        id=UUID(int=len(name)),
        application_id=UUID("cccccccc-0000-0000-0000-000000000001"),
        name=name,
        environment_variable=env,
        secret_reference=reference,
        created_by="admin",
        updated_by="admin",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


class TestConfiguredVersion:
    def test_container_reads_image_tag(self):
        assert configured_version({"container": {"image_tag": "2.0"}}, "containerized") == "2.0"

    def test_github_prefers_upgrade_target_then_initial_branch(self):
        assert configured_version(
            {"github": {"version": "v2"}, "parameters": {"branch": "v1"}}, "github_workflow"
        ) == "v2"
        assert configured_version(
            {"github": {}, "parameters": {"branch": "v1"}}, "github_workflow"
        ) == "v1"
        assert configured_version({"github": {}}, "github_workflow") is None


class TestUpgradeKeepsPreviousConfiguration:
    async def test_running_upgrade_snapshots_configuration_it_replaces(self):
        instance = _container_instance(status="running")
        before = deepcopy(instance.configuration)
        with patch(_CRUD, new_callable=AsyncMock, return_value=instance):
            await prepare_registered_application_upgrade(
                _db(), instance.id, target_version="2.0.0", triggered_by="ops"
            )
        assert instance.previous_configuration == before
        assert instance.configuration["container"]["image_tag"] == "2.0.0"

    async def test_retrying_failed_upgrade_keeps_last_good_configuration(self):
        instance = _container_instance(status="failed")
        last_good = deepcopy(instance.configuration)
        instance.previous_configuration = last_good
        instance.configuration["container"]["image_tag"] = "2.0.0"  # the failed attempt
        with patch(_CRUD, new_callable=AsyncMock, return_value=instance):
            await prepare_registered_application_upgrade(
                _db(), instance.id, target_version="2.0.0", triggered_by="ops"
            )
        assert instance.previous_configuration == last_good

    async def test_github_initial_branch_counts_as_current_version(self):
        instance = _container_instance(status="running")
        instance.application.application_type = "github_workflow"
        instance.configuration = {"parameters": {"branch": "v1.4"}, "github": {"ref": "main"}}
        with (
            patch(_CRUD, new_callable=AsyncMock, return_value=instance),
            pytest.raises(DeploymentAlreadyAtVersion),
        ):
            await prepare_registered_application_upgrade(
                _db(), instance.id, target_version="v1.4", triggered_by="ops"
            )


class TestRollback:
    async def test_rollback_after_upgrade_restores_previous_version(self):
        instance = _container_instance(status="running")
        previous = deepcopy(instance.configuration)  # image 1.2.3
        instance.previous_configuration = previous
        instance.configuration["container"]["image_tag"] = "2.0.0"
        upgraded = deepcopy(instance.configuration)
        with patch(_CRUD, new_callable=AsyncMock, return_value=instance):
            result = await prepare_registered_application_rollback(
                _db(), instance.id, triggered_by="ops"
            )
        assert result.status == "updating"
        assert result.configuration["container"]["image_tag"] == "1.2.3"
        # Swapped, so a second rollback rolls forward again.
        assert result.previous_configuration == upgraded
        assert "to version '1.2.3'" in result.events[-1].message
        assert result.dns_status == "active"  # DNS is untouched by a version change

    def test_api_rollback_dispatches_upgrade_pipeline_to_previous_version(
        self, registered_applications_admin_client  # noqa: F811
    ):
        instance = _upgradeable_container_instance()
        instance.status = "updating"  # as prepared by the CRUD layer
        instance.rollback_count = 1
        with (
            patch(
                f"{_API}.crud_registered_application.prepare_registered_application_rollback",
                new_callable=AsyncMock,
                return_value=instance,
            ),
            patch(
                f"{_API}.dispatch_registered_application_upgrade",
                new_callable=AsyncMock,
                return_value={"operation": "upgrade", "rollback": True},
            ) as upgrade_mock,
            patch(
                f"{_API}.dispatch_registered_application_deployment", new_callable=AsyncMock
            ) as deploy_mock,
            patch(
                f"{_API}.crud_registered_application.complete_registered_application_upgrade_dispatch",
                new_callable=AsyncMock,
                return_value=instance,
            ) as complete_mock,
        ):
            response = registered_applications_admin_client.post(
                f"/api/registered-applications/deployments/{instance.id}/rollback"
            )
        assert response.status_code == 202
        deploy_mock.assert_not_awaited()
        assert upgrade_mock.await_args.kwargs["target_version"] == "1.2.3"
        assert upgrade_mock.await_args.kwargs["rollback"] is True
        assert "Rollback #1" in complete_mock.await_args.kwargs["event_message"]

    def test_container_redeploy_rollback_sends_registry_and_llm_credentials(
        self, registered_applications_admin_client  # noqa: F811
    ):
        # Previously a rollback redispatched without these, so the pipeline
        # received empty registry and LLM tokens.
        instance = _upgradeable_container_instance()
        instance.status = "deploying"
        instance.rollback_count = 1
        with (
            patch(
                f"{_API}.crud_registered_application.prepare_registered_application_rollback",
                new_callable=AsyncMock,
                return_value=instance,
            ),
            patch(
                f"{_API}.crud_registered_application.resolve_container_registry_credentials",
                new_callable=AsyncMock,
                return_value=ContainerRegistryRuntime(username="amberd", password="reg-pass"),
            ),
            patch(
                f"{_API}.crud_registered_application.resolve_llm_api_token",
                new_callable=AsyncMock,
                return_value="llm-token",
            ),
            patch(
                f"{_API}.dispatch_registered_application_deployment",
                new_callable=AsyncMock,
                return_value={"pipeline": "container"},
            ) as deploy_mock,
            patch(
                f"{_API}.crud_registered_application.complete_registered_application_dispatch",
                new_callable=AsyncMock,
                return_value=instance,
            ),
        ):
            response = registered_applications_admin_client.post(
                f"/api/registered-applications/deployments/{instance.id}/rollback"
            )
        assert response.status_code == 202
        kwargs = deploy_mock.await_args.kwargs
        assert kwargs["registry_username"] == "amberd"
        assert kwargs["registry_token"] == "reg-pass"
        assert kwargs["llm_api_token"] == "llm-token"
        assert "reg-pass" not in response.text

    def test_github_redeploy_rollback_uses_the_saved_connection(
        self, registered_applications_admin_client  # noqa: F811
    ):
        instance = _upgradeable_github_instance()
        instance.status = "deploying"
        instance.rollback_count = 1
        runtime = SimpleNamespace(token="gh-token", api_base_url="https://ghe.example/api/v3")
        with (
            patch(
                f"{_API}.crud_registered_application.prepare_registered_application_rollback",
                new_callable=AsyncMock,
                return_value=instance,
            ),
            patch(
                f"{_API}.crud_github_connection.resolve_github_connection",
                new_callable=AsyncMock,
                return_value=runtime,
            ),
            patch(
                f"{_API}.dispatch_registered_application_deployment",
                new_callable=AsyncMock,
                return_value={"trigger_method": "workflow_dispatch"},
            ) as deploy_mock,
            patch(
                f"{_API}.crud_registered_application.complete_registered_application_dispatch",
                new_callable=AsyncMock,
                return_value=instance,
            ),
        ):
            response = registered_applications_admin_client.post(
                f"/api/registered-applications/deployments/{instance.id}/rollback"
            )
        assert response.status_code == 202
        assert deploy_mock.await_args.kwargs["github_token"] == "gh-token"
        assert deploy_mock.await_args.kwargs["github_api_base_url"] == "https://ghe.example/api/v3"


class TestManagedSecrets:
    def test_container_configuration_carries_references_not_values(self):
        configuration = build_container_deployment_configuration(
            _persisted_container_version(),
            ContainerRegisteredApplicationDeploymentCreate(
                instance_name="chat-prod", version="2.0.0"
            ),
            tier=1,
            managed_secrets=[
                _secret("api-key", "API_KEY", "vault://chat/api-key"),
                _secret("pending", "PENDING", None),  # provider write never completed
            ],
        )
        assert configuration["managed_secrets"] == [
            {
                "id": str(UUID(int=7)),
                "name": "api-key",
                "environment_variable": "API_KEY",
                "reference": "vault://chat/api-key",
            }
        ]

    def test_pipeline_payload_forwards_secret_references(self):
        configuration = build_container_deployment_configuration(
            _persisted_container_version(),
            ContainerRegisteredApplicationDeploymentCreate(
                instance_name="chat-prod", version="2.0.0"
            ),
            tier=1,
            managed_secrets=[_secret("api-key", "API_KEY", "vault://chat/api-key")],
        )
        payload = build_container_pipeline_payload(
            configuration,
            deployment_id=UUID(int=1),
            instance_name="chat-prod",
            tier=1,
            triggered_by="ops",
            registry_username=None,
            registry_token=None,
            llm_api_token=None,
        )
        assert payload["managed_secrets"] == [
            {
                "name": "api-key",
                "environment_variable": "API_KEY",
                "reference": "vault://chat/api-key",
            }
        ]

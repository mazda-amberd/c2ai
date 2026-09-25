"""Rollback returns to the previous version; redeploys keep their credentials;
managed secrets reach container deployments as references only."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from c2ai.core.exceptions import DeploymentAlreadyAtVersion
from c2ai.deployments.configuration import (
    build_container_deployment_configuration,
    configured_version,
)
from c2ai.deployments.pipelines import (
    build_container_pipeline_payload,
)
from c2ai.deployments.repository import (
    prepare_registered_application_rollback,
    prepare_registered_application_upgrade,
)
from c2ai.models.registered_application import ContainerApplicationSecret
from c2ai.schemas.registered_application import ContainerRegisteredApplicationDeploymentCreate
from tests.test_api_registered_applications import (
    _persisted_container_version,
    registered_applications_admin_client,  # noqa: F401 - fixture
)
from tests.test_registered_application_tracking import _container_instance, _db

_CRUD = "c2ai.deployments.repository.get_registered_application_deployment"
_API = "c2ai.api.registered_applications.deployments"


def _secret(name: str, env: str, reference: str | None) -> ContainerApplicationSecret:
    return ContainerApplicationSecret(
        id=UUID(int=len(name)),
        application_id=UUID("cccccccc-0000-0000-0000-000000000001"),
        name=name,
        environment_variable=env,
        secret_reference=reference,
        created_by="admin",
        updated_by="admin",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
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

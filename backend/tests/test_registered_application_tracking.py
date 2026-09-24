"""Tests for durable registered-deployment progress and rollback state."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from c2ai.core.exceptions import (
    DeploymentAlreadyAtVersion,
    DeploymentProgressConflict,
    DeploymentRollbackNotAvailable,
    DeploymentTerminationNotAvailable,
    DeploymentTerminationNotSupported,
    DeploymentUpgradeNotAvailable,
    DeploymentUpgradeNotSupported,
    DuplicateDeploymentSubdomain,
)
from c2ai.crud.registered_application import (
    complete_registered_application_dispatch,
    complete_registered_application_termination_dispatch,
    complete_registered_application_upgrade_dispatch,
    create_registered_application_deployment,
    prepare_registered_application_rollback,
    prepare_registered_application_termination,
    prepare_registered_application_upgrade,
    update_registered_application_deployment_progress,
)
from c2ai.models.registered_application import (
    DeploymentInstance,
    RegisteredApplication,
    RegisteredApplicationVersion,
)
from c2ai.schemas.registered_application import (
    RegisteredApplicationDeploymentProgressUpdate,
)
from tests.helpers import mock_db


def _instance(
    *,
    status: str = "deploying",
    current_step: str = "validating_configuration",
) -> DeploymentInstance:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
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
    application.versions.append(version)
    return DeploymentInstance(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        application=application,
        application_version=version,
        instance_name="release-prod",
        tier=2,
        status=status,
        current_step=current_step,
        configuration={"parameters": {"environment": "production"}},
        triggered_by="admin",
        rollback_count=0,
        created_at=now,
        updated_at=now,
    )


def _db():
    return mock_db()


@pytest.mark.asyncio
async def test_complete_deployment_dispatch_refreshes_server_timestamp_before_commit():
    db = _db()
    instance = _instance(status="pending")

    result = await complete_registered_application_dispatch(
        db,
        instance,
        {"trigger_method": "workflow_dispatch", "workflow_id": "ada-deploy.yaml"},
    )

    assert result.status == "deploying"
    assert result.dispatch_reference["workflow_id"] == "ada-deploy.yaml"
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(instance, attribute_names=["updated_at"])
    db.commit.assert_awaited_once()


def _container_instance(*, status: str = "running") -> DeploymentInstance:
    instance = _instance(status=status, current_step="completed")
    instance.application.application_type = "containerized"
    instance.configuration = {
        "parameters": {"replicas": 3},
        "secrets": [],
        "container": {
            "registry": "Docker Hub",
            "image_repository": "amberd/chat-service",
            "image_tag": "1.2.3",
            "container_port": 8080,
            "replica_count": 3,
            "environment_variables": {"LOG_LEVEL": "info"},
            "persistent_volume_size": "10Gi",
            "service_type": "Ingress",
            "host": "release-prod.amberd.ai",
            "tls_issuer": "letsencrypt",
            "image_pull_secret": "dockerhub-credential",
        },
        "dns": {
            "subdomain": "release-prod",
            "hostname": "release-prod.amberd.ai",
            "managed_by": "athena",
        },
        "managed_secrets": [{"id": "managed-secret-1", "reference": "vault://one"}],
    }
    instance.subdomain = "release-prod"
    instance.hostname = "release-prod.amberd.ai"
    instance.dns_status = "active"
    return instance


def test_progress_contract_requires_consistent_terminal_states():
    with pytest.raises(ValueError, match="failure_reason"):
        RegisteredApplicationDeploymentProgressUpdate(
            current_step="failed",
            status="failed",
        )
    with pytest.raises(ValueError, match="completed step"):
        RegisteredApplicationDeploymentProgressUpdate(
            current_step="completed",
            status="deploying",
        )
    with pytest.raises(ValueError, match="completed step"):
        RegisteredApplicationDeploymentProgressUpdate(
            current_step="verifying_deployment",
            status="terminated",
        )


@pytest.mark.asyncio
async def test_progress_update_persists_stage_and_event():
    db = _db()
    instance = _instance()
    payload = RegisteredApplicationDeploymentProgressUpdate(
        current_step="creating_namespace",
        status="deploying",
        message="Namespace tier2 created.",
    )
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await update_registered_application_deployment_progress(
            db,
            instance.id,
            payload,
        )

    assert result.current_step == "creating_namespace"
    assert result.status == "deploying"
    assert result.events[-1].message == "Namespace tier2 created."
    assert result.events[-1].created_by == "deployment-pipeline"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_progress_update_is_idempotent_and_rejects_backward_steps():
    db = _db()
    instance = _instance(current_step="waiting_for_rollout")
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        same = RegisteredApplicationDeploymentProgressUpdate(
            current_step="waiting_for_rollout",
            status="deploying",
        )
        await update_registered_application_deployment_progress(db, instance.id, same)
        assert instance.events == []

        backwards = RegisteredApplicationDeploymentProgressUpdate(
            current_step="creating_namespace",
            status="deploying",
        )
        with pytest.raises(DeploymentProgressConflict):
            await update_registered_application_deployment_progress(
                db,
                instance.id,
                backwards,
            )

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_progress_records_reason_and_completion_time():
    db = _db()
    instance = _instance(current_step="waiting_for_rollout")
    payload = RegisteredApplicationDeploymentProgressUpdate(
        current_step="failed",
        status="failed",
        message="Rollout timed out.",
        failure_reason="Deployment did not become ready within 10 minutes.",
    )
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(db, instance.id, payload)

    assert instance.status == "failed"
    assert instance.current_step == "failed"
    assert instance.completed_at is not None
    assert "10 minutes" in instance.failure_reason


@pytest.mark.asyncio
async def test_rollback_reopens_failed_instance_without_erasing_history():
    db = _db()
    instance = _instance(status="failed", current_step="failed")
    instance.failure_reason = "Rollout failed"
    prior_event_count = len(instance.events)
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await prepare_registered_application_rollback(
            db,
            instance.id,
            triggered_by="operator",
        )

    assert result.status == "deploying"
    assert result.current_step == "validating_configuration"
    assert result.failure_reason is None
    assert result.rollback_count == 1
    assert len(result.events) == prior_event_count + 1
    assert result.events[-1].created_by == "operator"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_rejects_an_active_deployment():
    db = _db()
    instance = _instance(status="deploying")
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        with pytest.raises(DeploymentRollbackNotAvailable):
            await prepare_registered_application_rollback(
                db,
                instance.id,
                triggered_by="operator",
            )

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_container_upgrade_changes_only_image_tag_and_preserves_history():
    db = _db()
    instance = _container_instance()
    previous_configuration = deepcopy(instance.configuration)
    previous_event_count = len(instance.events)
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await prepare_registered_application_upgrade(
            db,
            instance.id,
            target_version="2.0.0",
            triggered_by="operator",
        )

    expected_configuration = deepcopy(previous_configuration)
    expected_configuration["container"]["image_tag"] = "2.0.0"
    assert result.configuration == expected_configuration
    assert previous_configuration["container"]["image_tag"] == "1.2.3"
    assert result.status == "updating"
    assert result.current_step == "validating_configuration"
    assert result.dns_status == "active"
    assert len(result.events) == previous_event_count + 1
    assert "2.0.0" in result.events[-1].message
    assert result.events[-1].created_by == "operator"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_container_upgrade_dispatch_commits_reference_and_event():
    db = _db()
    instance = _container_instance(status="updating")
    instance.configuration["container"]["image_tag"] = "2.0.0"

    result = await complete_registered_application_upgrade_dispatch(
        db,
        instance,
        {"pipeline": "container-upgrade", "workflow_id": "container-update.yml"},
    )

    assert result.status == "updating"
    assert result.dispatch_reference["pipeline"] == "container-upgrade"
    assert "2.0.0" in result.events[-1].message
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upgrade_rejects_same_version_unsupported_type_and_active_state():
    db = _db()
    instance = _container_instance()
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        with pytest.raises(DeploymentAlreadyAtVersion):
            await prepare_registered_application_upgrade(
                db,
                instance.id,
                target_version="1.2.3",
                triggered_by="operator",
            )

        instance.application.application_type = "unsupported"
        with pytest.raises(DeploymentUpgradeNotSupported):
            await prepare_registered_application_upgrade(
                db,
                instance.id,
                target_version="2.0.0",
                triggered_by="operator",
            )

        instance.application.application_type = "containerized"
        instance.status = "updating"
        with pytest.raises(DeploymentUpgradeNotAvailable):
            await prepare_registered_application_upgrade(
                db,
                instance.id,
                target_version="2.0.0",
                triggered_by="operator",
            )


@pytest.mark.asyncio
async def test_prepare_github_upgrade_stores_version_without_changing_workflow_config():
    db = _db()
    instance = _instance(status="running", current_step="completed")
    instance.configuration = {
        "parameters": {"environment": "production"},
        "secrets": [{"key": "API_TOKEN", "reference": "vault://release/token"}],
        "github": {
            "connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/release-workflow",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
        },
    }
    previous_configuration = deepcopy(instance.configuration)
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await prepare_registered_application_upgrade(
            db,
            instance.id,
            target_version="2.0.0",
            triggered_by="operator",
        )

    expected = deepcopy(previous_configuration)
    expected["github"]["version"] = "2.0.0"
    assert result.configuration == expected
    assert "version" not in previous_configuration["github"]
    assert result.status == "updating"
    assert result.events[-1].status == "updating"

    completed = await complete_registered_application_upgrade_dispatch(
        db,
        result,
        {"pipeline": "github-upgrade"},
    )
    assert completed.dispatch_reference["pipeline"] == "github-upgrade"
    assert "2.0.0" in completed.events[-1].message


@pytest.mark.asyncio
async def test_prepare_github_upgrade_retries_failed_instance_with_same_version():
    db = _db()
    instance = _instance(status="failed", current_step="failed")
    instance.failure_reason = "GitHub Actions job 'update' failed."
    instance.configuration = {
        "parameters": {"environment": "production"},
        "secrets": [],
        "github": {
            "connection": "github-app-1",
            "trigger_method": "workflow_dispatch",
            "repository": "amberd-ai/release-workflow",
            "workflow_file_path": ".github/workflows/deploy.yml",
            "ref": "main",
            "version": "dev",
        },
    }
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await prepare_registered_application_upgrade(
            db,
            instance.id,
            target_version="dev",
            triggered_by="operator",
        )

    assert result.configuration["github"]["version"] == "dev"
    assert result.status == "updating"
    assert result.current_step == "validating_configuration"
    assert result.failure_reason is None
    assert "dev" in result.events[-1].message


@pytest.mark.asyncio
async def test_upgrade_progress_keeps_updating_status_and_does_not_reconfigure_dns():
    db = _db()
    instance = _container_instance(status="updating")
    instance.current_step = "validating_configuration"
    instance.configuration["container"]["image_tag"] = "2.0.0"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="waiting_for_rollout",
                status="updating",
                message="Waiting for upgraded workload.",
            ),
        )
        assert instance.status == "updating"
        assert instance.dns_status == "active"

        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="completed",
                status="running",
                message="Upgrade completed.",
            ),
        )

    assert instance.status == "running"
    assert instance.current_step == "completed"
    assert instance.dns_status == "active"


@pytest.mark.asyncio
async def test_failed_upgrade_records_reason_without_marking_dns_failed():
    db = _db()
    instance = _container_instance(status="updating")
    instance.current_step = "waiting_for_rollout"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="failed",
                status="failed",
                message="Upgrade rollout failed.",
                failure_reason="New image did not become ready.",
            ),
        )

    assert instance.status == "failed"
    assert instance.current_step == "failed"
    assert instance.failure_reason == "New image did not become ready."
    assert instance.dns_status == "active"
    assert instance.events[-1].failure_reason == "New image did not become ready."


@pytest.mark.asyncio
async def test_prepare_container_termination_preserves_configuration_and_history():
    db = _db()
    instance = _container_instance()
    previous_configuration = deepcopy(instance.configuration)
    previous_event_count = len(instance.events)
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        result = await prepare_registered_application_termination(
            db,
            instance.id,
            triggered_by="operator",
        )

    assert result.configuration == previous_configuration
    assert result.status == "terminating"
    assert result.current_step == "validating_configuration"
    assert result.dns_status == "active"
    assert len(result.events) == previous_event_count + 1
    assert result.events[-1].status == "terminating"
    assert result.events[-1].created_by == "operator"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_container_termination_dispatch_commits_reference_and_event():
    db = _db()
    instance = _container_instance(status="terminating")

    result = await complete_registered_application_termination_dispatch(
        db,
        instance,
        {"pipeline": "container-termination"},
    )

    assert result.status == "terminating"
    assert result.dispatch_reference["pipeline"] == "container-termination"
    assert result.events[-1].status == "terminating"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_termination_rejects_unsupported_type_and_active_operation():
    db = _db()
    instance = _container_instance()
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        instance.application.application_type = "unsupported"
        with pytest.raises(DeploymentTerminationNotSupported):
            await prepare_registered_application_termination(
                db,
                instance.id,
                triggered_by="operator",
            )

        instance.application.application_type = "containerized"
        instance.status = "updating"
        with pytest.raises(DeploymentTerminationNotAvailable):
            await prepare_registered_application_termination(
                db,
                instance.id,
                triggered_by="operator",
            )


@pytest.mark.asyncio
async def test_termination_progress_deletes_dns_and_records_terminated_time():
    db = _db()
    instance = _container_instance(status="terminating")
    instance.current_step = "validating_configuration"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="applying_resources",
                status="terminating",
                message="Deleting Kubernetes resources.",
            ),
        )
        assert instance.status == "terminating"

        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="configuring_dns",
                status="terminating",
                message="Deleting DNS record.",
            ),
        )
        assert instance.dns_status == "deleting"

        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="completed",
                status="terminated",
                message="Resources and DNS record deleted.",
            ),
        )

    assert instance.status == "terminated"
    assert instance.current_step == "completed"
    assert instance.dns_status == "deleted"
    assert instance.terminated_at is not None
    assert instance.completed_at == instance.terminated_at


@pytest.mark.asyncio
async def test_failed_termination_records_failure_and_dns_state():
    db = _db()
    instance = _container_instance(status="terminating")
    instance.current_step = "configuring_dns"
    instance.dns_status = "deleting"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="failed",
                status="failed",
                message="DNS cleanup failed.",
                failure_reason="Provider rejected deletion.",
            ),
        )

    assert instance.status == "failed"
    assert instance.dns_status == "failed"
    assert instance.terminated_at is None
    assert instance.events[-1].failure_reason == "Provider rejected deletion."


@pytest.mark.asyncio
async def test_github_termination_completes_without_dns_stage():
    db = _db()
    instance = _instance(status="running", current_step="completed")
    instance.configuration = {
        "parameters": {"environment": "production"},
        "github": {
            "repository": "amberd-ai/release-workflow",
            "ref": "main",
        },
    }
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await prepare_registered_application_termination(
            db,
            instance.id,
            triggered_by="operator",
        )
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="verifying_deployment",
                status="terminating",
                message="Verifying workflow cleanup.",
            ),
        )
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="completed",
                status="terminated",
                message="GitHub workflow termination completed.",
            ),
        )

    assert instance.status == "terminated"
    assert instance.subdomain is None
    assert instance.dns_status is None
    assert instance.terminated_at is not None


@pytest.mark.asyncio
async def test_container_dns_progress_becomes_active_on_completion():
    db = _db()
    instance = _instance(current_step="verifying_deployment")
    instance.subdomain = "release-prod"
    instance.hostname = "release-prod.amberd.ai"
    instance.dns_status = "pending"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="configuring_dns",
                status="deploying",
                message="Creating DNS record.",
            ),
        )
        assert instance.dns_status == "configuring"

        await update_registered_application_deployment_progress(
            db,
            instance.id,
            RegisteredApplicationDeploymentProgressUpdate(
                current_step="completed",
                status="running",
                message="Deployment and DNS configuration completed.",
            ),
        )

    assert instance.dns_status == "active"
    assert instance.status == "running"


@pytest.mark.asyncio
async def test_github_deployment_rejects_dns_progress():
    db = _db()
    instance = _instance(current_step="verifying_deployment")
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        with pytest.raises(DeploymentProgressConflict, match="containerized"):
            await update_registered_application_deployment_progress(
                db,
                instance.id,
                RegisteredApplicationDeploymentProgressUpdate(
                    current_step="configuring_dns",
                    status="deploying",
                ),
            )


@pytest.mark.asyncio
async def test_container_deployment_cannot_complete_before_dns_stage():
    db = _db()
    instance = _instance(current_step="verifying_deployment")
    instance.subdomain = "release-prod"
    instance.hostname = "release-prod.amberd.ai"
    instance.dns_status = "pending"
    with patch(
        "c2ai.crud.registered_application."
        "get_registered_application_deployment",
        new_callable=AsyncMock,
        return_value=instance,
    ):
        with pytest.raises(DeploymentProgressConflict, match="configure DNS"):
            await update_registered_application_deployment_progress(
                db,
                instance.id,
                RegisteredApplicationDeploymentProgressUpdate(
                    current_step="completed",
                    status="running",
                ),
            )

    assert instance.dns_status == "pending"
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_container_deployment_persists_dns_identity():
    db = _db()
    version = _instance().application_version
    version.application.application_type = "containerized"
    deployment = await create_registered_application_deployment(
        db,
        version,
        instance_name="release-prod",
        tier=2,
        configuration={
            "parameters": {},
            "secrets": [],
            "container": {"image_tag": "1.0.0"},
            "dns": {
                "subdomain": "release-prod",
                "hostname": "release-prod.amberd.ai",
                "managed_by": "athena",
            },
        },
        triggered_by="admin",
    )

    assert deployment.subdomain == "release-prod"
    assert deployment.hostname == "release-prod.amberd.ai"
    assert deployment.dns_status == "pending"


class _SubdomainDiagnostic:
    constraint_name = "uq_deployment_instances_dns_subdomain_active"


class _SubdomainDatabaseError(Exception):
    diag = _SubdomainDiagnostic()


@pytest.mark.asyncio
async def test_create_container_deployment_rejects_duplicate_subdomain():
    db = _db()
    db.flush.side_effect = IntegrityError(
        "INSERT INTO deployment_instances",
        {},
        _SubdomainDatabaseError(),
    )
    version = _instance().application_version
    version.application.application_type = "containerized"
    with pytest.raises(DuplicateDeploymentSubdomain, match="already in use"):
        await create_registered_application_deployment(
            db,
            version,
            instance_name="release-prod",
            tier=2,
            configuration={
                "dns": {
                    "subdomain": "release-prod",
                    "hostname": "release-prod.amberd.ai",
                }
            },
            triggered_by="admin",
        )

    db.rollback.assert_awaited_once()

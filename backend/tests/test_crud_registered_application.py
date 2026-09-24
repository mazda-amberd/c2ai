"""Tests for registered-application persistence operations."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from c2ai.models.registered_application import (
    ContainerApplicationSecret,
    RegisteredApplication,
)
from c2ai.crud.registered_application import (
    complete_container_application_secret_write,
    create_container_registered_application,
    create_github_registered_application,
    delete_registered_application,
    get_registered_application_by_name,
    list_container_application_secrets,
    list_registered_applications,
    prepare_container_application_secret_create,
    prepare_container_application_secret_delete,
    resolve_container_registry_credentials,
)
from c2ai.core.exceptions import (
    ContainerApplicationSecretInUse,
    ContainerSecretsNotSupported,
    DuplicateContainerApplicationSecret,
    DuplicateRegisteredApplication,
    RegisteredApplicationHasManagedSecrets,
    RegisteredApplicationHasRunningInstances,
    RegisteredApplicationNotFound,
    ServiceUnavailableError,
)
from c2ai.schemas.registered_application import (
    ContainerApplicationSecretCreate,
    ContainerRegisteredApplicationCreate,
    GitHubRegisteredApplicationCreate,
)


def _payload() -> GitHubRegisteredApplicationCreate:
    return GitHubRegisteredApplicationCreate.model_validate(
        {
            "application_type": "github_workflow",
            "name": "example-chatbot",
            "description": "Customer support workflow",
            "github": {
                "github_connection": "github-app-1",
                "trigger_method": "workflow_dispatch",
                "repository": "amberd-ai/example-chatbot",
                "code_repository": "amberd-ai/application",
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
    )


def _container_payload() -> ContainerRegisteredApplicationCreate:
    return ContainerRegisteredApplicationCreate.model_validate(
        {
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
                "cpu_request": "500m",
                "memory_request": "512Mi",
                "scaling": "3",
                "storage": "10Gi",
            },
            "parameters": [{"key": "LOG_LEVEL", "value": "info"}],
            "llm": {
                "endpoint": "https://llm.example.com/v1",
                "api_token": "write-only-llm-token",
                "model_name": "qwen3-6",
            },
        }
    )


def _db_with_name_lookup(result_value=None):
    db = MagicMock()
    lookup_result = MagicMock()
    lookup_result.scalar_one_or_none.return_value = result_value
    db.execute = AsyncMock(return_value=lookup_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_create_github_application_persists_complete_version_graph(monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    db = _db_with_name_lookup()

    version = await create_github_registered_application(
        db,
        _payload(),
        created_by="admin-user",
    )

    application = db.add.call_args.args[0]
    assert isinstance(application, RegisteredApplication)
    assert application.name == "example-chatbot"
    assert application.application_type == "github_workflow"
    assert application.current_version == 1
    assert application.created_by == "admin-user"
    assert application.versions == [version]

    assert version.version == 1
    assert version.description == "Customer support workflow"
    assert version.github_configuration.repository == "amberd-ai/example-chatbot"
    assert version.github_configuration.code_repository == "amberd-ai/application"
    assert version.github_configuration.trigger_method == "workflow_dispatch"
    assert version.parameters[0].position == 0
    assert version.parameters[0].parameter_type == "text"
    assert version.parameters[0].required is True
    assert version.parameters[0].default_value is None
    assert version.parameters[0].options == []
    assert version.secret_references == []
    assert version.llm_configuration.endpoint == "https://llm.example.com/v1"
    assert version.llm_configuration.model_name == "qwen3-coder-next"
    assert "write-only-token" not in str(
        version.llm_configuration.api_token_encrypted
    )

    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_github_application_rejects_existing_name_case_insensitively():
    db = _db_with_name_lookup(RegisteredApplication(name="Example-Chatbot"))

    with pytest.raises(DuplicateRegisteredApplication):
        await create_github_registered_application(
            db,
            _payload(),
            created_by="admin-user",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


class _UniqueNameDiagnostic:
    constraint_name = "uq_registered_applications_name_ci"


class _UniqueNameDatabaseError(Exception):
    diag = _UniqueNameDiagnostic()


@pytest.mark.asyncio
async def test_create_github_application_maps_concurrent_name_conflict(monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    db = _db_with_name_lookup()
    db.flush.side_effect = IntegrityError(
        "INSERT INTO registered_applications",
        {},
        _UniqueNameDatabaseError(),
    )

    with pytest.raises(DuplicateRegisteredApplication):
        await create_github_registered_application(
            db,
            _payload(),
            created_by="admin-user",
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_github_application_requires_llm_encryption_key(monkeypatch):
    monkeypatch.delenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    db = _db_with_name_lookup()

    with pytest.raises(
        ServiceUnavailableError,
        match="Credential storage is not configured",
    ):
        await create_github_registered_application(
            db,
            _payload(),
            created_by="admin-user",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_container_application_persists_complete_version_graph(monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    db = _db_with_name_lookup()

    version = await create_container_registered_application(
        db,
        _container_payload(),
        created_by="admin-user",
    )

    application = db.add.call_args.args[0]
    configuration = version.container_configuration
    assert application.application_type == "containerized"
    assert application.current_version == 1
    assert application.versions == [version]
    assert configuration.registry == "Docker Hub"
    assert configuration.registry_credential_id is None
    assert configuration.registry_username == "amberd"
    assert "write-only-registry-password" not in str(
        configuration.registry_password_encrypted
    )
    assert configuration.image_repository == "amberd/chat-service"
    assert configuration.default_image_tag == "1.2.3"
    assert configuration.image_pull_policy == "IfNotPresent"
    assert configuration.container_port == 8080
    assert configuration.expose_public_service is True
    assert configuration.cpu_request == "500m"
    assert configuration.memory_request == "512Mi"
    assert configuration.scaling == "3"
    assert configuration.storage == "10Gi"
    assert configuration.environment_variables == []
    assert version.parameters[0].key == "LOG_LEVEL"
    assert version.parameters[0].parameter_type == "text"
    assert version.parameters[0].required is True
    assert version.secret_references == []
    assert version.llm_configuration.endpoint == "https://llm.example.com/v1"
    assert version.llm_configuration.model_name == "qwen3-6"
    assert "write-only-llm-token" not in str(
        version.llm_configuration.api_token_encrypted
    )
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_container_application_requires_credential_encryption_key(
    monkeypatch,
):
    monkeypatch.delenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    db = _db_with_name_lookup()

    with pytest.raises(
        ServiceUnavailableError,
        match="Credential storage is not configured",
    ):
        await create_container_registered_application(
            db,
            _container_payload(),
            created_by="admin-user",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_container_registry_credentials_decrypts_runtime_values(
    monkeypatch,
):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    result = MagicMock()
    result.one_or_none.return_value = ("amberd", "write-only-registry-password")
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    runtime = await resolve_container_registry_credentials(
        db,
        UUID("dddddddd-0000-0000-0000-000000000001"),
    )

    assert runtime is not None
    assert runtime.username == "amberd"
    assert runtime.password == "write-only-registry-password"


@pytest.mark.asyncio
async def test_create_container_application_rejects_existing_name():
    db = _db_with_name_lookup(RegisteredApplication(name="chat-service"))

    with pytest.raises(DuplicateRegisteredApplication):
        await create_container_registered_application(
            db,
            _container_payload(),
            created_by="admin-user",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def _catalog_application(identifier: str, name: str) -> RegisteredApplication:
    created_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    return RegisteredApplication(
        id=UUID(identifier),
        name=name,
        application_type="github_workflow",
        status="active",
        current_version=1,
        created_by="admin-user",
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_list_registered_applications_returns_active_tier_aggregates():
    first = _catalog_application(
        "aaaaaaaa-0000-0000-0000-000000000010", "example-chatbot"
    )
    second = _catalog_application(
        "bbbbbbbb-0000-0000-0000-000000000010", "nightly-reports"
    )

    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    rows_result = MagicMock()
    rows_result.all.return_value = [
        (first, "Customer support", 3, 2),
        (second, "Nightly reports", 0, 0),
    ]
    tier_result = MagicMock()
    tier_result.all.return_value = [
        (first.id, 1, 2),
        (first.id, 3, 1),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result, tier_result])

    page = await list_registered_applications(
        db,
        search="chat",
        application_type="github_workflow",
        application_status="active",
        tier=1,
        sort_by="instances",
        sort_order="desc",
        offset=0,
        limit=20,
    )

    assert page.total == 2
    assert len(page.items) == 2
    assert page.items[0].total_deployed_instances == 3
    assert page.items[0].tiers_deployed_to == {1: 2, 3: 1}
    assert page.items[1].tiers_deployed_to == {}
    assert db.execute.await_count == 3
    compiled_statements = [
        str(call.args[0].compile(dialect=postgresql.dialect()))
        for call in db.execute.await_args_list
    ]
    assert "EXISTS" in compiled_statements[0]
    assert "deployment_instances" in compiled_statements[1]
    assert "GROUP BY" in compiled_statements[2]


@pytest.mark.asyncio
async def test_list_registered_applications_skips_tier_query_for_empty_page():
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    rows_result = MagicMock()
    rows_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result])

    page = await list_registered_applications(db)

    assert page.total == 0
    assert page.items == []
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_delete_registered_application_soft_deletes_when_no_active_instances():
    application = _catalog_application(
        "cccccccc-0000-0000-0000-000000000010", "chat-service"
    )
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    instance_result = MagicMock()
    instance_result.scalar_one.return_value = 0
    secret_result = MagicMock()
    secret_result.scalar_one.return_value = 0
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[application_result, instance_result, secret_result]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    await delete_registered_application(
        db,
        application.id,
        deleted_by="admin-user",
    )

    assert application.deleted_at is not None
    assert application.updated_by == "admin-user"
    db.add.assert_called_once_with(application)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_registered_application_blocks_active_instances():
    application = _catalog_application(
        "dddddddd-0000-0000-0000-000000000010", "chat-service"
    )
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    instance_result = MagicMock()
    instance_result.scalar_one.return_value = 2
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[application_result, instance_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with pytest.raises(RegisteredApplicationHasRunningInstances) as exc_info:
        await delete_registered_application(
            db,
            application.id,
            deleted_by="admin-user",
        )

    assert "2 active deployment" in exc_info.value.detail
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_registered_application_blocks_managed_secrets():
    application = _catalog_application(
        "abababab-0000-0000-0000-000000000010", "chat-service"
    )
    application.application_type = "containerized"
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    instance_result = MagicMock()
    instance_result.scalar_one.return_value = 0
    secret_result = MagicMock()
    secret_result.scalar_one.return_value = 2
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[application_result, instance_result, secret_result]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with pytest.raises(RegisteredApplicationHasManagedSecrets, match="Delete those"):
        await delete_registered_application(
            db,
            application.id,
            deleted_by="admin-user",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_registered_application_returns_not_found_for_deleted_record():
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=application_result)
    db.rollback = AsyncMock()

    with pytest.raises(RegisteredApplicationNotFound):
        await delete_registered_application(
            db,
            UUID("eeeeeeee-0000-0000-0000-000000000010"),
            deleted_by="admin-user",
        )

    db.rollback.assert_awaited_once()


def _container_application() -> RegisteredApplication:
    return RegisteredApplication(
        id=UUID("cccccccc-0000-0000-0000-000000000099"),
        name="managed-chat-service",
        application_type="containerized",
        status="active",
        current_version=1,
        created_by="admin-user",
    )


@pytest.mark.asyncio
async def test_prepare_and_complete_container_secret_store_metadata_only():
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = _container_application()
    db = MagicMock()
    db.execute = AsyncMock(return_value=application_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    payload = ContainerApplicationSecretCreate(
        name="chatbot-api-key",
        environment_variable="CHATBOT_API_KEY",
        secret_value="super-sensitive",
    )

    secret = await prepare_container_application_secret_create(
        db,
        _container_application().id,
        payload,
        created_by="admin-user",
    )
    await complete_container_application_secret_write(
        db,
        secret,
        reference="vault://athena/chatbot/api-key",
        updated_by="admin-user",
    )

    assert isinstance(secret, ContainerApplicationSecret)
    assert secret.name == "chatbot-api-key"
    assert secret.environment_variable == "CHATBOT_API_KEY"
    assert secret.secret_reference == "vault://athena/chatbot/api-key"
    assert not hasattr(secret, "secret_value")
    assert not hasattr(secret, "value")
    assert "super-sensitive" not in repr(secret.__dict__)
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_container_secret_crud_rejects_github_applications():
    application = _container_application()
    application.application_type = "github_workflow"
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    db = MagicMock()
    db.execute = AsyncMock(return_value=application_result)

    with pytest.raises(ContainerSecretsNotSupported):
        await list_container_application_secrets(db, application.id)


class _SecretUniqueDiagnostic:
    constraint_name = "uq_container_application_secrets_env_active"


class _SecretUniqueDatabaseError(Exception):
    diag = _SecretUniqueDiagnostic()


@pytest.mark.asyncio
async def test_container_secret_maps_concurrent_metadata_conflict():
    application = _container_application()
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    db = MagicMock()
    db.execute = AsyncMock(return_value=application_result)
    db.add = MagicMock()
    db.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT INTO container_application_secrets",
            {},
            _SecretUniqueDatabaseError(),
        )
    )
    db.rollback = AsyncMock()

    with pytest.raises(DuplicateContainerApplicationSecret):
        await prepare_container_application_secret_create(
            db,
            application.id,
            ContainerApplicationSecretCreate(
                name="chatbot-api-key",
                environment_variable="CHATBOT_API_KEY",
                secret_value="super-sensitive",
            ),
            created_by="admin-user",
        )

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_container_secret_delete_is_guarded_while_deployment_references_it():
    application = _container_application()
    secret = ContainerApplicationSecret(
        id=UUID("99999999-0000-0000-0000-000000000099"),
        application_id=application.id,
        name="chatbot-api-key",
        environment_variable="CHATBOT_API_KEY",
        secret_reference="vault://athena/chatbot/api-key",
        created_by="admin-user",
        updated_by="admin-user",
    )
    application_result = MagicMock()
    application_result.scalar_one_or_none.return_value = application
    secret_result = MagicMock()
    secret_result.scalar_one_or_none.return_value = secret
    reference_result = MagicMock()
    reference_result.scalar_one.return_value = 1
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[application_result, secret_result, reference_result]
    )
    db.rollback = AsyncMock()

    with pytest.raises(ContainerApplicationSecretInUse, match="rollback-capable"):
        await prepare_container_application_secret_delete(
            db,
            application.id,
            secret.id,
        )

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_name_lookup_ignores_soft_deleted_applications():
    db = _db_with_name_lookup()

    assert await get_registered_application_by_name(db, "test-app") is None

    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "deleted_at IS NULL" in compiled

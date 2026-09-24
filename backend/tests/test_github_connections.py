"""Tests for safe GitHub connection validation and credential resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from c2ai.api import github_connections as github_connections_api
from c2ai.app import app
from c2ai.auth.jwt import AthenaTokenUser, require_admin
from c2ai.clients.github_connection import (
    GitHubConnectionValidationResult,
    validate_github_repository_connection,
)
from c2ai.core.exceptions import (
    GitHubConnectionValidationFailed,
    ServiceUnavailableError,
)
from c2ai.crud.github_connection import (
    create_github_connection,
    github_api_base_url,
    list_github_connections,
    resolve_github_connection,
)
from c2ai.models.registered_application import GitHubConnection
from c2ai.schemas.github_connection import (
    GitHubConnectionCreate,
    GitHubConnectionOut,
)


@pytest.fixture
def github_connection_admin_client(test_client):
    def _admin_override():
        return AthenaTokenUser(
            identifier="admin",
            service="athena",
            metadata={"user_type": "Admin"},
        )

    app.dependency_overrides[require_admin] = _admin_override
    yield test_client
    app.dependency_overrides.pop(require_admin, None)


def test_connection_contract_normalizes_url_and_never_serializes_token():
    payload = GitHubConnectionCreate(
        connection_name="Amberd DevOps",
        repository_url="https://github.com/amberd-ai/devops.git/",
        access_token="github_pat_secret",
    )
    assert payload.connection_name == "Amberd DevOps"
    assert payload.repository_url == "https://github.com/amberd-ai/devops"
    assert payload.connection_url == payload.repository_url

    response = GitHubConnectionOut(
        id="legacy-connection",
        display_name="legacy-connection",
        connection_url=None,
        legacy=True,
    ).model_dump()
    assert "access_token" not in response
    assert "github_pat_secret" not in str(response)


def test_connection_url_rejects_insecure_remote_hosts():
    with pytest.raises(ValidationError, match="must use HTTPS"):
        GitHubConnectionCreate(
            connection_name="Enterprise",
            repository_url="http://github.example.com/team/project",
            access_token="secret",
        )


def test_connection_url_accepts_an_owner_without_a_repository():
    payload = GitHubConnectionCreate(
        connection_name="Amberd",
        repository_url="https://github.com/amberd-ai/",
        access_token="secret",
    )

    assert payload.repository_url == "https://github.com/amberd-ai"


def test_connection_url_requires_an_owner():
    with pytest.raises(ValidationError, match="identify an owner"):
        GitHubConnectionCreate(
            connection_name="Amberd",
            repository_url="https://github.com",
            access_token="secret",
        )


@pytest.mark.asyncio
async def test_create_encrypts_token_with_pgcrypto(monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    connection = await create_github_connection(
        db,
        GitHubConnectionCreate(
            connection_name="Amberd DevOps",
            repository_url="https://github.com/amberd-ai/devops",
            access_token="github_pat_secret",
        ),
        created_by="admin",
    )

    assert isinstance(connection, GitHubConnection)
    assert connection.display_name == "Amberd DevOps"
    assert connection.connection_url == "https://github.com/amberd-ai/devops"
    assert not isinstance(connection.access_token_encrypted, bytes)
    assert "github_pat_secret" not in str(connection.access_token_encrypted)
    db.add.assert_called_once_with(connection)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_includes_legacy_ids_from_registered_applications():
    stored = MagicMock()
    stored.scalars.return_value.all.return_value = []
    legacy = MagicMock()
    legacy.scalars.return_value.all.return_value = ["amberd-ai", "staging-org"]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[stored, legacy])

    connections, legacy_ids = await list_github_connections(db)

    assert connections == []
    assert legacy_ids == ["amberd-ai", "staging-org"]


@pytest.mark.asyncio
async def test_resolve_decrypts_managed_token_and_selects_api_url(monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "encryption-key")
    result = MagicMock()
    result.one_or_none.return_value = (
        "https://github.enterprise.example/platform",
        "github_pat_secret",
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    runtime = await resolve_github_connection(
        db,
        "50000000-0000-0000-0000-000000000001",
    )

    assert runtime is not None
    assert runtime.token == "github_pat_secret"
    assert runtime.api_base_url == "https://github.enterprise.example/api/v3"
    assert await resolve_github_connection(db, "legacy-connection") is None


def test_github_com_uses_public_api_base_url():
    assert github_api_base_url("https://github.com/amberd-ai") == "https://api.github.com"


@pytest.mark.asyncio
async def test_validation_succeeds_when_token_can_read_repository():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.github.com/repos/amberd-ai/devops"
        assert request.headers["Authorization"] == "Bearer github_pat_secret"
        return httpx.Response(200, json={"full_name": "amberd-ai/devops"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await validate_github_repository_connection(
            repository_url="https://github.com/amberd-ai/devops",
            access_token="github_pat_secret",
            client=client,
        )

    assert result == GitHubConnectionValidationResult(
        valid=True,
        message="Connection validated successfully for amberd-ai/devops.",
        repository="amberd-ai/devops",
    )


@pytest.mark.asyncio
async def test_validation_succeeds_for_an_organization_url():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.github.com/users/amberd-ai"
        assert request.headers["Authorization"] == "Bearer github_pat_secret"
        return httpx.Response(
            200,
            json={"login": "amberd-ai", "type": "Organization"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await validate_github_repository_connection(
            repository_url="https://github.com/amberd-ai",
            access_token="github_pat_secret",
            client=client,
        )

    assert result == GitHubConnectionValidationResult(
        valid=True,
        message="Connection validated successfully for amberd-ai.",
        repository=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_message"),
    [
        (401, "GitHub rejected the personal access token."),
        (403, "The token is not authorized to access this GitHub repository."),
        (404, "The repository was not found or the token does not have access to it."),
    ],
)
async def test_validation_returns_safe_failure_for_rejected_access(
    status_code,
    expected_message,
):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "sensitive upstream detail"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await validate_github_repository_connection(
            repository_url="https://github.com/amberd-ai/private-repo",
            access_token="github_pat_secret",
            client=client,
        )

    assert result.valid is False
    assert result.message == expected_message
    assert "github_pat_secret" not in str(result)
    assert "sensitive upstream detail" not in str(result)


@pytest.mark.asyncio
async def test_validation_maps_network_failure_to_service_unavailable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ServiceUnavailableError, match="could not be reached"):
            await validate_github_repository_connection(
                repository_url="https://github.com/amberd-ai/devops",
                access_token="github_pat_secret",
                client=client,
            )


@pytest.mark.asyncio
async def test_create_revalidates_and_never_persists_a_failed_connection():
    payload = GitHubConnectionCreate(
        connection_name="Amberd DevOps",
        repository_url="https://github.com/amberd-ai/devops",
        access_token="github_pat_invalid",
    )
    current_user = AthenaTokenUser(
        identifier="admin",
        service="athena",
        metadata={"user_type": "Admin"},
    )
    db = MagicMock()

    with (
        patch.object(
            github_connections_api,
            "_validate_credentials",
            AsyncMock(
                return_value=GitHubConnectionValidationResult(
                    valid=False,
                    message="GitHub rejected the personal access token.",
                    repository="amberd-ai/devops",
                )
            ),
        ),
        patch.object(
            github_connections_api.crud_github_connection,
            "create_github_connection",
            AsyncMock(),
        ) as persist,
    ):
        with pytest.raises(GitHubConnectionValidationFailed, match="rejected"):
            await github_connections_api.create_github_connection(
                payload,
                current_user,
                db,
            )

    persist.assert_not_awaited()


def test_validation_endpoint_returns_safe_success(github_connection_admin_client):
    with patch.object(
        github_connections_api,
        "_validate_credentials",
        AsyncMock(
            return_value=GitHubConnectionValidationResult(
                valid=True,
                message="Connection validated successfully for amberd-ai/devops.",
                repository="amberd-ai/devops",
            )
        ),
    ):
        response = github_connection_admin_client.post(
            "/api/github-connections/validate",
            json={
                "connection_name": "Amberd DevOps",
                "repository_url": "https://github.com/amberd-ai/devops",
                "access_token": "github_pat_secret",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "message": "Connection validated successfully for amberd-ai/devops.",
        "repository": "amberd-ai/devops",
    }
    assert "github_pat_secret" not in response.text

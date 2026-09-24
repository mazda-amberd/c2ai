"""Security boundary tests for the write-only container secret provider."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from c2ai.clients.container_secret_provider import (
    ContainerSecretProviderClient,
)
from c2ai.core.exceptions import ServiceUnavailableError


@pytest.mark.asyncio
async def test_provider_sends_value_once_and_returns_only_opaque_reference():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"reference": "vault://athena/chatbot/api-key"}
    http_client = MagicMock()
    http_client.put = AsyncMock(return_value=response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=http_client)
    context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "c2ai.clients.container_secret_provider.httpx.AsyncClient",
        return_value=context,
    ):
        provider = ContainerSecretProviderClient(
            base_url="https://secret-broker.internal/v1",
            token="provider-token",
        )
        reference = await provider.upsert_secret(
            secret_id=UUID("70000000-0000-0000-0000-000000000001"),
            application_id=UUID("30000000-0000-0000-0000-000000000001"),
            name="chatbot-api-key",
            environment_variable="CHATBOT_API_KEY",
            secret_value="super-sensitive",
        )

    assert reference == "vault://athena/chatbot/api-key"
    request = http_client.put.await_args
    assert request.kwargs["json"]["secret_value"] == "super-sensitive"
    assert request.kwargs["headers"]["Authorization"] == "Bearer provider-token"
    assert "super-sensitive" not in reference


def test_provider_requires_url_and_token_configuration():
    with patch.dict(
        "os.environ",
        {"CONTAINER_SECRET_PROVIDER_URL": "", "CONTAINER_SECRET_PROVIDER_TOKEN": ""},
    ):
        with pytest.raises(ServiceUnavailableError, match="not configured"):
            ContainerSecretProviderClient()

"""Write-only client for the external container secret broker."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from c2ai.clients.http import http_client
from c2ai.config import get_settings
from c2ai.core.exceptions import ServiceUnavailableError


class ContainerSecretProviderClient:
    """Store secret material externally and return only opaque references."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = (base_url or get_settings().container_secret_provider_url).rstrip(
            "/"
        )
        self.token = token or get_settings().container_secret_provider_token
        if not self.base_url or not self.token:
            raise ServiceUnavailableError(
                "Container secret management is not configured."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def upsert_secret(
        self,
        *,
        secret_id: UUID,
        application_id: UUID,
        name: str,
        environment_variable: str,
        secret_value: str | None,
    ) -> str:
        """Create or update broker material without returning the value."""

        payload: dict[str, Any] = {
            "application_id": str(application_id),
            "name": name,
            "environment_variable": environment_variable,
        }
        if secret_value is not None:
            payload["secret_value"] = secret_value

        try:
            async with http_client(30.0) as client:
                response = await client.put(
                    f"{self.base_url}/secrets/{secret_id}",
                    json=payload,
                    headers=self._headers,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ServiceUnavailableError(
                "The container secret provider could not store the secret."
            ) from error

        reference = body.get("reference") if isinstance(body, dict) else None
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 512:
            raise ServiceUnavailableError(
                "The container secret provider returned an invalid reference."
            )
        return reference.strip()

    async def delete_secret(self, *, secret_id: UUID, reference: str) -> None:
        """Delete broker material by stable Athena ID and opaque reference."""

        try:
            async with http_client(30.0) as client:
                response = await client.request(
                    "DELETE",
                    f"{self.base_url}/secrets/{secret_id}",
                    json={"reference": reference},
                    headers=self._headers,
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(
                "The container secret provider could not delete the secret."
            ) from error

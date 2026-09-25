"""Outbound clients the registered-application routes depend on.

Routes receive them with ``Depends``; tests replace them through
``app.dependency_overrides`` rather than patching module paths.
"""

from __future__ import annotations

from c2ai.clients.container_registry import ContainerRegistryClient
from c2ai.clients.container_secret_provider import ContainerSecretProviderClient

PREFIX = "/api/registered-applications"
TAGS: list[str] = ["Registered Applications"]


def container_secret_provider() -> ContainerSecretProviderClient:
    """Build the configured write-only secret broker client."""

    return ContainerSecretProviderClient()


def container_registry_client() -> ContainerRegistryClient:
    """Build the registry adapter used for image-tag discovery."""

    return ContainerRegistryClient()

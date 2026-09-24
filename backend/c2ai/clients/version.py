"""Client helpers for fetching deployed application versions."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from c2ai.schemas.grafana import Instance

logger = logging.getLogger(__name__)

_VERSION_LOOKUP_TIMEOUT = 2.0
_VERSION_LOOKUP_CONCURRENCY = 8
_INSTANCE_DOMAIN = os.getenv("ATHENA_INSTANCE_DOMAIN", "amberd.ai")


class InstanceVersionClient:
    """
    HTTP client for reading deployed application version metadata.

    This client calls the public ``/version`` endpoint exposed by each
    deployed ADA instance and extracts the ``version`` field from the JSON
    response.
    """

    def __init__(
        self,
        domain: str = _INSTANCE_DOMAIN,
        timeout: float = _VERSION_LOOKUP_TIMEOUT,
    ):
        """
        Initialize the version lookup client.

        Args:
            domain (str): Base instance domain suffix.
            timeout (float): Per-request timeout in seconds.

        Returns:
            None: This constructor does not return a value.
        """
        self.domain = domain
        self.timeout = timeout

    def build_version_url(self, subdomain: str) -> str:
        """
        Build the public ``/version`` endpoint URL for an instance.

        Args:
            subdomain (str): Instance subdomain or namespace.

        Returns:
            str: Fully qualified version endpoint URL.
        """
        return f"https://{subdomain}.{self.domain}/version"

    async def fetch_instance_version(
        self,
        client: httpx.AsyncClient,
        subdomain: str,
    ) -> str | None:
        """
        Fetch the deployed product version for a single instance.

        Args:
            client (httpx.AsyncClient): Shared async HTTP client.
            subdomain (str): Instance subdomain or namespace.

        Returns:
            Optional[str]: Resolved product version, or ``None`` when lookup fails.
        """
        try:
            response = await client.get(
                self.build_version_url(subdomain),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Version lookup failed for %s: %s", subdomain, exc)
            return None
        except ValueError as exc:
            logger.warning(
                "Version lookup returned invalid JSON for %s: %s",
                subdomain,
                exc,
            )
            return None

        version_value = payload.get("version") if isinstance(payload, dict) else None
        if isinstance(version_value, str) and version_value.strip():
            return version_value

        logger.warning("Version lookup returned no version field for %s", subdomain)
        return None


async def enrich_tiers_with_versions(
    tiers: dict[str, list[Instance] | None],
    version_client: InstanceVersionClient | None = None,
) -> dict[str, list[Instance] | None]:
    """
    Populate ``Instance.version`` for every instance in the tier mapping.

    Args:
        tiers (dict[str, Optional[list[Instance]]]): Tier-to-instance mapping
            produced by the Grafana metrics client.
        version_client (Optional[InstanceVersionClient]): Optional injected
            lookup client for testing or custom configuration.

    Returns:
        dict[str, Optional[list[Instance]]]: The same mapping with ``version``
            populated when available.
    """
    resolved_client = version_client or InstanceVersionClient()
    semaphore = asyncio.Semaphore(_VERSION_LOOKUP_CONCURRENCY)

    async with httpx.AsyncClient() as http_client:
        async def enrich_instance(instance: Instance) -> None:
            """
            Enrich one instance in-place with version metadata.

            Args:
                instance (Instance): Metrics instance to enrich.

            Returns:
                None: The instance is mutated in place.
            """
            async with semaphore:
                instance.version = await resolved_client.fetch_instance_version(
                    http_client,
                    instance.nodename,
                )

        await asyncio.gather(
            *[
                enrich_instance(instance)
                for instances in tiers.values()
                if instances
                for instance in instances
            ]
        )

    return tiers

"""Helpers for enriching metrics instances with backend-owned display metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.deployment import Deployment
from c2ai.schemas.grafana import Instance
from c2ai.utils.host_labels import split_workflow_host_label

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstanceMetadata:
    """Backend-owned metadata exposed alongside Grafana metrics."""

    client_name: str | None
    instance_name: str | None


def _normalize(value: str | None) -> str | None:
    """Collapse blank strings to ``None`` while preserving real values."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def load_instance_metadata_map(
    db: AsyncSession,
    subdomains: list[str],
) -> dict[str, InstanceMetadata]:
    """
    Fetch the latest deployment metadata per subdomain from Athena's DB.

    When multiple rows exist for the same subdomain, the newest row wins.
    """
    if not subdomains:
        return {}

    result = await db.execute(
        select(Deployment)
        .where(Deployment.subdomain.in_(subdomains))
        .order_by(Deployment.created_at.desc(), Deployment.id.desc())
    )
    deployments = result.scalars().all()

    metadata_by_subdomain: dict[str, InstanceMetadata] = {}
    for deployment in deployments:
        if deployment.subdomain in metadata_by_subdomain:
            continue
        metadata_by_subdomain[deployment.subdomain] = InstanceMetadata(
            client_name=_normalize(deployment.customer_name),
            instance_name=_normalize(deployment.env_instance),
        )

    return metadata_by_subdomain


async def enrich_tiers_with_instance_metadata(
    db: AsyncSession,
    tiers: dict[str, list[Instance] | None],
) -> dict[str, list[Instance] | None]:
    """
    Populate ``client_name`` and ``instance_name`` for every metrics instance.

    Athena deployment history is the preferred source of truth. When there is no
    matching deployment row, fall back to server-side workflow-host-label parsing
    so the frontend does not have to derive these values on its own.
    """
    all_instances = [
        instance
        for instances in tiers.values()
        if instances
        for instance in instances
    ]
    subdomains = sorted({instance.nodename for instance in all_instances})

    try:
        metadata_by_subdomain = await load_instance_metadata_map(db, subdomains)
    except Exception as exc:
        logger.warning("Could not load deployment metadata for metrics instances: %s", exc)
        metadata_by_subdomain = {}

    for instance in all_instances:
        metadata = metadata_by_subdomain.get(instance.nodename)
        if metadata is None:
            client_name, instance_name = split_workflow_host_label(instance.nodename)
            metadata = InstanceMetadata(
                client_name=_normalize(client_name),
                instance_name=_normalize(instance_name),
            )

        instance.client_name = metadata.client_name
        instance.instance_name = metadata.instance_name

    return tiers

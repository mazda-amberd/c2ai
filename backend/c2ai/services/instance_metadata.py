"""Helpers for enriching metrics instances with backend-owned display metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.registered_application import DeploymentInstance
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


def _parameter(instance: DeploymentInstance, key: str) -> str | None:
    parameters = (instance.configuration or {}).get("parameters")
    value = parameters.get(key) if isinstance(parameters, dict) else None
    return value if isinstance(value, str) else None


async def load_instance_metadata_map(
    db: AsyncSession,
    subdomains: list[str],
) -> dict[str, InstanceMetadata]:
    """
    Client and instance names per subdomain from the deployment records.

    Deployments that were given a customer and environment (ADA and other
    GitHub workflows) supply them, as does the customer recorded by the Deploy
    Application form; the newest instance at a subdomain wins.
    """
    if not subdomains:
        return {}

    result = await db.execute(
        select(DeploymentInstance)
        .where(
            or_(
                DeploymentInstance.subdomain.in_(subdomains),
                DeploymentInstance.instance_name.in_(subdomains),
            )
        )
        .order_by(DeploymentInstance.created_at.desc(), DeploymentInstance.id.desc())
    )

    metadata_by_subdomain: dict[str, InstanceMetadata] = {}
    for instance in result.scalars().all():
        subdomain = instance.subdomain or instance.instance_name
        if subdomain in metadata_by_subdomain:
            continue
        client_name = _parameter(instance, "customer_name")
        environment = _parameter(instance, "env_instance")
        if client_name is None:
            # Recorded from the Deploy Application form for templates whose
            # workflow (or container) takes no customer parameter.
            recorded = (instance.configuration or {}).get("customer_name")
            if isinstance(recorded, str):
                client_name = recorded
                environment = environment or instance.instance_name
        if client_name is None and environment is None:
            continue
        metadata_by_subdomain[subdomain] = InstanceMetadata(
            client_name=_normalize(client_name),
            instance_name=_normalize(environment),
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

# pylint: disable=import-error
"""
CRUD operations for the Deployment model.

Async SQLAlchemy (AsyncSession) functions to create, query, and update
deployment records in the `deployments` table.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.deployment import Deployment
from c2ai.constants.deployment import WEBHOOK_TERMINAL_STATUSES

logger = logging.getLogger(__name__)


async def create_deployment(
    db: AsyncSession,
    *,
    subdomain: str,
    customer_name: str,
    env_instance: str,
    tier: int,
    branch: str,
    domain: str,
) -> Deployment:
    """
    Insert a new Deployment row with status='deploying'.

    Args:
        db: Async database session.
        subdomain: Computed subdomain string.
        customer_name: Customer name from the form.
        env_instance: Environment / instance label for the deploy workflow.
        tier: Numeric tier index (1-4).
        branch: Git branch to deploy.
        domain: Target domain.

    Returns:
        The newly created Deployment ORM instance.
    """
    deployment = Deployment(
        subdomain=subdomain,
        customer_name=customer_name,
        env_instance=env_instance,
        tier=tier,
        branch=branch,
        domain=domain,
        status="deploying",
    )
    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)
    logger.info("Created deployment id=%s subdomain=%s", deployment.id, subdomain)
    return deployment


async def get_deployments_by_tier(
    db: AsyncSession,
    tier: int,
    *,
    status: Optional[str] = None,
) -> List[Deployment]:
    """
    Return deployments for a given tier, optionally filtered by status.

    Args:
        db: Async database session.
        tier: Numeric tier index to filter by.
        status: Optional status string to filter by (e.g. "deploying").

    Returns:
        List of matching Deployment instances ordered by created_at descending.
    """
    query = (
        select(Deployment)
        .where(Deployment.tier == tier)
        .order_by(Deployment.created_at.desc())
    )
    if status is not None:
        query = query.where(Deployment.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_deployment(
    db: AsyncSession,
    deployment_id: int,
) -> Optional[Deployment]:
    """
    Fetch a single Deployment by primary key.

    Args:
        db: Async database session.
        deployment_id: Primary key of the deployment.

    Returns:
        The Deployment if found, else None.
    """
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    return result.scalar_one_or_none()


async def update_deployment_status(
    db: AsyncSession,
    deployment_id: int,
    status: str,
) -> Optional[Deployment]:
    """
    Update the status of a deployment. Sets completed_at for terminal webhook
    outcomes (success, failed, cancelled).

    Args:
        db: Async database session.
        deployment_id: Primary key of the deployment to update.
        status: New status string (e.g. success, failed, cancelled).

    Returns:
        The updated Deployment, or None if not found.
    """
    deployment = await get_deployment(db, deployment_id)
    if not deployment:
        logger.warning("Deployment id=%s not found for status update", deployment_id)
        return None

    deployment.status = status
    if status in WEBHOOK_TERMINAL_STATUSES:
        deployment.completed_at = datetime.utcnow()

    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)
    logger.info("Deployment id=%s updated to status=%s", deployment_id, status)
    return deployment

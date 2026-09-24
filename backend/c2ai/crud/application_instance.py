# pylint: disable=import-error
"""Persist application instance snapshots (mirrors GET /api/metrics payload)."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.application_instance import ApplicationInstance
from c2ai.schemas.grafana import Instance, Status

logger = logging.getLogger(__name__)


async def replace_application_instances_for_tiers(
    db: AsyncSession,
    tiers: dict[str, Optional[list[Instance]]],
) -> None:
    """
    For each tier key in ``tiers``, delete existing rows for that tier and insert the
    current instance list. Tiers not present in ``tiers`` are left unchanged (supports
    ``tier=`` query filter on ``/api/metrics``).

    ``None`` means no instances for that tier (e.g. Tier 4): all rows for that tier
    are removed.
    """
    for tier_name, instances in tiers.items():
        await db.execute(delete(ApplicationInstance).where(ApplicationInstance.tier_name == tier_name))
        if instances is None:
            continue
        for inst in instances:
            status_val = inst.status.value if isinstance(inst.status, Status) else str(inst.status)
            db.add(
                ApplicationInstance(
                    tier_name=tier_name,
                    name=inst.name,
                    nodename=inst.nodename,
                    cpu=float(inst.cpu),
                    memory=float(inst.memory),
                    gpu=float(inst.gpu),
                    status=status_val,
                )
            )

    await db.commit()

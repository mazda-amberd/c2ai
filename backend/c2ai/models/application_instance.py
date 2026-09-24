# pylint: disable=import-error
"""SQLAlchemy model for `application_instances` (Grafana deployment snapshot)."""

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class ApplicationInstance(Base):
    """
    One row per (tier, nodename) as returned by GET /api/metrics.

    ``name`` is the workload label (often repeated, e.g. ``ada``); ``nodename`` is the
    unique namespace / host label. Re-synced on each successful metrics fetch.
    """

    __tablename__ = "application_instances"
    __table_args__ = (
        UniqueConstraint("tier_name", "nodename", name="uq_application_instances_tier_nodename"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tier_name = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    nodename = Column(String, nullable=False)
    cpu = Column(Float, nullable=False)
    memory = Column(Float, nullable=False)
    gpu = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

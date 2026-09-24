# pylint: disable=import-error
"""SQLAlchemy model for the `pipeline_runs` table: the deployment operation log."""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class PipelineRun(Base):
    """
    One row per lifecycle operation of a deployment instance.

    Every application type writes here (ADA through /api/deploy*, registered
    applications through /api/registered-applications); the instance's
    current state lives in ``deployment_instances``. At most one row per
    subdomain may be unfinished (``ended_at IS NULL``) — a unique index
    enforces the one-operation-at-a-time rule.

    Attributes:
        id (str): UUID; also the ``deployment_id`` input of legacy workflows.
        deployment_instance_id (UUID): The instance this operation acts on.
        subdomain (str): The instance's host label (or instance name).
        operation (str): "deploy" | "update" | "migration" | "terminate".
        event_type (str): Workflow file (or pipeline) that runs it.
        triggered_by (str): Athena user identifier.
        run_id (int | None): GitHub Actions run id, once known.
        tier (int | None): Tier the operation targets.
        branch (str | None): Version/ref being deployed.
        dispatched_at (datetime): When the operation was requested.
        ended_at (datetime | None): NULL while the operation is in progress.
        conclusion (str | None): success | failure | cancelled | abandoned.
    """

    __tablename__ = "pipeline_runs"

    id = Column(String, primary_key=True)
    deployment_instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deployment_instances.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Declared so the unit of work inserts an instance before its first
    # operation row when both are flushed together.
    deployment_instance = relationship("DeploymentInstance", lazy="noload")

    subdomain = Column(String, nullable=False, index=True)
    operation = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    triggered_by = Column(String, nullable=False)

    run_id = Column(BigInteger, nullable=True)

    tier = Column(Integer, nullable=True)
    branch = Column(String, nullable=True)

    dispatched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at = Column(DateTime(timezone=True), nullable=True)
    conclusion = Column(String, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subdomain": self.subdomain,
            "operation": self.operation,
            "event_type": self.event_type,
            "triggered_by": self.triggered_by,
            "run_id": self.run_id,
            "tier": self.tier,
            "branch": self.branch,
            "dispatched_at": (
                self.dispatched_at.isoformat() if self.dispatched_at else None
            ),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<PipelineRun id={self.id!r} subdomain={self.subdomain!r} "
            f"op={self.operation!r} run_id={self.run_id}>"
        )

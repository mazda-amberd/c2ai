# pylint: disable=import-error
"""SQLAlchemy model for the `pipeline_runs` table."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from .base import Base


class PipelineRun(Base):
    """
    One row per deploy/update/terminate operation triggered from the Athena UI.

    GitHub Actions owns the pipeline status; this table stores only the
    correlation metadata needed to look up the GH run and the lifecycle sentinel
    `ended_at` used to enforce the one-active-operation-per-subdomain guard.

    Attributes:
        id (str): UUID primary key; also used as `correlation_id` / `deployment_id`
            passed to the GitHub Actions workflow so the two sides share a handle.
        subdomain (str): Target instance subdomain, e.g. amberd-acme-ada.
        operation (str): "deploy" | "update" | "terminate".
        event_type (str): GitHub workflow file name: "ada-deploy.yaml" etc.
        triggered_by (str): Athena user identifier from the JWT.
        run_id (int | None): GitHub Actions run_id; NULL until resolved by background task.
        tier (int | None): Numeric tier (deploy/update only).
        branch (str | None): Git branch (deploy/update only).
        dispatched_at (datetime): When the workflow was dispatched.
        ended_at (datetime | None): NULL while active; set when GH run completes.
    """

    __tablename__ = "pipeline_runs"

    id = Column(String, primary_key=True)

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

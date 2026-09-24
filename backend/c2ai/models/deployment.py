# pylint: disable=import-error
"""SQLAlchemy model for the `deployments` table."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from .base import Base


class Deployment(Base):
    """
    SQLAlchemy model for the `deployments` table.

    Tracks deployments triggered from the Athena UI. A deployment is created
    with status 'deploying' when the GitHub Actions workflow is dispatched and
    updated to a terminal status (success, failed, or cancelled) via the inbound
    webhook once the pipeline finishes or is cancelled.

    Attributes:
        id (int): Auto-increment primary key.
        subdomain (str): Computed subdomain, e.g. amberd-{customer}.
        customer_name (str): Customer identifier provided by the user.
        env_instance (str): Environment / instance label for the deploy workflow.
        tier (int): Numeric tier index (1-4) for dashboard filtering.
            Provider string (e.g. "tier1") is derived as f"tier{tier}" when needed.
        branch (str): Git branch to deploy.
        domain (str): Target domain, e.g. "amberd.ai".
        status (str): One of "deploying", "success", "failed", "cancelled".
        created_at (datetime): When the deployment was triggered.
        completed_at (datetime | None): When the pipeline finished (set by webhook).
    """

    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    subdomain = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    env_instance = Column(String, nullable=False)
    tier = Column(Integer, nullable=False)
    branch = Column(String, nullable=False)
    domain = Column(String, nullable=False)

    status = Column(String, nullable=False, default="deploying")

    created_at = Column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=False), nullable=True)

    def to_dict(self) -> dict:
        """Convert the Deployment instance to a serialisable dictionary."""
        return {
            "id": self.id,
            "subdomain": self.subdomain,
            "customer_name": self.customer_name,
            "env_instance": self.env_instance,
            "tier": self.tier,
            "branch": self.branch,
            "domain": self.domain,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<Deployment id={self.id} subdomain={self.subdomain!r} "
            f"status={self.status!r}>"
        )

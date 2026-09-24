# pylint: disable=import-error
"""SQLAlchemy model for the `deployments` table."""


from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from .base import Base


class Deployment(Base):
    """Who a legacy ADA deployment was requested for.

    One row is written per ``POST /api/deploy`` together with its
    ``pipeline_runs`` row. The metrics views read the newest row per subdomain
    to label a namespace with its client and instance names. Pipeline progress
    lives in ``pipeline_runs``/GitHub; ``status`` here is ``dispatched``.
    """

    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    subdomain = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    env_instance = Column(String, nullable=False)
    tier = Column(Integer, nullable=False)
    branch = Column(String, nullable=False)
    domain = Column(String, nullable=False)

    status = Column(String, nullable=False, default="dispatched")

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

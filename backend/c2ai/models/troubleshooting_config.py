"""SQLAlchemy model for the singleton troubleshooting configuration."""

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, SmallInteger
from sqlalchemy.sql import func

from .base import Base


class TroubleshootingConfig(Base):
    """Database-controlled evidence lookback window for troubleshooting."""

    __tablename__ = "troubleshooting_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_troubleshooting_config_singleton"),
        CheckConstraint(
            "lookback_hours BETWEEN 1 AND 168",
            name="ck_troubleshooting_lookback_hours",
        ),
    )

    id = Column(SmallInteger, primary_key=True, default=1)
    lookback_hours = Column(Integer, nullable=False, default=4)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

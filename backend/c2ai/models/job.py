"""SQLAlchemy model for the ``jobs`` table (see migrations/0022_jobs.sql)."""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class Job(Base):
    """One unit of background work, claimed by a worker under a lease."""

    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    # Handler-reported progress, e.g. the troubleshooting phase.
    stage = Column(String, nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    result = Column(JSONB, nullable=True)
    # Identifier of the user a job belongs to (only they may read it).
    owner = Column(String, nullable=True)
    dedupe_key = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=1)
    run_after = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_by = Column(String, nullable=True)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    retention_seconds = Column(Integer, nullable=False, default=86400)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Job id={self.id} kind={self.kind!r} status={self.status!r}>"

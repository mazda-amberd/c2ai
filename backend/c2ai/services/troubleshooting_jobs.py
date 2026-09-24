"""Short-lived owner-scoped storage for troubleshooting jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from c2ai.schemas.troubleshooting import (
    TroubleshootingJobStatus,
    TroubleshootingReportRequest,
    TroubleshootingReportResponse,
)

_JOB_TTL = timedelta(minutes=15)


@dataclass
class TroubleshootingJobRecord:
    """Internal job state; never expose owner identity in API responses."""

    job_id: str
    owner_id: str
    request: TroubleshootingReportRequest
    status: TroubleshootingJobStatus
    created_at: datetime
    updated_at: datetime
    report: TroubleshootingReportResponse | None = None
    error_code: str | None = None
    error_message: str | None = None


class InMemoryTroubleshootingJobStore:
    """Thread-safe transient store suitable for the first single-process release."""

    def __init__(self) -> None:
        self._jobs: dict[str, TroubleshootingJobRecord] = {}
        self._lock = RLock()

    def _cleanup(self, now: datetime) -> None:
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if now - job.updated_at >= _JOB_TTL
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)

    def create(
        self,
        *,
        owner_id: str,
        request: TroubleshootingReportRequest,
        now: datetime,
    ) -> TroubleshootingJobRecord:
        with self._lock:
            self._cleanup(now)
            record = TroubleshootingJobRecord(
                job_id=str(uuid4()),
                owner_id=owner_id,
                request=request,
                status="gathering_data",
                created_at=now,
                updated_at=now,
            )
            self._jobs[record.job_id] = record
            return replace(record)

    def get_for_owner(
        self,
        *,
        job_id: str,
        owner_id: str,
        now: datetime,
    ) -> TroubleshootingJobRecord | None:
        with self._lock:
            self._cleanup(now)
            record = self._jobs.get(job_id)
            if record is None or record.owner_id != owner_id:
                return None
            return replace(record)

    def set_status(
        self,
        job_id: str,
        status: TroubleshootingJobStatus,
        *,
        now: datetime,
    ) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = status
            record.updated_at = now

    def complete(
        self,
        job_id: str,
        report: TroubleshootingReportResponse,
        *,
        now: datetime,
    ) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = "completed"
            record.report = report
            record.error_code = None
            record.error_message = None
            record.updated_at = now

    def fail(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        now: datetime,
    ) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = "failed"
            record.report = None
            record.error_code = code
            record.error_message = message
            record.updated_at = now

    def clear(self) -> None:
        """Clear transient records; intended for application shutdown and tests."""

        with self._lock:
            self._jobs.clear()

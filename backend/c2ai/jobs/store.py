"""Job persistence: a PostgreSQL queue and an in-memory twin for unit tests.

Both implement the same operations. Every write a worker makes to a running
job is fenced on ``locked_by``: once a lease expires and another worker claims
the job, the first worker's late updates are ignored.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
FINISHED = frozenset({SUCCEEDED, FAILED})

DEFAULT_RETENTION = timedelta(days=1)
LEASE_EXPIRED_CODE = "JobLeaseExpired"
_LEASE_EXPIRED_MESSAGE = "The worker running this job stopped before finishing it."


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    kind: str
    status: str
    payload: dict[str, Any]
    stage: str | None = None
    result: dict[str, Any] | None = None
    owner: str | None = None
    dedupe_key: str | None = None
    attempts: int = 0
    max_attempts: int = 1
    run_after: datetime | None = None
    locked_by: str | None = None
    locked_until: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    retention_seconds: int = int(DEFAULT_RETENTION.total_seconds())
    expires_at: datetime | None = None


@dataclass(frozen=True)
class FollowUp:
    """A job to enqueue atomically when another one finishes (periodic jobs)."""

    kind: str
    run_after: datetime
    payload: dict[str, Any] | None = None
    dedupe_key: str | None = None
    max_attempts: int = 1


class JobStore(ABC):
    def now(self) -> datetime:
        """The queue's clock (retry and schedule times are computed from it)."""

        return utcnow()

    @abstractmethod
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        owner: str | None = None,
        dedupe_key: str | None = None,
        run_after: datetime | None = None,
        max_attempts: int = 1,
        retention: timedelta = DEFAULT_RETENTION,
        session: AsyncSession | None = None,
    ) -> JobRecord | None:
        """Queue a job; None when an unfinished job already has ``dedupe_key``.

        With ``session`` the job is written in the caller's transaction (the
        outbox pattern) and becomes visible when the caller commits.
        """

    @abstractmethod
    async def get(self, job_id: UUID | str) -> JobRecord | None:
        """A job that has not expired, or None."""

    @abstractmethod
    async def claim(
        self, worker_id: str, *, kinds: Sequence[str], lease: timedelta, limit: int = 1
    ) -> list[JobRecord]:
        """Take up to ``limit`` ready jobs of ``kinds``, oldest first."""

    @abstractmethod
    async def claim_job(self, job_id: UUID, worker_id: str, lease: timedelta) -> JobRecord | None:
        """Claim one specific queued job (to run it inline); None if taken."""

    @abstractmethod
    async def renew(self, job_id: UUID, worker_id: str, lease: timedelta) -> bool:
        """Extend a running job's lease; False when the worker lost it."""

    @abstractmethod
    async def set_stage(self, job_id: UUID, worker_id: str, stage: str) -> None: ...

    @abstractmethod
    async def succeed(
        self,
        job_id: UUID,
        worker_id: str,
        result: dict[str, Any] | None,
        *,
        follow_up: FollowUp | None = None,
    ) -> bool: ...

    @abstractmethod
    async def fail(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        code: str,
        message: str,
        retry_at: datetime | None = None,
        follow_up: FollowUp | None = None,
    ) -> bool:
        """Fail the job, or queue it again at ``retry_at`` if attempts remain."""

    @abstractmethod
    async def recover_expired_leases(self) -> int:
        """Re-queue (or fail, when out of attempts) jobs whose worker vanished."""

    @abstractmethod
    async def purge_expired(self) -> int:
        """Delete finished jobs past their retention."""


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

_COLUMNS = (
    "id, kind, status, payload, stage, result, owner, dedupe_key, attempts, max_attempts,"
    " run_after, locked_by, locked_until, error_code, error_message, created_at,"
    " updated_at, finished_at, retention_seconds, expires_at"
)

_INSERT = f"""
INSERT INTO jobs (kind, payload, owner, dedupe_key, run_after, max_attempts, retention_seconds)
VALUES (:kind, CAST(:payload AS jsonb), :owner, :dedupe_key, :run_after, :max_attempts,
        :retention_seconds)
ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
DO NOTHING
RETURNING {_COLUMNS}
"""

_FINISH = f"""
UPDATE jobs
SET status = :status, stage = NULL, result = CAST(:result AS jsonb),
    error_code = :error_code, error_message = :error_message,
    locked_by = NULL, locked_until = NULL, updated_at = now(), finished_at = now(),
    expires_at = now() + make_interval(secs => retention_seconds)
WHERE id = :id AND status = 'running' AND locked_by = :worker
RETURNING {_COLUMNS}
"""


def _record(row: Any) -> JobRecord:
    return JobRecord(**dict(row._mapping))


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


class PostgresJobStore(JobStore):
    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self._session_factory = session_factory

    async def _insert(self, session: AsyncSession, **values: Any) -> JobRecord | None:
        row = (await session.execute(text(_INSERT), values)).first()
        return _record(row) if row else None

    async def enqueue(
        self,
        kind,
        payload=None,
        *,
        owner=None,
        dedupe_key=None,
        run_after=None,
        max_attempts=1,
        retention=DEFAULT_RETENTION,
        session=None,
    ):
        values = {
            "kind": kind,
            "payload": _json(payload or {}),
            "owner": owner,
            "dedupe_key": dedupe_key,
            "run_after": run_after or utcnow(),
            "max_attempts": max_attempts,
            "retention_seconds": int(retention.total_seconds()),
        }
        if session is not None:
            return await self._insert(session, **values)
        async with self._session_factory() as own:
            record = await self._insert(own, **values)
            await own.commit()
            return record

    async def claim_job(self, job_id, worker_id, lease):
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        f"""
                        UPDATE jobs
                        SET status = 'running', attempts = attempts + 1,
                            locked_by = :worker,
                            locked_until = now() + make_interval(secs => :lease),
                            updated_at = now()
                        WHERE id = :id AND status = 'queued'
                        RETURNING {_COLUMNS}
                        """
                    ),
                    {"id": job_id, "worker": worker_id, "lease": lease.total_seconds()},
                )
            ).first()
            await session.commit()
            return _record(row) if row else None

    async def get(self, job_id):
        try:
            job_uuid = UUID(str(job_id))
        except ValueError:
            return None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM jobs WHERE id = :id"
                        " AND (expires_at IS NULL OR expires_at > now())"
                    ),
                    {"id": job_uuid},
                )
            ).first()
            return _record(row) if row else None

    async def claim(self, worker_id, *, kinds, lease, limit=1):
        if not kinds or limit < 1:
            return []
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        f"""
                        WITH ready AS (
                            SELECT id FROM jobs
                            WHERE status = 'queued' AND run_after <= now()
                              AND kind = ANY(:kinds)
                            ORDER BY run_after, created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT :limit
                        )
                        UPDATE jobs AS j
                        SET status = 'running', attempts = j.attempts + 1,
                            locked_by = :worker,
                            locked_until = now() + make_interval(secs => :lease),
                            updated_at = now()
                        FROM ready WHERE j.id = ready.id
                        RETURNING {", ".join(f"j.{c.strip()}" for c in _COLUMNS.split(","))}
                        """
                    ),
                    {
                        "kinds": list(kinds),
                        "limit": limit,
                        "worker": worker_id,
                        "lease": lease.total_seconds(),
                    },
                )
            ).all()
            await session.commit()
            return [_record(row) for row in rows]

    async def renew(self, job_id, worker_id, lease):
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "UPDATE jobs SET locked_until = now() + make_interval(secs => :lease),"
                    " updated_at = now()"
                    " WHERE id = :id AND status = 'running' AND locked_by = :worker"
                ),
                {"id": job_id, "worker": worker_id, "lease": lease.total_seconds()},
            )
            await session.commit()
            return result.rowcount == 1

    async def set_stage(self, job_id, worker_id, stage):
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE jobs SET stage = :stage, updated_at = now()"
                    " WHERE id = :id AND status = 'running' AND locked_by = :worker"
                ),
                {"id": job_id, "worker": worker_id, "stage": stage},
            )
            await session.commit()

    async def _finish(self, job_id, worker_id, values, follow_up):
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(_FINISH), {"id": job_id, "worker": worker_id, **values}
                )
            ).first()
            if row is None:
                await session.rollback()
                return False
            if follow_up is not None:
                await self._insert(
                    session,
                    kind=follow_up.kind,
                    payload=_json(follow_up.payload or {}),
                    owner=None,
                    dedupe_key=follow_up.dedupe_key,
                    run_after=follow_up.run_after,
                    max_attempts=follow_up.max_attempts,
                    retention_seconds=int(DEFAULT_RETENTION.total_seconds()),
                )
            await session.commit()
            return True

    async def succeed(self, job_id, worker_id, result, *, follow_up=None):
        return await self._finish(
            job_id,
            worker_id,
            {
                "status": SUCCEEDED,
                "result": _json(result),
                "error_code": None,
                "error_message": None,
            },
            follow_up,
        )

    async def fail(
        self, job_id, worker_id, *, code, message, retry_at=None, follow_up=None
    ):
        if retry_at is not None:
            async with self._session_factory() as session:
                result = await session.execute(
                    text(
                        "UPDATE jobs SET status = 'queued', run_after = :retry_at,"
                        " error_code = :code, error_message = :message,"
                        " locked_by = NULL, locked_until = NULL, updated_at = now()"
                        " WHERE id = :id AND status = 'running' AND locked_by = :worker"
                        " AND attempts < max_attempts"
                    ),
                    {
                        "id": job_id,
                        "worker": worker_id,
                        "retry_at": retry_at,
                        "code": code,
                        "message": message,
                    },
                )
                await session.commit()
                if result.rowcount == 1:
                    return True
        return await self._finish(
            job_id,
            worker_id,
            {"status": FAILED, "result": None, "error_code": code, "error_message": message},
            follow_up,
        )

    async def recover_expired_leases(self):
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                        error_code = CASE WHEN attempts < max_attempts THEN error_code
                                          ELSE :code END,
                        error_message = CASE WHEN attempts < max_attempts THEN error_message
                                             ELSE :message END,
                        finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                        expires_at = CASE WHEN attempts < max_attempts THEN NULL
                                          ELSE now() + make_interval(secs => retention_seconds)
                                     END,
                        stage = NULL, locked_by = NULL, locked_until = NULL, updated_at = now()
                    WHERE status = 'running' AND locked_until < now()
                    """
                ),
                {"code": LEASE_EXPIRED_CODE, "message": _LEASE_EXPIRED_MESSAGE},
            )
            await session.commit()
            return result.rowcount or 0

    async def purge_expired(self):
        async with self._session_factory() as session:
            result = await session.execute(
                text("DELETE FROM jobs WHERE expires_at IS NOT NULL AND expires_at <= now()")
            )
            await session.commit()
            return result.rowcount or 0


# ---------------------------------------------------------------------------
# In memory (unit tests)
# ---------------------------------------------------------------------------


class MemoryJobStore(JobStore):
    """Same semantics as the PostgreSQL store, for tests without a database."""

    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._jobs: dict[UUID, JobRecord] = {}
        self._clock = clock
        self._lock = asyncio.Lock()

    def now(self) -> datetime:
        return self._clock()

    @property
    def jobs(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def _deduped(self, dedupe_key: str | None) -> bool:
        return dedupe_key is not None and any(
            job.dedupe_key == dedupe_key and job.status in (QUEUED, RUNNING)
            for job in self._jobs.values()
        )

    def _add(self, **values: Any) -> JobRecord | None:
        if self._deduped(values.get("dedupe_key")):
            return None
        now = self._clock()
        record = JobRecord(
            id=uuid.uuid4(),
            status=QUEUED,
            created_at=now,
            updated_at=now,
            **{"run_after": now, **values},
        )
        self._jobs[record.id] = record
        return record

    async def enqueue(
        self,
        kind,
        payload=None,
        *,
        owner=None,
        dedupe_key=None,
        run_after=None,
        max_attempts=1,
        retention=DEFAULT_RETENTION,
        session=None,
    ):
        async with self._lock:
            return self._add(
                kind=kind,
                payload=dict(payload or {}),
                owner=owner,
                dedupe_key=dedupe_key,
                run_after=run_after or self._clock(),
                max_attempts=max_attempts,
                retention_seconds=int(retention.total_seconds()),
            )

    async def get(self, job_id):
        try:
            record = self._jobs.get(UUID(str(job_id)))
        except ValueError:
            return None
        if record is None or (record.expires_at and record.expires_at <= self._clock()):
            return None
        return record

    async def claim(self, worker_id, *, kinds, lease, limit=1):
        async with self._lock:
            now = self._clock()
            ready = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.status == QUEUED and job.run_after <= now and job.kind in kinds
                ),
                key=lambda job: (job.run_after, job.created_at),
            )[:limit]
            claimed = []
            for job in ready:
                updated = replace(
                    job,
                    status=RUNNING,
                    attempts=job.attempts + 1,
                    locked_by=worker_id,
                    locked_until=now + lease,
                    updated_at=now,
                )
                self._jobs[job.id] = updated
                claimed.append(updated)
            return claimed

    async def claim_job(self, job_id, worker_id, lease):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != QUEUED:
                return None
            now = self._clock()
            claimed = replace(
                job,
                status=RUNNING,
                attempts=job.attempts + 1,
                locked_by=worker_id,
                locked_until=now + lease,
                updated_at=now,
            )
            self._jobs[job_id] = claimed
            return claimed

    def _owned(self, job_id: UUID, worker_id: str) -> JobRecord | None:
        job = self._jobs.get(job_id)
        if job is None or job.status != RUNNING or job.locked_by != worker_id:
            return None
        return job

    async def renew(self, job_id, worker_id, lease):
        job = self._owned(job_id, worker_id)
        if job is None:
            return False
        now = self._clock()
        self._jobs[job_id] = replace(job, locked_until=now + lease, updated_at=now)
        return True

    async def set_stage(self, job_id, worker_id, stage):
        job = self._owned(job_id, worker_id)
        if job is not None:
            self._jobs[job_id] = replace(job, stage=stage, updated_at=self._clock())

    def _finish(self, job, **values):
        now = self._clock()
        self._jobs[job.id] = replace(
            job,
            stage=None,
            locked_by=None,
            locked_until=None,
            updated_at=now,
            finished_at=now,
            expires_at=now + timedelta(seconds=job.retention_seconds),
            **values,
        )

    def _follow(self, follow_up: FollowUp | None) -> None:
        if follow_up is not None:
            self._add(
                kind=follow_up.kind,
                payload=dict(follow_up.payload or {}),
                dedupe_key=follow_up.dedupe_key,
                run_after=follow_up.run_after,
                max_attempts=follow_up.max_attempts,
            )

    async def succeed(self, job_id, worker_id, result, *, follow_up=None):
        async with self._lock:
            job = self._owned(job_id, worker_id)
            if job is None:
                return False
            self._finish(
                job, status=SUCCEEDED, result=result, error_code=None, error_message=None
            )
            self._follow(follow_up)
            return True

    async def fail(
        self, job_id, worker_id, *, code, message, retry_at=None, follow_up=None
    ):
        async with self._lock:
            job = self._owned(job_id, worker_id)
            if job is None:
                return False
            if retry_at is not None and job.attempts < job.max_attempts:
                self._jobs[job_id] = replace(
                    job,
                    status=QUEUED,
                    run_after=retry_at,
                    error_code=code,
                    error_message=message,
                    locked_by=None,
                    locked_until=None,
                    updated_at=self._clock(),
                )
                return True
            self._finish(job, status=FAILED, result=None, error_code=code, error_message=message)
            self._follow(follow_up)
            return True

    async def recover_expired_leases(self):
        async with self._lock:
            now = self._clock()
            count = 0
            for job in list(self._jobs.values()):
                if job.status == RUNNING and job.locked_until and job.locked_until < now:
                    count += 1
                    if job.attempts < job.max_attempts:
                        self._jobs[job.id] = replace(
                            job, status=QUEUED, stage=None, locked_by=None, locked_until=None
                        )
                    else:
                        self._finish(
                            job,
                            status=FAILED,
                            error_code=LEASE_EXPIRED_CODE,
                            error_message=_LEASE_EXPIRED_MESSAGE,
                        )
            return count

    async def purge_expired(self):
        async with self._lock:
            now = self._clock()
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.expires_at is not None and job.expires_at <= now
            ]
            for job_id in expired:
                del self._jobs[job_id]
            return len(expired)

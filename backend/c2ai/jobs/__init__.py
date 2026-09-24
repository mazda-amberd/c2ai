"""Durable background jobs (see migrations/0022_jobs.sql and ``worker.py``)."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from c2ai.jobs.store import JobRecord, JobStore, MemoryJobStore, PostgresJobStore
from c2ai.jobs.worker import JobContext, JobFailed, Worker, job_handler, wake_worker

__all__ = [
    "JobContext",
    "JobFailed",
    "JobRecord",
    "JobStore",
    "MemoryJobStore",
    "PostgresJobStore",
    "Worker",
    "get_job_notifier",
    "get_job_store",
    "job_handler",
    "wake_worker",
]


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    """The application's job queue (a FastAPI dependency tests can override)."""

    from c2ai.db.session import AsyncSessionLocal

    return PostgresJobStore(AsyncSessionLocal)


def get_job_notifier() -> Callable[[], object]:
    """Called after a request enqueues work: wakes the in-process worker."""

    return wake_worker

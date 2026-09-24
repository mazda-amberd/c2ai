"""The job worker: claims jobs, runs their handlers under a renewed lease,
and keeps periodic jobs scheduled.

Runs inside the API process (``C2AI_RUN_WORKER=true``, the default) or on its
own with ``python -m c2ai.worker``; any number of workers may share one
database. Handlers register with ``@job_handler("kind")``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from c2ai.jobs.store import FollowUp, JobRecord, JobStore

logger = logging.getLogger(__name__)


class JobFailed(Exception):
    """A handler's expected failure: recorded on the job with ``code``.

    ``retry=True`` queues the job again (with backoff) while attempts remain.
    """

    def __init__(self, code: str, message: str, *, retry: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry = retry


@dataclass
class JobContext:
    job: JobRecord
    store: JobStore
    worker_id: str

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    async def set_stage(self, stage: str) -> None:
        await self.store.set_stage(self.job.id, self.worker_id, stage)


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class HandlerSpec:
    kind: str
    run: JobHandler
    lease: timedelta
    retry_backoff: timedelta


@dataclass(frozen=True)
class Schedule:
    """Keep one ``kind`` job queued, run every ``every`` while ``enabled()``."""

    kind: str
    every: Callable[[], timedelta]
    enabled: Callable[[], bool] = lambda: True

    @property
    def dedupe_key(self) -> str:
        return f"schedule:{self.kind}"


_handlers: dict[str, HandlerSpec] = {}
_schedules: dict[str, Schedule] = {}


def job_handler(
    kind: str,
    *,
    lease: timedelta = timedelta(minutes=2),
    retry_backoff: timedelta = timedelta(seconds=30),
) -> Callable[[JobHandler], JobHandler]:
    def register(run: JobHandler) -> JobHandler:
        _handlers[kind] = HandlerSpec(kind, run, lease, retry_backoff)
        return run

    return register


def register_schedule(schedule: Schedule) -> None:
    _schedules[schedule.kind] = schedule


def registered_handlers() -> dict[str, HandlerSpec]:
    _load_handlers()
    return dict(_handlers)


def _load_handlers() -> None:
    # Importing the handler modules registers them.
    from c2ai.jobs import handlers  # noqa: F401


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


@dataclass
class Worker:
    store: JobStore
    worker_id: str = field(default_factory=default_worker_id)
    concurrency: int = 4
    poll_seconds: float = 1.0
    handlers: dict[str, HandlerSpec] | None = None
    schedules: Sequence[Schedule] | None = None

    def __post_init__(self) -> None:
        if self.handlers is None:
            self.handlers = registered_handlers()
        if self.schedules is None:
            _load_handlers()
            self.schedules = list(_schedules.values())
        self._wake = asyncio.Event()
        self._running: set[asyncio.Task] = set()

    def wake(self) -> None:
        """Look for work now instead of at the next poll (called after enqueue)."""

        self._wake.set()

    # --- scheduling ---------------------------------------------------------

    async def ensure_schedules(self) -> None:
        for schedule in self.schedules or ():
            if schedule.enabled():
                await self.store.enqueue(schedule.kind, dedupe_key=schedule.dedupe_key)

    def _follow_up(self, job: JobRecord) -> FollowUp | None:
        schedule = next((s for s in self.schedules or () if s.kind == job.kind), None)
        if schedule is None or job.dedupe_key != schedule.dedupe_key or not schedule.enabled():
            return None
        return FollowUp(
            kind=job.kind,
            dedupe_key=schedule.dedupe_key,
            run_after=self.store.now() + schedule.every(),
        )

    # --- execution ----------------------------------------------------------

    async def _heartbeat(self, job: JobRecord, spec: HandlerSpec) -> None:
        interval = max(spec.lease.total_seconds() / 3, 0.05)
        while True:
            await asyncio.sleep(interval)
            if not await self.store.renew(job.id, self.worker_id, spec.lease):
                logger.warning("Lost the lease on job %s (%s)", job.id, job.kind)
                return

    async def execute(self, job: JobRecord) -> None:
        spec = (self.handlers or {})[job.kind]
        heartbeat = asyncio.create_task(self._heartbeat(job, spec))
        follow_up = self._follow_up(job)
        try:
            result = await spec.run(JobContext(job, self.store, self.worker_id))
        except asyncio.CancelledError:
            # Shutdown: leave the lease to expire so another worker retries it.
            raise
        except JobFailed as error:
            retry_at = self.store.now() + spec.retry_backoff * job.attempts if error.retry else None
            await self.store.fail(
                job.id,
                self.worker_id,
                code=error.code,
                message=error.message,
                retry_at=retry_at,
                follow_up=follow_up,
            )
        except Exception:
            logger.exception("Job %s (%s) failed", job.id, job.kind)
            await self.store.fail(
                job.id,
                self.worker_id,
                code="JobFailed",
                message="The background job failed unexpectedly.",
                retry_at=self.store.now() + spec.retry_backoff * job.attempts,
                follow_up=follow_up,
            )
        else:
            await self.store.succeed(job.id, self.worker_id, result, follow_up=follow_up)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _claim(self, slots: int) -> list[JobRecord]:
        claimed: list[JobRecord] = []
        # Claim per kind so each job gets its own handler's lease.
        for spec in (self.handlers or {}).values():
            if len(claimed) >= slots:
                break
            claimed += await self.store.claim(
                self.worker_id, kinds=[spec.kind], lease=spec.lease, limit=slots - len(claimed)
            )
        return claimed

    async def run_once(self) -> int:
        """Recover, schedule, and run every ready job to completion (tests, CLI)."""

        await self.store.recover_expired_leases()
        await self.ensure_schedules()
        total = 0
        while jobs := await self._claim(self.concurrency):
            await asyncio.gather(*(self.execute(job) for job in jobs))
            total += len(jobs)
        return total

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set; running jobs are cancelled on exit."""

        logger.info("Job worker %s started (%s handlers)", self.worker_id, len(self.handlers or {}))
        next_maintenance = 0.0
        loop = asyncio.get_running_loop()
        try:
            while not stop.is_set():
                try:
                    if loop.time() >= next_maintenance:
                        await self.store.recover_expired_leases()
                        await self.ensure_schedules()
                        next_maintenance = loop.time() + 30
                    slots = self.concurrency - len(self._running)
                    for job in await self._claim(slots) if slots > 0 else []:
                        task = asyncio.create_task(self.execute(job), name=f"job-{job.kind}")
                        self._running.add(task)
                        task.add_done_callback(self._running.discard)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Job worker poll failed; retrying")
                self._wake.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
        finally:
            for task in list(self._running):
                task.cancel()
            for task in list(self._running):
                with suppress(asyncio.CancelledError, Exception):
                    await task
            logger.info("Job worker %s stopped", self.worker_id)


# --- the in-process worker the API may run ----------------------------------

_in_process: Worker | None = None


def set_in_process_worker(worker: Worker | None) -> None:
    global _in_process
    _in_process = worker


def wake_worker() -> None:
    """Nudge the in-process worker, if any, after enqueueing a job."""

    if _in_process is not None:
        _in_process.wake()

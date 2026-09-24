"""Job queue semantics (in-memory store) and the worker loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from c2ai.jobs import JobFailed, MemoryJobStore, Worker
from c2ai.jobs.store import FAILED, QUEUED, RUNNING, SUCCEEDED
from c2ai.jobs.worker import HandlerSpec, Schedule
from c2ai.services.financial_ingestion_runner import FinancialIngestionRunResult


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def _worker(store, handlers, *, schedules=()):
    specs = {
        kind: HandlerSpec(kind, run, timedelta(minutes=1), timedelta(seconds=10))
        for kind, run in handlers.items()
    }
    return Worker(store, worker_id="w1", handlers=specs, schedules=list(schedules))


async def test_handler_result_and_stage_are_recorded():
    store = MemoryJobStore()

    async def run(ctx):
        await ctx.set_stage("halfway")
        assert (await store.get(ctx.job.id)).stage == "halfway"
        return {"answer": ctx.payload["x"] * 2}

    job = await store.enqueue("double", {"x": 21}, owner="alice")
    assert await _worker(store, {"double": run}).run_once() == 1
    done = await store.get(job.id)
    assert (done.status, done.result, done.stage, done.attempts) == (
        SUCCEEDED, {"answer": 42}, None, 1
    )
    assert done.expires_at is not None


async def test_dedupe_key_allows_one_unfinished_job():
    store = MemoryJobStore()
    first = await store.enqueue("tick", dedupe_key="k")
    assert await store.enqueue("tick", dedupe_key="k") is None
    await _worker(store, {"tick": AsyncMock(return_value=None)}).run_once()
    assert (await store.get(first.id)).status == SUCCEEDED
    assert await store.enqueue("tick", dedupe_key="k") is not None


async def test_retryable_failure_backs_off_until_attempts_run_out():
    clock = Clock()
    store = MemoryJobStore(clock)
    calls = 0

    async def flaky(_ctx):
        nonlocal calls
        calls += 1
        raise JobFailed("GitHubBusy", "try later", retry=True)

    job = await store.enqueue("flaky", max_attempts=2)
    worker = _worker(store, {"flaky": flaky})
    await worker.run_once()
    queued = await store.get(job.id)
    assert (queued.status, queued.error_code) == (QUEUED, "GitHubBusy")
    assert queued.run_after == clock.now + timedelta(seconds=10)

    await worker.run_once()  # not due yet
    assert calls == 1
    clock.advance(seconds=10)
    await worker.run_once()
    assert calls == 2
    assert (await store.get(job.id)).status == FAILED


async def test_non_retryable_failure_fails_immediately():
    store = MemoryJobStore()

    async def broken(_ctx):
        raise JobFailed("BadInput", "nope")

    job = await store.enqueue("broken", max_attempts=5)
    await _worker(store, {"broken": broken}).run_once()
    failed = await store.get(job.id)
    assert (failed.status, failed.error_code, failed.error_message) == (FAILED, "BadInput", "nope")


async def test_expired_lease_is_requeued_and_stale_worker_is_fenced():
    clock = Clock()
    store = MemoryJobStore(clock)
    job = await store.enqueue("slow", max_attempts=2)
    [claimed] = await store.claim("dead-worker", kinds=["slow"], lease=timedelta(seconds=30))
    assert claimed.status == RUNNING

    clock.advance(seconds=31)
    assert await store.recover_expired_leases() == 1
    assert (await store.get(job.id)).status == QUEUED

    [reclaimed] = await store.claim("w2", kinds=["slow"], lease=timedelta(seconds=30))
    assert reclaimed.attempts == 2
    # The first worker wakes up late: its writes no longer apply.
    assert await store.succeed(job.id, "dead-worker", {"late": True}) is False
    assert await store.renew(job.id, "dead-worker", timedelta(seconds=30)) is False
    assert await store.succeed(job.id, "w2", {"ok": True}) is True
    assert (await store.get(job.id)).result == {"ok": True}


async def test_lease_expiry_without_attempts_left_fails_the_job():
    clock = Clock()
    store = MemoryJobStore(clock)
    job = await store.enqueue("slow")
    await store.claim("dead-worker", kinds=["slow"], lease=timedelta(seconds=30))
    clock.advance(minutes=1)
    await store.recover_expired_leases()
    failed = await store.get(job.id)
    assert (failed.status, failed.error_code) == (FAILED, "JobLeaseExpired")


async def test_finished_jobs_expire_and_are_purged():
    clock = Clock()
    store = MemoryJobStore(clock)
    job = await store.enqueue("quick", retention=timedelta(minutes=15))
    await _worker(store, {"quick": AsyncMock(return_value=None)}).run_once()
    clock.advance(minutes=16)
    assert await store.get(job.id) is None
    assert await store.purge_expired() == 1
    assert store.jobs == []


async def test_periodic_job_reschedules_itself_after_success_and_failure():
    clock = Clock()
    store = MemoryJobStore(clock)
    outcomes = iter([None, RuntimeError("boom")])

    async def tick(_ctx):
        outcome = next(outcomes)
        if outcome:
            raise outcome
        return {"ok": True}

    schedule = Schedule(kind="tick", every=lambda: timedelta(minutes=5))
    worker = _worker(store, {"tick": tick}, schedules=[schedule])
    assert await worker.run_once() == 1
    [next_run] = [job for job in store.jobs if job.status == QUEUED]
    assert next_run.run_after == clock.now + timedelta(minutes=5)
    assert await worker.run_once() == 0  # not due; no duplicate scheduled
    clock.advance(minutes=5)
    assert await worker.run_once() == 1  # fails, still reschedules
    statuses = sorted(job.status for job in store.jobs)
    assert statuses == [FAILED, QUEUED, SUCCEEDED]


async def test_disabled_schedule_enqueues_nothing():
    store = MemoryJobStore()
    schedule = Schedule(kind="tick", every=lambda: timedelta(minutes=5), enabled=lambda: False)
    await _worker(store, {"tick": AsyncMock()}, schedules=[schedule]).run_once()
    assert store.jobs == []


@pytest.mark.parametrize("enabled", ["true", "false"])
async def test_financial_ingestion_is_a_registered_periodic_job(monkeypatch, enabled):
    monkeypatch.setenv("ATHENA_FINANCIAL_INGESTION_ENABLED", enabled)
    monkeypatch.setenv("ATHENA_FINANCIAL_POLL_SECONDS", "600")
    store = MemoryJobStore()
    run = AsyncMock(return_value=FinancialIngestionRunResult(lock_acquired=True))
    with patch("c2ai.jobs.handlers.financial.run_gateway_cost_ingestion_once", run):
        worker = Worker(store, worker_id="w1")
        await worker.run_once()

    ingest_jobs = [job for job in store.jobs if job.kind == "financial.ingest"]
    if enabled == "false":
        assert ingest_jobs == [] and run.await_count == 0
        return
    assert run.await_count == 1
    done = next(job for job in ingest_jobs if job.status == SUCCEEDED)
    assert done.result["lock_acquired"] is True
    [upcoming] = [job for job in ingest_jobs if job.status == QUEUED]
    assert timedelta(seconds=590) < upcoming.run_after - done.finished_at <= timedelta(seconds=601)

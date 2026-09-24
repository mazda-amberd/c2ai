"""The PostgreSQL job queue: SKIP LOCKED claims, dedupe, leases, fencing."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from c2ai.jobs import PostgresJobStore, Worker
from c2ai.jobs.store import FAILED, QUEUED, SUCCEEDED
from c2ai.jobs.worker import HandlerSpec, Schedule
from tests.integration.conftest import _SERVER_URL

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

LEASE = timedelta(seconds=30)


@pytest.fixture
async def store(session_factory):
    async with session_factory() as session:
        await session.execute(text("DELETE FROM jobs"))
        await session.commit()
    return PostgresJobStore(session_factory)


async def test_concurrent_workers_never_claim_the_same_job(store):
    for index in range(20):
        await store.enqueue("work", {"n": index})
    batches = await asyncio.gather(
        *(store.claim(f"w{i}", kinds=["work"], lease=LEASE, limit=4) for i in range(8))
    )
    claimed = [job.id for batch in batches for job in batch]
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


async def test_dedupe_index_admits_one_unfinished_job_per_key(store):
    first = await store.enqueue("tick", dedupe_key="schedule:tick")
    assert await store.enqueue("tick", dedupe_key="schedule:tick") is None
    [claimed] = await store.claim("w1", kinds=["tick"], lease=LEASE)
    assert await store.enqueue("tick", dedupe_key="schedule:tick") is None  # still running
    assert await store.succeed(claimed.id, "w1", {"ok": True})
    assert await store.enqueue("tick", dedupe_key="schedule:tick") is not None
    assert (await store.get(first.id)).result == {"ok": True}


async def test_expired_lease_requeues_and_fences_the_old_worker(store, session_factory):
    job = await store.enqueue("slow", max_attempts=2)
    await store.claim("dead", kinds=["slow"], lease=LEASE)
    async with session_factory() as session:
        await session.execute(
            text("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job.id},
        )
        await session.commit()

    assert await store.recover_expired_leases() == 1
    assert (await store.get(job.id)).status == QUEUED
    [again] = await store.claim("alive", kinds=["slow"], lease=LEASE)
    assert again.attempts == 2
    assert await store.succeed(job.id, "dead", {"late": True}) is False
    assert await store.fail(job.id, "alive", code="X", message="boom") is True
    assert (await store.get(job.id)).status == FAILED


async def test_retry_then_success_and_expiry(store, session_factory):
    job = await store.enqueue("flaky", max_attempts=2, retention=timedelta(seconds=60))
    [claimed] = await store.claim("w", kinds=["flaky"], lease=LEASE)
    past = store.now() - timedelta(seconds=1)
    assert await store.fail(claimed.id, "w", code="Busy", message="later", retry_at=past)
    [retried] = await store.claim("w", kinds=["flaky"], lease=LEASE)
    assert (retried.attempts, retried.error_code) == (2, "Busy")
    await store.succeed(retried.id, "w", None)
    assert (await store.get(job.id)).status == SUCCEEDED

    async with session_factory() as session:
        await session.execute(
            text("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :id"),
            {"id": job.id},
        )
        await session.commit()
    assert await store.get(job.id) is None
    assert await store.purge_expired() == 1


async def test_periodic_follow_up_is_written_with_the_finish(store):
    async def tick(_ctx):
        return {"ran": True}

    worker = Worker(
        store,
        worker_id="w",
        handlers={"tick": HandlerSpec("tick", tick, LEASE, timedelta(seconds=5))},
        schedules=[Schedule(kind="tick", every=lambda: timedelta(minutes=10))],
    )
    assert await worker.run_once() == 1
    assert await worker.run_once() == 0
    async with store._session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT status, run_after > now() + interval '9 minutes' FROM jobs"
                     " ORDER BY created_at")
            )
        ).all()
    assert [tuple(row) for row in rows] == [(SUCCEEDED, False), (QUEUED, True)]

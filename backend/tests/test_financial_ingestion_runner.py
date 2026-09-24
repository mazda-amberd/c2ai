"""Tests for advisory-locked gateway polling orchestration."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from c2ai.services.financial_ingestion_runner import (
    FinancialIngestionRunResult,
    financial_ingestion_advisory_lock,
    financial_ingestion_loop,
    run_gateway_cost_ingestion_once,
    start_financial_ingestion_scheduler,
)
from c2ai.services.gateway_cost_ingestion import GatewayCostIngestionResult

START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class _TransactionContext:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("transaction-started")

    async def __aexit__(self, *_args):
        self.events.append("transaction-ended")


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return _ConnectionContext(self.connection)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


@pytest.mark.asyncio
async def test_advisory_lock_is_released_with_dedicated_transaction():
    transaction_events = []
    connection = AsyncMock()
    connection.execute.return_value = _scalar_result(True)
    connection.begin = MagicMock(
        side_effect=lambda: _TransactionContext(transaction_events)
    )

    async with financial_ingestion_advisory_lock(_Engine(connection)) as acquired:
        assert acquired is True

    assert connection.execute.await_count == 1
    assert "pg_try_advisory_xact_lock" in str(connection.execute.await_args.args[0])
    assert transaction_events == ["transaction-started", "transaction-ended"]


@pytest.mark.asyncio
async def test_runner_polls_once_under_one_global_lock():
    lock_entries = []

    @asynccontextmanager
    async def acquired_lock():
        lock_entries.append("acquired")
        try:
            yield True
        finally:
            lock_entries.append("released")

    session = AsyncMock()
    session_factory = MagicMock(side_effect=lambda: _SessionContext(session))
    grafana = MagicMock()
    ingestion_result = GatewayCostIngestionResult(
        period_start=START,
        period_end=END,
        baseline_created=False,
        records=(MagicMock(), MagicMock()),
        observed_duration_seconds=Decimal("150"),
        attributed_duration_seconds=Decimal("123"),
        unmapped_namespaces=("unknown-app",),
    )

    with patch(
        "c2ai.services.financial_ingestion_runner.ingest_gateway_costs",
        new_callable=AsyncMock,
        return_value=ingestion_result,
    ) as ingest:
        result = await run_gateway_cost_ingestion_once(
            observed_at=END,
            grafana_client=grafana,
            session_factory=session_factory,
            lock_context_factory=acquired_lock,
        )

    assert result == FinancialIngestionRunResult(
        lock_acquired=True,
        period_start=START,
        period_end=END,
        records_processed=2,
        observed_duration_seconds=Decimal("150"),
        attributed_duration_seconds=Decimal("123"),
        unmapped_namespaces=("unknown-app",),
    )
    assert lock_entries == ["acquired", "released"]
    assert ingest.await_args.args == (session, grafana)
    assert ingest.await_args.kwargs["observed_at"] == END


@pytest.mark.asyncio
async def test_runner_skips_when_another_replica_holds_lock():
    @asynccontextmanager
    async def unavailable_lock():
        yield False

    result = await run_gateway_cost_ingestion_once(
        lock_context_factory=unavailable_lock,
    )

    assert result == FinancialIngestionRunResult(lock_acquired=False)


@pytest.mark.asyncio
async def test_grafana_failure_propagates_and_releases_lock():
    released = False

    @asynccontextmanager
    async def acquired_lock():
        nonlocal released
        try:
            yield True
        finally:
            released = True

    session = AsyncMock()
    session_factory = MagicMock(side_effect=lambda: _SessionContext(session))

    with (
        patch(
            "c2ai.services.financial_ingestion_runner.ingest_gateway_costs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("grafana unavailable"),
        ),
        pytest.raises(RuntimeError, match="grafana unavailable"),
    ):
        await run_gateway_cost_ingestion_once(
            grafana_client=MagicMock(),
            session_factory=session_factory,
            lock_context_factory=acquired_lock,
        )

    assert released is True


def test_scheduler_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ATHENA_FINANCIAL_INGESTION_ENABLED", "false")

    assert start_financial_ingestion_scheduler() == (None, None)


@pytest.mark.asyncio
async def test_scheduler_retries_after_a_failed_run(monkeypatch):
    stop_event = asyncio.Event()
    attempts = 0

    async def scheduled_run():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary gateway failure")
        stop_event.set()
        return FinancialIngestionRunResult(
            lock_acquired=True,
            period_start=START,
            period_end=END,
        )

    async def immediate_timeout(waitable, *, timeout):
        del timeout
        waitable.close()
        raise TimeoutError

    monkeypatch.setenv("ATHENA_FINANCIAL_POLL_SECONDS", "1")
    with (
        patch(
            "c2ai.services.financial_ingestion_runner.run_gateway_cost_ingestion_once",
            side_effect=scheduled_run,
        ),
        patch(
            "c2ai.services.financial_ingestion_runner.asyncio.wait_for",
            side_effect=immediate_timeout,
        ),
    ):
        await financial_ingestion_loop(stop_event)

    assert attempts == 2

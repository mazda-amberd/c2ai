"""Operational runner for advisory-locked Grafana LLM cost ingestion."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from c2ai.clients.grafana import GrafanaClient
from c2ai.db.session import AsyncSessionLocal, engine
from c2ai.services.gateway_cost_ingestion import ingest_gateway_costs

logger = logging.getLogger(__name__)

# Stable, application-specific signed BIGINT key for PostgreSQL advisory locks.
FINANCIAL_INGESTION_LOCK_KEY = 4_701_384_465_192_026
DEFAULT_POLL_SECONDS = 3600


@dataclass(frozen=True)
class FinancialIngestionRunResult:
    """Outcome of one Grafana source-namespace interval poll."""

    lock_acquired: bool
    period_start: datetime | None = None
    period_end: datetime | None = None
    baseline_created: bool = False
    records_processed: int = 0
    public_records_processed: int = 0
    observed_duration_seconds: Decimal = Decimal("0")
    attributed_duration_seconds: Decimal = Decimal("0")
    observed_public_tokens: Decimal = Decimal("0")
    attributed_public_tokens: Decimal = Decimal("0")
    unmapped_namespaces: tuple[str, ...] = ()
    unpriced_models: tuple[str, ...] = ()


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def financial_ingestion_enabled() -> bool:
    """Whether the in-process gateway polling scheduler should run."""
    return os.getenv("ATHENA_FINANCIAL_INGESTION_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@asynccontextmanager
async def financial_ingestion_advisory_lock(
    db_engine: AsyncEngine = engine,
) -> AsyncIterator[bool]:
    """Hold a transaction advisory lock on one dedicated connection."""
    async with db_engine.connect() as connection:
        async with connection.begin():
            result = await connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": FINANCIAL_INGESTION_LOCK_KEY},
            )
            yield bool(result.scalar_one())


async def run_gateway_cost_ingestion_once(
    *,
    observed_at: datetime | None = None,
    grafana_client: GrafanaClient | None = None,
    session_factory: Callable[[], AsyncSession] | None = None,
    lock_context_factory=None,
) -> FinancialIngestionRunResult:
    """Query one Grafana interval and persist it under the global lock."""
    lock_factory = lock_context_factory or financial_ingestion_advisory_lock
    async with lock_factory() as acquired:
        if not acquired:
            return FinancialIngestionRunResult(lock_acquired=False)

        grafana = grafana_client or GrafanaClient()
        make_session = session_factory or AsyncSessionLocal
        async with make_session() as db:
            ingestion = await ingest_gateway_costs(
                db,
                grafana,
                observed_at=observed_at,
            )

        return FinancialIngestionRunResult(
            lock_acquired=True,
            period_start=ingestion.period_start,
            period_end=ingestion.period_end,
            baseline_created=ingestion.baseline_created,
            records_processed=len(ingestion.records),
            public_records_processed=len(ingestion.public_records),
            observed_duration_seconds=ingestion.observed_duration_seconds,
            attributed_duration_seconds=ingestion.attributed_duration_seconds,
            observed_public_tokens=ingestion.observed_public_tokens,
            attributed_public_tokens=ingestion.attributed_public_tokens,
            unmapped_namespaces=ingestion.unmapped_namespaces,
            unpriced_models=ingestion.unpriced_models,
        )


async def financial_ingestion_loop(stop_event: asyncio.Event) -> None:
    """Poll immediately and then at the configured interval."""
    poll_seconds = _positive_env_int(
        "ATHENA_FINANCIAL_POLL_SECONDS",
        DEFAULT_POLL_SECONDS,
    )
    while not stop_event.is_set():
        try:
            result = await run_gateway_cost_ingestion_once()
            if not result.lock_acquired:
                logger.info(
                    "Financial ingestion skipped: another replica holds the lock"
                )
            elif result.baseline_created:
                logger.info(
                    "Financial ingestion stored the initial LLM gateway baseline at %s",
                    result.period_end.isoformat() if result.period_end else "unknown",
                )
            else:
                logger.info(
                    "Financial ingestion completed: private_records=%s "
                    "public_records=%s "
                    "observed_duration_seconds=%s attributed_duration_seconds=%s "
                    "attributed_public_tokens=%s period=%s..%s",
                    result.records_processed,
                    result.public_records_processed,
                    result.observed_duration_seconds,
                    result.attributed_duration_seconds,
                    result.attributed_public_tokens,
                    result.period_start.isoformat()
                    if result.period_start
                    else "unknown",
                    result.period_end.isoformat() if result.period_end else "unknown",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Financial ingestion failed; stored costs were left intact"
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:  # the poll interval elapsed; poll again
            continue


def start_financial_ingestion_scheduler() -> tuple[
    asyncio.Task | None, asyncio.Event | None
]:
    """Start gateway polling when enabled by configuration."""
    if not financial_ingestion_enabled():
        logger.info("Financial ingestion scheduler is disabled")
        return None, None
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        financial_ingestion_loop(stop_event),
        name="athena-financial-ingestion",
    )
    return task, stop_event


async def stop_financial_ingestion_scheduler(
    task: asyncio.Task | None,
    stop_event: asyncio.Event | None,
) -> None:
    """Signal and cancel the scheduler without delaying application shutdown."""
    if task is None:
        return
    if stop_event is not None:
        stop_event.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

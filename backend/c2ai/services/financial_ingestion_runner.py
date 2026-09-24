"""One advisory-locked Grafana LLM cost poll (scheduled as the ``financial.ingest`` job)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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

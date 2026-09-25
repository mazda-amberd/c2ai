"""FastAPI application factory for the C2AI (Athena) backend."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from c2ai.api.auth_session import router as auth_session_router
from c2ai.api.base import router as base_router
from c2ai.api.deployments import router as deployments_router
from c2ai.api.financial import router as financial_router
from c2ai.api.github_connections import router as github_connections_router
from c2ai.api.grafana import router as grafana_router
from c2ai.api.logs import router as logs_router
from c2ai.api.metrics import router as metrics_v2_router
from c2ai.api.ops import router as ops_router
from c2ai.api.registered_applications import router as registered_applications_router
from c2ai.api.troubleshooting import router as troubleshooting_router
from c2ai.api.users import router as users_router
from c2ai.clients.http import close_http_clients
from c2ai.config import check_startup_settings, get_settings
from c2ai.core.exception_handlers import attach_exception_handlers
from c2ai.core.frontend import setup_frontend_serving
from c2ai.core.observability import RequestContextMiddleware
from c2ai.db.migrate import schema_status
from c2ai.db.session import AsyncSessionLocal
from c2ai.jobs import Worker, get_job_store
from c2ai.jobs.worker import set_in_process_worker

logger = logging.getLogger(__name__)


async def _check_schema() -> None:
    """Refuse to serve a database that is behind this build's migrations."""

    async with AsyncSessionLocal() as db:
        status = await schema_status(db)
    if status.current:
        return
    message = (
        f"The database schema is behind this build (pending: {', '.join(status.pending)}). "
        "Run `python -m c2ai.db.migrate`."
    )
    if get_settings().allow_pending_migrations:
        logger.error("%s Starting anyway (C2AI_ALLOW_PENDING_MIGRATIONS).", message)
        return
    raise RuntimeError(message)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    check_startup_settings()
    await _check_schema()
    settings = get_settings()
    worker_task = None
    stop = asyncio.Event()
    if settings.run_worker:
        worker = Worker(get_job_store(), poll_seconds=settings.worker_poll_seconds)
        set_in_process_worker(worker)
        worker_task = asyncio.create_task(worker.run(stop), name="c2ai-job-worker")
    try:
        yield
    finally:
        stop.set()
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
            set_in_process_worker(None)
        await close_http_clients()


def create_app(*, serve_frontend: bool = True) -> FastAPI:
    """Build the API with its routers, error handlers, and optional SPA serving."""

    application = FastAPI(title="C2AI Athena Service", lifespan=_lifespan)
    application.add_middleware(RequestContextMiddleware)

    origins = get_settings().cors_origin_list
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )

    for router in (
        ops_router,
        base_router,
        grafana_router,
        github_connections_router,
        auth_session_router,
        users_router,
        deployments_router,
        logs_router,
        metrics_v2_router,
        registered_applications_router,
        financial_router,
        troubleshooting_router,
    ):
        application.include_router(router)

    attach_exception_handlers(application)
    if serve_frontend:
        # Registered last: the SPA catch-all must not shadow any API route.
        setup_frontend_serving(application)
    return application


app = create_app(serve_frontend=get_settings().serve_frontend)

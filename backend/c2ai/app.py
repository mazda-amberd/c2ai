"""FastAPI application factory for the C2AI (Athena) backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

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
from c2ai.api.registered_applications import router as registered_applications_router
from c2ai.api.troubleshooting import router as troubleshooting_router
from c2ai.api.users import router as users_router
from c2ai.clients.http import close_http_clients
from c2ai.config import check_startup_settings, get_settings
from c2ai.core.background import cancel_background_tasks
from c2ai.core.exception_handlers import attach_exception_handlers
from c2ai.core.frontend import setup_frontend_serving
from c2ai.services.financial_ingestion_runner import (
    start_financial_ingestion_scheduler,
    stop_financial_ingestion_scheduler,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    check_startup_settings()
    scheduler_task, scheduler_stop = start_financial_ingestion_scheduler()
    try:
        yield
    finally:
        await stop_financial_ingestion_scheduler(scheduler_task, scheduler_stop)
        await cancel_background_tasks()
        await close_http_clients()


def create_app(*, serve_frontend: bool = True) -> FastAPI:
    """Build the API with its routers, error handlers, and optional SPA serving."""

    application = FastAPI(title="C2AI Athena Service", lifespan=_lifespan)

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

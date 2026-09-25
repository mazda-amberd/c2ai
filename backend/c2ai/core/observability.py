"""Correlation ids, structured logs, and Prometheus metrics for the service.

* Every request gets an id (the caller's ``X-Request-ID`` or a new one),
  echoed in the response and attached to every log line written while
  handling it; job runs get ``job:<kind>:<id>`` the same way.
* ``C2AI_LOG_FORMAT=json`` switches the console to one JSON object per line.
* ``/metrics`` (``c2ai.api.ops``) exposes request, outbound-call, job and
  deployment metrics; the gauges that live in the database are read when
  Prometheus scrapes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "c2ai_http_requests_total",
    "HTTP requests handled, by route template and status.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "c2ai_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["method", "route"],
    registry=REGISTRY,
)
OUTBOUND_REQUESTS = Counter(
    "c2ai_outbound_requests_total",
    "Calls to external services (GitHub, Grafana, registries, ...), by host and status.",
    ["host", "status"],
    registry=REGISTRY,
)
OUTBOUND_DURATION = Histogram(
    "c2ai_outbound_request_duration_seconds",
    "Latency of calls to external services, by host.",
    ["host"],
    registry=REGISTRY,
)
GITHUB_RATE_LIMIT_REMAINING = Gauge(
    "c2ai_github_rate_limit_remaining",
    "Requests left in the current GitHub API rate-limit window (last response seen).",
    registry=REGISTRY,
)
JOB_RUNS = Counter(
    "c2ai_job_runs_total",
    "Background job executions, by kind and outcome.",
    ["kind", "outcome"],
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "c2ai_job_duration_seconds",
    "Background job run time, by kind.",
    ["kind"],
    registry=REGISTRY,
)
JOB_LEASES_EXPIRED = Counter(
    "c2ai_job_leases_expired_total",
    "Jobs whose worker stopped before finishing them (lease expired).",
    registry=REGISTRY,
)
JOBS = Gauge(
    "c2ai_jobs",
    "Jobs in the queue, by kind and status (read at scrape time).",
    ["kind", "status"],
    registry=REGISTRY,
)
JOB_OLDEST_READY_SECONDS = Gauge(
    "c2ai_job_oldest_ready_seconds",
    "Age of the oldest job that is due but not yet claimed, by kind.",
    ["kind"],
    registry=REGISTRY,
)
OPEN_OPERATIONS = Gauge(
    "c2ai_deployment_operations_open",
    "Unfinished deployment operations, by kind and dispatch state.",
    ["kind", "dispatch_state"],
    registry=REGISTRY,
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


class CorrelationIdFilter(logging.Filter):
    """Adds ``record.correlation_id`` for the log formats."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class RequestContextMiddleware:
    """Correlation id + request metrics for every HTTP request (pure ASGI)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1").strip()
        request_id = incoming[:64] if incoming else new_correlation_id()
        token = correlation_id.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [
                    *message["headers"],
                    (b"x-request-id", request_id.encode("latin-1")),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope.get("method", "")
            HTTP_REQUESTS.labels(method, template, str(status_code)).inc()
            HTTP_DURATION.labels(method, template).observe(time.perf_counter() - started)
            correlation_id.reset(token)


async def record_outbound_response(response) -> None:
    """httpx response hook: outbound call metrics and GitHub's remaining quota."""

    request = response.request
    host = request.url.host or "unknown"
    OUTBOUND_REQUESTS.labels(host, str(response.status_code)).inc()
    started = request.extensions.get("c2ai_started")
    if started is not None:
        OUTBOUND_DURATION.labels(host).observe(time.perf_counter() - started)
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and "github" in host:
        try:
            GITHUB_RATE_LIMIT_REMAINING.set(float(remaining))
        except ValueError:
            pass


async def mark_outbound_start(request) -> None:
    request.extensions["c2ai_started"] = time.perf_counter()

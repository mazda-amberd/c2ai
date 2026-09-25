"""Shared, pooled ``httpx.AsyncClient`` instances for outbound calls.

Every outbound integration (GitHub, Grafana, registries, the secret broker,
instance version probes) borrows a client from here instead of opening a new
one per call, so TLS connections are reused and retry/timeout policy lives in
one place.

Clients are pooled per event loop (connection pools cannot cross loops) and
per timeout; the API closes them on shutdown with ``close_http_clients()``.
Tests route every request through ``httpx.MockTransport`` with
``use_http_transport()`` instead of patching ``httpx.AsyncClient``.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import httpx

from c2ai.core.observability import mark_outbound_start, record_outbound_response

DEFAULT_TIMEOUT = 15.0
# Connection-level failures (DNS, refused, reset before a response) are
# retried; HTTP error statuses never are, because dispatches are not idempotent.
_CONNECT_RETRIES = 2
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)

_pools: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[float | None, bool], httpx.AsyncClient]
] = weakref.WeakKeyDictionary()
_transport_override: httpx.AsyncBaseTransport | None = None


def _new_client(timeout: float | None, follow_redirects: bool) -> httpx.AsyncClient:
    transport = _transport_override or httpx.AsyncHTTPTransport(
        retries=_CONNECT_RETRIES, limits=_LIMITS
    )
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        follow_redirects=follow_redirects,
        event_hooks={
            "request": [mark_outbound_start],
            "response": [record_outbound_response],
        },
    )


def get_http_client(
    timeout: float | None = DEFAULT_TIMEOUT, *, follow_redirects: bool = False
) -> httpx.AsyncClient:
    """Return the pooled client for the running loop (never close it yourself)."""

    loop = asyncio.get_running_loop()
    pool = _pools.setdefault(loop, {})
    key = (timeout, follow_redirects)
    client = pool.get(key)
    if client is None or client.is_closed:
        client = _new_client(timeout, follow_redirects)
        pool[key] = client
    return client


@asynccontextmanager
async def http_client(
    timeout: float | None = DEFAULT_TIMEOUT, *, follow_redirects: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    """``async with http_client(15) as client:`` — a drop-in for ``httpx.AsyncClient``
    that borrows the shared pool instead of opening and closing connections."""

    yield get_http_client(timeout, follow_redirects=follow_redirects)


async def close_http_clients() -> None:
    """Close the running loop's clients (API shutdown, worker shutdown)."""

    loop = asyncio.get_running_loop()
    pool = _pools.pop(loop, {})
    for client in pool.values():
        await client.aclose()


@contextmanager
def use_http_transport(transport: httpx.AsyncBaseTransport) -> Iterator[None]:
    """Route every pooled client through ``transport`` (tests)."""

    global _transport_override
    previous = _transport_override
    _transport_override = transport
    _pools.clear()
    try:
        yield
    finally:
        _transport_override = previous
        _pools.clear()

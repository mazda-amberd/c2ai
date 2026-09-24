"""The shared outbound HTTP pool: reuse, per-timeout clients, test transport."""

import httpx
import pytest

from c2ai.clients.http import close_http_clients, get_http_client, http_client


@pytest.mark.asyncio
async def test_same_client_is_reused_per_timeout():
    first = get_http_client(15.0)
    assert get_http_client(15.0) is first
    assert get_http_client(30.0) is not first
    async with http_client(15.0) as borrowed:
        assert borrowed is first
    assert not first.is_closed  # borrowing never closes the pool
    await close_http_clients()
    assert first.is_closed
    assert get_http_client(15.0) is not first
    await close_http_clients()


@pytest.mark.asyncio
async def test_http_mock_routes_pooled_clients(http_mock):
    sent = http_mock(lambda request: httpx.Response(200, json={"ok": True}))
    async with http_client() as client:
        response = await client.get("https://api.example.test/ping")
    assert response.json() == {"ok": True}
    assert [str(request.url) for request in sent] == ["https://api.example.test/ping"]

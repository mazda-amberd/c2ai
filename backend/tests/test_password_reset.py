"""Forgot password without a database: the endpoint, the email client, the password."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from c2ai.app import app
from c2ai.clients.postmark import EmailDeliveryFailed, send_email
from c2ai.jobs import MemoryJobStore, get_job_notifier, get_job_store
from c2ai.services.password_reset import ANSWER, temporary_password


@pytest.fixture
def queue():
    store = MemoryJobStore()
    woken = []
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_notifier] = lambda: lambda: woken.append(True)
    yield store, woken
    app.dependency_overrides.pop(get_job_store, None)
    app.dependency_overrides.pop(get_job_notifier, None)


@pytest.fixture
def email(monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "pm-token")
    monkeypatch.setenv("AMBERD_REPORT_FROM_EMAIL", "c2ai@amberd.ai")
    monkeypatch.setenv("AMBERD_REPORT_REPLY_TO", "support@amberd.ai")


async def test_the_endpoint_queues_the_reset_and_answers_before_looking(test_client, queue):
    store, woken = queue
    for address in ("  alice@example.com ", "", "Alice@Example.com"):
        response = test_client.post("/auth/forgot-password", json={"email": address})
        assert response.status_code == 202
        assert response.json() == {"ok": True, "detail": ANSWER}

    [job] = store.jobs
    assert job.kind == "auth.password_reset"
    assert job.payload == {"address": "alice@example.com"}
    # A second request for the same address while one is queued adds nothing.
    assert job.dedupe_key == "password-reset:alice@example.com"
    assert len(woken) == 2


def test_temporary_passwords_are_three_groups_without_lookalikes():
    passwords = {temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert re.fullmatch(r"[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}", password)
        assert not set(password) & set("0O1lIoi")


async def test_postmark_gets_one_message_per_send(http_mock, email):
    sent = http_mock(lambda request: httpx.Response(200, json={"MessageID": "m-7"}))
    assert await send_email(to="a@example.com", subject="Hi", text_body="t", html_body="<p>t</p>") == "m-7"
    [request] = sent
    assert request.headers["X-Postmark-Server-Token"] == "pm-token"
    assert json.loads(request.content) == {
        "From": "c2ai@amberd.ai",
        "To": "a@example.com",
        "Subject": "Hi",
        "TextBody": "t",
        "HtmlBody": "<p>t</p>",
        "MessageStream": "outbound",
        "ReplyTo": "support@amberd.ai",
    }


async def test_amberd_agents_settings_work_unchanged(http_mock, monkeypatch):
    """An Amberd Agents (or dealership BOOKMARK_*) .env configures C2AI as is."""

    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "pm-token")
    monkeypatch.setenv("BOOKMARK_REPORT_FROM_EMAIL", "noreply@amberd.ai")
    monkeypatch.setenv("BOOKMARK_REPORT_MESSAGE_STREAM", "transactional")
    monkeypatch.setenv("POSTMARK_EMAIL_ENDPOINT", "https://postmark.test/email")
    monkeypatch.setenv("POSTMARK_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("POSTMARK_READ_TIMEOUT_SECONDS", "9")
    sent = http_mock(lambda request: httpx.Response(200, json={"MessageID": "m"}))

    await send_email(to="a@example.com", subject="Hi", text_body="t")

    [request] = sent
    assert str(request.url) == "https://postmark.test/email"
    message = json.loads(request.content)
    assert (message["From"], message["MessageStream"]) == ("noreply@amberd.ai", "transactional")
    assert "ReplyTo" not in message
    assert request.extensions["timeout"] == {"connect": 3.0, "read": 9.0, "write": 9.0, "pool": 9.0}


@pytest.mark.parametrize(
    "response, reason",
    [
        (httpx.Response(422, json={"ErrorCode": 406, "Message": "Inactive recipient"}), "406"),
        (httpx.Response(200, json={}), "no MessageID"),
    ],
)
async def test_postmark_refusals_raise(http_mock, email, response, reason):
    http_mock(lambda request: response)
    with pytest.raises(EmailDeliveryFailed, match=reason):
        await send_email(to="a@example.com", subject="Hi", text_body="t")


async def test_nothing_is_sent_without_configuration(http_mock):
    sent = http_mock(lambda request: httpx.Response(200, json={"MessageID": "m"}))
    with pytest.raises(EmailDeliveryFailed, match="not configured"):
        await send_email(to="a@example.com", subject="Hi", text_body="t")
    assert sent == []

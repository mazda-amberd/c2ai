"""The default account, forgot password and the temporary-password rule on PostgreSQL."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from c2ai.app import app
from c2ai.crud import user as crud_user
from c2ai.db.session import get_db_session
from c2ai.jobs import PostgresJobStore, Worker, get_job_notifier, get_job_store
from c2ai.services.password_reset import ANSWER
from tests.integration.conftest import _SERVER_URL

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

PASSWORD = "Str0ng!pass"
ALICE = "alice@example.com"
TEMPORARY = re.compile(r"Temporary password: ([A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4})")


async def _add_user(db, identifier, *, needs_password_reset=False):
    await crud_user.create_user(
        db,
        {
            "identifier": identifier,
            "password": PASSWORD,
            "first_name": "Alice",
            "last_name": "Example",
            "metadata_": {"user_type": "Admin", "needs_password_reset": needs_password_reset},
            "created_by": "system",
        },
    )


@pytest.fixture
async def client(session_factory):
    async with session_factory() as db:
        await db.execute(text("TRUNCATE users, revoked_tokens, login_failures, jobs CASCADE"))
        await _add_user(db, ALICE)
        await _add_user(db, "bob")  # a username that is not an address
        await db.commit()

    async def _session():
        async with session_factory() as session:
            yield session

    store = PostgresJobStore(session_factory)

    async def run_queued_jobs():
        await Worker(store, schedules=[], session_factory=session_factory).run_once()

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_job_notifier] = lambda: run_queued_jobs
    try:
        yield TestClient(app)
    finally:
        for dependency in (get_db_session, get_job_store, get_job_notifier):
            app.dependency_overrides.pop(dependency, None)


class Outbox(list):
    """Every message sent to Postmark; ``refuse`` makes Postmark reject them."""

    refuse = False


@pytest.fixture
def postmark(http_mock, monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "pm-token")
    monkeypatch.setenv("AMBERD_REPORT_FROM_EMAIL", "c2ai@amberd.ai")
    monkeypatch.setenv("C2AI_PUBLIC_URL", "https://c2ai.example.com")
    outbox = Outbox()

    def handler(request: httpx.Request) -> httpx.Response:
        outbox.append(request)
        if outbox.refuse:
            return httpx.Response(422, json={"ErrorCode": 406, "Message": "Inactive recipient"})
        return httpx.Response(200, json={"MessageID": "msg-1"})

    http_mock(handler)
    return outbox


def _login(client, identifier, password):
    return client.post(
        "/auth/login",
        json={"identifier": identifier, "password": password, "set_cookie": False},
    )


def _bearer(response):
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _forgot(client, email):
    response = client.post("/auth/forgot-password", json={"email": email})
    assert response.status_code == 202, response.text
    assert response.json() == {"ok": True, "detail": ANSWER}


def _temporary_password(request: httpx.Request) -> str:
    return TEMPORARY.search(json.loads(request.content)["TextBody"]).group(1)


async def test_an_empty_database_gets_admin_at_amberd(client, session_factory):
    async with session_factory() as db:
        assert await crud_user.ensure_default_admin(db) is False  # somebody exists
        await db.execute(text("TRUNCATE users CASCADE"))
        assert await crud_user.ensure_default_admin(db) is True
        await db.commit()
        assert await crud_user.ensure_default_admin(db) is False  # never twice

    admin = _bearer(_login(client, "admin@amberd.ai", "admin@amberd.ai"))
    me = client.get("/auth/whoami", headers=admin).json()
    assert me["metadata"]["user_type"] == "Admin"
    assert me["metadata"]["needs_password_reset"] is False
    # Not made to change it: the only account must not be one step from a lockout.
    assert client.get("/users/", headers=admin).status_code == 200


async def test_forgot_password_emails_a_password_that_must_be_replaced(client, postmark):
    before = _bearer(_login(client, ALICE, PASSWORD))

    _forgot(client, ALICE)

    [email] = postmark
    assert email.url == "https://api.postmarkapp.com/email"
    assert email.headers["X-Postmark-Server-Token"] == "pm-token"
    message = json.loads(email.content)
    assert (message["From"], message["To"]) == ("c2ai@amberd.ai", ALICE)
    assert message["Subject"] == "Your C2AI password"
    assert "Sign in at https://c2ai.example.com" in message["TextBody"]
    temporary = _temporary_password(email)
    assert temporary in message["HtmlBody"]

    # The old password and every session stop working at once.
    assert client.get("/auth/whoami", headers=before).status_code == 401
    assert _login(client, ALICE, PASSWORD).status_code == 401

    session = _bearer(_login(client, ALICE, temporary))
    me = client.get("/auth/whoami", headers=session).json()
    assert me["first_name"] == "Alice"
    assert me["metadata"]["needs_password_reset"] is True
    blocked = client.get("/users/", headers=session)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "PasswordChangeRequired"

    chosen = client.patch(
        "/users/update_password", json={"new_password": "N3w!secret"}, headers=session
    )
    assert chosen.status_code == 200, chosen.text
    own = _bearer(_login(client, ALICE, "N3w!secret"))
    assert client.get("/auth/whoami", headers=own).json()["metadata"]["needs_password_reset"] is False
    assert client.get("/users/", headers=own).status_code == 200


async def test_the_answer_is_the_same_whatever_happens(client, postmark):
    _forgot(client, "nobody@example.com")
    _forgot(client, "")
    _forgot(client, "bob")  # an account, but no address to send to
    assert postmark == []
    assert _login(client, "bob", PASSWORD).status_code == 200

    _forgot(client, ALICE)
    _forgot(client, ALICE)  # inside the five-minute cooldown
    assert len(postmark) == 1
    assert _login(client, ALICE, _temporary_password(postmark[0])).status_code == 200


async def test_a_reset_that_cannot_be_delivered_changes_nothing(client, postmark):
    postmark.refuse = True
    _forgot(client, ALICE)
    assert len(postmark) == 1
    assert _login(client, ALICE, PASSWORD).status_code == 200

    # A failed send starts no cooldown: asking again works once email does.
    postmark.refuse = False
    _forgot(client, ALICE)
    assert len(postmark) == 2
    assert _login(client, ALICE, _temporary_password(postmark[1])).status_code == 200


async def test_without_email_configured_nothing_is_reset(client, http_mock):
    sent = http_mock(lambda request: httpx.Response(500))
    _forgot(client, ALICE)
    assert sent == []
    assert _login(client, ALICE, PASSWORD).status_code == 200

"""The Users page API (``/api/users``) on PostgreSQL - Amberd Agents' rules."""

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
from tests.integration.conftest import _SERVER_URL

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

PASSWORD = "Str0ng!pass"
BOSS = "boss@example.com"
TEMPORARY = re.compile(r"[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}")
NEWCOMER = {
    "first_name": " New ",
    "last_name": "Person",
    "email": "New.Person@Example.com",
    "role": "user",
}


async def _account(db, identifier, user_type="Admin"):
    await crud_user.create_user(
        db,
        {
            "identifier": identifier,
            "password": PASSWORD,
            "first_name": identifier.split("@")[0].title(),
            "last_name": "Example",
            "metadata_": {"user_type": user_type, "needs_password_reset": False},
            "created_by": "system",
        },
    )


@pytest.fixture
async def client(session_factory):
    async with session_factory() as db:
        await db.execute(text("TRUNCATE users, revoked_tokens, login_failures CASCADE"))
        await _account(db, BOSS)
        await _account(db, "viewer@example.com", "User")
        await db.commit()

    async def _session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def outbox(http_mock, monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "pm-token")
    monkeypatch.setenv("AMBERD_REPORT_FROM_EMAIL", "noreply@amberd.ai")
    return http_mock(lambda request: httpx.Response(200, json={"MessageID": "m-1"}))


def _login(client, identifier, password=PASSWORD):
    return client.post(
        "/auth/login",
        json={"identifier": identifier, "password": password, "set_cookie": False},
    )


def _bearer(client, identifier, password=PASSWORD):
    response = _login(client, identifier, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _emailed_password(request: httpx.Request) -> str:
    return TEMPORARY.search(json.loads(request.content)["TextBody"]).group(0)


async def test_adding_someone_emails_them_a_temporary_password(client, outbox):
    boss = _bearer(client, BOSS)

    added = client.post("/api/users", json=NEWCOMER, headers=boss)

    assert added.status_code == 201, added.text
    person = added.json()
    assert (person["email"], person["name"], person["role_label"]) == (
        "new.person@example.com",
        "New Person",
        "User",
    )
    assert person["status"] == "invited"
    assert (person["email_sent"], person["temporary_password"]) == (True, "")
    [email] = outbox
    message = json.loads(email.content)
    assert (message["To"], message["Subject"]) == ("new.person@example.com", "Your C2AI account")
    assert "An account has been created for you on C2AI." in message["TextBody"]

    # They sign in with it (any capitalisation) and must choose their own.
    newcomer = _bearer(client, "New.Person@example.com", _emailed_password(email))
    me = client.get("/auth/whoami", headers=newcomer).json()
    assert me["metadata"]["needs_password_reset"] is True

    listing = client.get("/api/users", headers=boss).json()
    assert [u["email"] for u in listing["users"]] == [
        BOSS,
        "new.person@example.com",
        "viewer@example.com",
    ]  # admins first, then by name
    assert (listing["admins"], listing["email_configured"]) == (1, True)
    assert listing["roles"] == [
        {"value": "user", "label": "User"},
        {"value": "admin", "label": "Admin"},
    ]

    again = client.post("/api/users", json=NEWCOMER, headers=boss)
    assert again.status_code == 409
    assert again.json()["detail"].startswith("new.person@example.com is already here.")
    bad = client.post("/api/users", json={**NEWCOMER, "email": "not-an-address"}, headers=boss)
    assert bad.status_code == 422
    assert "does not look like an email address" in bad.json()["detail"]


async def test_without_email_the_password_is_shown_once(client):
    boss = _bearer(client, BOSS)

    added = client.post("/api/users", json=NEWCOMER, headers=boss).json()

    assert added["email_sent"] is False
    assert "Email is not configured" in added["email_error"]
    assert TEMPORARY.fullmatch(added["temporary_password"])
    assert _login(client, "new.person@example.com", added["temporary_password"]).status_code == 200
    listing = client.get("/api/users", headers=boss).json()
    assert listing["email_configured"] is False
    assert "temporary_password" not in listing["users"][1]


async def test_editing_resetting_and_removing(client, outbox):
    boss = _bearer(client, BOSS)
    viewer = next(
        u for u in client.get("/api/users", headers=boss).json()["users"]
        if u["email"] == "viewer@example.com"
    )
    old_session = _bearer(client, "viewer@example.com")

    edited = client.put(
        f"/api/users/{viewer['user_id']}",
        json={"first_name": "Vera", "last_name": "Viewer", "email": "vera@example.com",
              "role": "admin"},
        headers=boss,
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["name"], edited.json()["role"]) == ("Vera Viewer", "admin")
    assert _login(client, "vera@example.com").status_code == 200  # same password

    reset = client.post(f"/api/users/{viewer['user_id']}/reset-password", headers=boss)
    assert reset.status_code == 200, reset.text
    assert reset.json()["status"] == "invited"
    [email] = outbox
    assert json.loads(email.content)["Subject"] == "Your new C2AI password"
    assert _login(client, "vera@example.com").status_code == 401  # old one stopped
    assert _login(client, "vera@example.com", _emailed_password(email)).status_code == 200
    assert client.get("/auth/whoami", headers=old_session).status_code == 401

    removed = client.delete(f"/api/users/{viewer['user_id']}", headers=boss)
    assert removed.json() == {"deleted": viewer["user_id"], "email": "vera@example.com"}
    assert _login(client, "vera@example.com", _emailed_password(email)).status_code == 401
    gone = client.delete(f"/api/users/{viewer['user_id']}", headers=boss)
    assert gone.status_code == 404


async def test_the_last_admin_and_your_own_account_are_protected(client, session_factory):
    boss = _bearer(client, BOSS)
    me = client.get("/api/users", headers=boss).json()["users"][0]

    demote = client.put(
        f"/api/users/{me['user_id']}",
        json={"first_name": "Boss", "last_name": "Example", "email": BOSS, "role": "user"},
        headers=boss,
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "CannotUpdateLastAdminToUser"
    itself = client.delete(f"/api/users/{me['user_id']}", headers=boss)
    assert itself.status_code == 409
    assert itself.json()["detail"] == (
        "That is the account you are signed in as. Ask another Admin to remove it."
    )

    # With a second Admin, that one can remove the first.
    async with session_factory() as db:
        await _account(db, "second@example.com")
        await db.commit()
    second = _bearer(client, "second@example.com")
    assert client.delete(f"/api/users/{me['user_id']}", headers=second).status_code == 200


async def test_only_admins_reach_the_users_page(client):
    viewer = _bearer(client, "viewer@example.com")
    response = client.get("/api/users", headers=viewer)
    assert response.status_code == 403
    assert response.json()["code"] == "AdminPrivilegesRequired"


async def test_changing_your_own_address_keeps_you_signed_in(client):
    signed_in = client.post("/auth/login", json={"identifier": BOSS, "password": PASSWORD})
    assert signed_in.status_code == 200
    me = client.get("/api/users").json()["users"][0]

    moved = client.put(
        f"/api/users/{me['user_id']}",
        json={"first_name": "Boss", "last_name": "Example", "email": "chief@example.com",
              "role": "admin"},
    )

    assert moved.status_code == 200, moved.text
    assert client.get("/auth/whoami").json()["identifier"] == "chief@example.com"

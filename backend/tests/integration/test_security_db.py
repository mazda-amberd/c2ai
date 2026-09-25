"""Session revocation, login throttling and credential re-encryption on PostgreSQL."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from c2ai.app import app
from c2ai.crud import user as crud_user
from c2ai.db.session import get_db_session
from c2ai.security import crypto
from c2ai.security.rotate import key_usage, reencrypt_all
from tests.integration.conftest import _SERVER_URL

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

PASSWORD = "Str0ng!pass"


@pytest.fixture
async def client(session_factory):
    async with session_factory() as db:
        await db.execute(text("TRUNCATE users, revoked_tokens, login_failures CASCADE"))
        await db.commit()
        await crud_user.create_user(
            db,
            {
                "identifier": "alice",
                "password": PASSWORD,
                "first_name": "Alice",
                "last_name": "Admin",
                "metadata_": {"user_type": "Admin", "needs_password_reset": False},
                "created_by": "system",
            },
        )
        await db.commit()

    async def _session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def _login(client, password=PASSWORD):
    return client.post(
        "/auth/login",
        json={"identifier": "alice", "password": password, "set_cookie": False},
    )


def _bearer(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_logout_revokes_only_that_session(client):
    first, second = _bearer(_login(client)), _bearer(_login(client))
    assert client.post("/auth/logout", headers=first).status_code == 200
    assert client.get("/auth/whoami", headers=first).status_code == 401
    assert client.get("/auth/whoami", headers=second).status_code == 200


async def test_password_reset_signs_out_every_session(client):
    session = _bearer(_login(client))
    other = client.post(
        "/users/reset_password/alice", headers=_bearer(_login(client))
    )
    assert other.status_code == 200, other.text
    assert client.get("/auth/whoami", headers=session).status_code == 401


async def test_own_password_change_keeps_this_browser_signed_in(client):
    old = _bearer(_login(client))
    response = client.patch(
        "/users/update_password", json={"new_password": "N3w!secret"}, headers=old
    )
    assert response.status_code == 200, response.text
    # The response carries a fresh session cookie for this client.
    assert client.get("/auth/whoami").status_code == 200
    client.cookies.clear()
    assert client.get("/auth/whoami", headers=old).status_code == 401
    assert _login(client, "N3w!secret").status_code == 200


async def test_failures_are_counted_in_the_database(client, monkeypatch, session_factory):
    monkeypatch.setenv("C2AI_LOGIN_MAX_FAILURES_PER_ACCOUNT", "3")
    assert [_login(client, "wrong").status_code for _ in range(3)] == [401, 401, 401]
    blocked = _login(client)
    assert blocked.status_code == 429
    async with session_factory() as db:
        rows = dict((await db.execute(text("SELECT key, failures FROM login_failures"))).all())
    assert rows == {"account:alice": 3, "client:testclient": 3}


async def test_legacy_pgcrypto_values_are_re_encrypted(session_factory, monkeypatch):
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "legacy passphrase")
    monkeypatch.delenv("C2AI_ENCRYPTION_KEYS", raising=False)
    derived = crypto.encrypt("ghp_derived", column=crypto.GITHUB_TOKEN)
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k1:{crypto.generate_key()}")
    async with session_factory() as db:
        await db.execute(text("DELETE FROM github_connections"))
        await db.execute(
            text(
                "INSERT INTO github_connections (display_name, connection_url,"
                " access_token_encrypted, created_by, updated_by) VALUES"
                " ('old', 'https://github.com/a', pgp_sym_encrypt('ghp_old', 'legacy passphrase',"
                " 'cipher-algo=aes256'), 'x', 'x'),"
                " ('derived', 'https://github.com/b', :derived, 'x', 'x')"
            ),
            {"derived": derived},
        )
        await db.commit()

    assert await key_usage(session_factory) == {"pgcrypto": 1, "derived": 1}
    assert (await reencrypt_all(session_factory))["rewritten"] == 2
    assert await key_usage(session_factory) == {"k1": 2}
    assert (await reencrypt_all(session_factory))["rewritten"] == 0
    async with session_factory() as db:
        blobs = (
            await db.execute(
                text("SELECT access_token_encrypted FROM github_connections ORDER BY display_name")
            )
        ).scalars().all()
    tokens = [crypto.decrypt(bytes(blob), column=crypto.GITHUB_TOKEN) for blob in blobs]
    assert tokens == ["ghp_derived", "ghp_old"]


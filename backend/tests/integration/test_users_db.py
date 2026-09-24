"""Users against PostgreSQL: the 0021 backfill, visibility, and the last-admin lock."""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extras import Json
from sqlalchemy.engine import make_url

from c2ai.app import app
from c2ai.config import get_settings
from c2ai.crud import user as crud_user
from c2ai.db.migrate import run_migrations
from c2ai.db.session import get_db_session
from c2ai.db.url import psycopg2_connect_kwargs
from tests.integration.conftest import _SERVER_URL, _admin_connection

pytestmark = pytest.mark.skipif(not _SERVER_URL, reason="needs C2AI_TEST_DATABASE_URL")

PASSWORD = "Str0ng!pass"


def test_0021_moves_user_type_into_columns():
    name = f"c2ai_test_{uuid.uuid4().hex[:10]}"
    admin = _admin_connection()
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    url = make_url(_SERVER_URL).set(database=name, drivername="postgresql+asyncpg")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        run_migrations(until="0020")
        conn = psycopg2.connect(**psycopg2_connect_kwargs(url.set(drivername="postgresql")))
        with conn, conn.cursor() as cursor:
            for identifier, meta, created_by in (
                ("admin", {"user_type": "Admin", "role": "Executive"}, "system"),
                ("ops", {"user_type": "admin"}, "admin"),
                ("legacy", {"role": "Admin"}, "admin"),
                ("viewer", {"user_type": "User", "needs_password_reset": True}, "ops"),
            ):
                cursor.execute(
                    "INSERT INTO users (identifier, password, first_name, last_name,"
                    " metadata, created_by) VALUES (%s, 'x', 'F', 'L', %s, %s)",
                    (identifier, Json(meta), created_by),
                )
        run_migrations()
        with conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT u.identifier, u.user_type, u.is_superuser, c.identifier, u.metadata"
                " FROM users u LEFT JOIN users c ON c.id = u.created_by_id"
                " ORDER BY u.identifier"
            )
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
        conn.close()
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()

    assert rows["admin"] == ("admin", True, None, {"role": "Executive"})
    assert rows["ops"][:3] == ("admin", False, "admin")
    assert rows["legacy"][:2] == ("admin", False)  # legacy role=Admin still counts
    assert rows["viewer"] == ("user", False, "ops", {"needs_password_reset": True})


@pytest.fixture
def client(session_factory):
    async def _session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def _make(db, identifier, user_type, *, superuser=False, created_by=None):
    user = await crud_user.create_user(
        db,
        {
            "identifier": identifier,
            "password": PASSWORD,
            "first_name": identifier.title(),
            "last_name": "Test",
            "metadata_": {"user_type": user_type, "needs_password_reset": False},
            "created_by": created_by.identifier if created_by else "system",
            "created_by_id": created_by.id if created_by else None,
        },
    )
    if superuser:
        user.is_superuser = True
        await db.commit()
    return user


def _login(client, identifier):
    # Header auth only: a cookie in the shared TestClient jar would win over it.
    response = client.post(
        "/auth/login",
        json={"identifier": identifier, "password": PASSWORD, "set_cookie": False},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_visibility_roles_and_last_admin(client, db):
    root = await _make(db, "root", "Admin", superuser=True)
    team_lead = await _make(db, "lead", "Admin", created_by=root)
    await _make(db, "member", "User", created_by=team_lead)
    await _make(db, "stranger", "User", created_by=root)

    lead = _login(client, "lead")
    listed = client.get("/users/", headers=lead).json()
    assert sorted(user["identifier"] for user in listed) == ["lead", "member"]
    assert {user["identifier"]: user["metadata_"]["user_type"] for user in listed} == {
        "lead": "Admin",
        "member": "User",
    }
    assert client.get("/users/", params={"user_name": "stranger"}, headers=lead).status_code == 404

    created = client.post(
        "/users/",
        json={
            "identifier": "newbie",
            "first_name": "New",
            "last_name": "Bie",
            "metadata_": {"user_type": "User", "role": "Engineer"},
        },
        headers=lead,
    )
    assert created.status_code == 201, created.text
    assert created.json()["metadata_"] == {
        "role": "Engineer",
        "needs_password_reset": True,
        "user_type": "User",
    }
    stored = await crud_user.get_user_by_identifier(db, "newbie")
    assert stored.created_by_id == team_lead.id
    assert "user_type" not in stored.metadata_

    root_headers = _login(client, "root")
    assert len(client.get("/users/", headers=root_headers).json()) == 5

    # Promote, then the lead's own admin rights come from the column at once.
    promoted = client.patch(
        "/users/",
        params={"user_name": "member"},
        json={"metadata_": {"user_type": "Admin"}},
        headers=root_headers,
    )
    assert promoted.json()["user"]["metadata_"]["user_type"] == "Admin"
    demoted = client.patch(
        "/users/",
        params={"user_name": "lead"},
        json={"metadata_": {"user_type": "User"}},
        headers=root_headers,
    )
    assert demoted.status_code == 200
    assert client.get("/users/", headers=lead).status_code == 403

    # Delete admins down to one; the last one cannot go.
    for identifier in ("member",):
        assert (
            client.delete("/users/", params={"user_name": identifier}, headers=root_headers)
            .status_code
            == 204
        )
    last = client.patch(
        "/users/",
        params={"user_name": "root"},
        json={"metadata_": {"user_type": "User"}},
        headers=root_headers,
    )
    assert last.status_code in (400, 409, 422)
    assert last.json()["code"] == "CannotUpdateLastAdminToUser"

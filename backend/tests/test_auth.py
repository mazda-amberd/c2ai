"""Authentication: token lifecycle, DB-backed authorization, and session endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import jwt as pyjwt
import pytest
from argon2 import PasswordHasher

from c2ai.auth.jwt import (
    clamp_token_ttl,
    create_jwt,
    decode_jwt,
    default_token_ttl_seconds,
    format_athena_datetime,
    get_jwt_secret,
)
from c2ai.core.exceptions import AppException
from c2ai.models.user import User

_LOOKUP = "c2ai.auth.jwt.get_user_by_identifier"
_LOGIN_LOOKUP = "c2ai.api.auth_session.get_user_by_identifier"


class FakeSessionStore:
    """In-memory revocation list and failure counters."""

    def __init__(self):
        self.revoked: set[str] = set()
        self.failures: dict[str, int] = {}

    async def is_token_revoked(self, _db, jti):
        return jti in self.revoked

    async def revoke_token(self, _db, jti, _expires_at):
        self.revoked.add(jti)

    async def failures_in_window(self, _db, keys, _window):
        return {key: self.failures[key] for key in keys if key in self.failures}

    async def record_failure(self, _db, keys, _window):
        for key in keys:
            self.failures[key] = self.failures.get(key, 0) + 1

    async def clear_failures(self, _db, keys):
        for key in keys:
            self.failures.pop(key, None)


@pytest.fixture(autouse=True)
def sessions(monkeypatch):
    from c2ai.api import auth_session
    from c2ai.auth import jwt as jwt_module
    from c2ai.crud import session as session_crud

    store = FakeSessionStore()
    for name in ("is_token_revoked", "revoke_token", "failures_in_window",
                 "record_failure", "clear_failures"):
        monkeypatch.setattr(session_crud, name, getattr(store, name))
    monkeypatch.setattr(jwt_module, "is_token_revoked", store.is_token_revoked)
    monkeypatch.setattr(auth_session.session_store, "is_token_revoked", store.is_token_revoked)
    return store


def _user(identifier: str = "alice", user_type: str = "Admin", password: str = "S3cret!pw") -> User:
    return User(
        id=uuid4(),
        identifier=identifier,
        password=PasswordHasher().hash(password),
        first_name="Alice",
        last_name="Admin",
        user_type=user_type.lower(),
        is_superuser=False,
        token_version=0,
        metadata_={"role": "Executive", "needs_password_reset": False},
        created_by="system",
    )


def _token(
    identifier: str = "alice", metadata: dict | None = None, ttl: int = 3600, tv: int = 0
) -> str:
    return create_jwt(
        payload={
            "identifier": identifier,
            "service": "athena",
            "metadata": metadata or {},
            "tv": tv,
        },
        expires_in=timedelta(seconds=ttl),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTokens:
    def test_round_trip_carries_standard_and_legacy_expiry(self):
        token = _token()
        claims = pyjwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
        assert {"iat", "exp", "created", "expired"} <= claims.keys()
        assert decode_jwt(token).identifier == "alice"

    def test_expired_token_is_rejected(self):
        token = pyjwt.encode(
            {"identifier": "alice", "exp": int(datetime.now(UTC).timestamp()) - 5},
            get_jwt_secret(),
            algorithm="HS256",
        )
        with pytest.raises(AppException) as error:
            decode_jwt(token)
        assert error.value.status_code == 401
        assert error.value.code == "TokenExpired"

    def test_legacy_token_without_exp_uses_expired_string(self):
        past = format_athena_datetime(datetime.now(UTC) - timedelta(minutes=1))
        token = pyjwt.encode({"identifier": "alice", "expired": past}, get_jwt_secret())
        with pytest.raises(AppException) as error:
            decode_jwt(token)
        assert error.value.code == "TokenExpired"

    def test_token_without_any_expiry_is_invalid(self):
        token = pyjwt.encode({"identifier": "alice"}, get_jwt_secret())
        with pytest.raises(AppException) as error:
            decode_jwt(token)
        assert error.value.code == "InvalidToken"

    def test_ttl_is_clamped_to_configured_maximum(self, monkeypatch):
        monkeypatch.setenv("ATHENA_TOKEN_MAX_TTL_SECONDS", "7200")
        assert clamp_token_ttl(10**9) == 7200
        assert clamp_token_ttl(1) == 60

    def test_default_ttl_respects_environment(self, monkeypatch):
        monkeypatch.setenv("ATHENA_TOKEN_TTL_SECONDS", "900")
        assert default_token_ttl_seconds() == 900


class TestAuthorization:
    def test_admin_endpoint_rejects_non_admin_with_403_not_401(self, test_client):
        # 401 would make the UI sign the user out; lacking rights is a 403.
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=_user(user_type="User")):
            response = test_client.get(
                "/api/registered-applications", headers=_auth(_token())
            )
        assert response.status_code == 403
        assert response.json()["code"] == "AdminPrivilegesRequired"

    def test_admin_rights_come_from_database_not_token(self, test_client):
        # The token still claims Admin, but the stored account was demoted.
        token = _token(metadata={"user_type": "Admin"})
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=_user(user_type="User")):
            response = test_client.get("/api/registered-applications", headers=_auth(token))
        assert response.status_code == 403

    def test_deleted_user_token_is_rejected(self, test_client):
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=None):
            response = test_client.get("/auth/whoami", headers=_auth(_token()))
        assert response.status_code == 401

    def test_whoami_returns_current_database_metadata(self, test_client):
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=_user(user_type="User")):
            response = test_client.get(
                "/auth/whoami", headers=_auth(_token(metadata={"user_type": "Admin"}))
            )
        assert response.status_code == 200
        body = response.json()
        assert body["identifier"] == "alice"
        assert body["metadata"]["user_type"] == "User"

    def test_legacy_metrics_endpoint_requires_authentication(self, test_client):
        assert test_client.get("/api/metrics").status_code == 401


class TestLogin:
    def test_login_sets_http_only_cookie_with_token_lifetime(self, test_client, monkeypatch):
        monkeypatch.setenv("ATHENA_TOKEN_TTL_SECONDS", "3600")
        with patch(_LOGIN_LOOKUP, new_callable=AsyncMock, return_value=_user()):
            response = test_client.post(
                "/auth/login", json={"identifier": "alice", "password": "S3cret!pw"}
            )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Max-Age=3600" in cookie
        claims = decode_jwt(response.json()["access_token"])
        assert claims.metadata["user_type"] == "Admin"

    def test_requested_lifetime_cannot_exceed_maximum(self, test_client, monkeypatch):
        monkeypatch.setenv("ATHENA_TOKEN_MAX_TTL_SECONDS", "600")
        with patch(_LOGIN_LOOKUP, new_callable=AsyncMock, return_value=_user()):
            response = test_client.post(
                "/auth/login",
                json={
                    "identifier": "alice",
                    "password": "S3cret!pw",
                    "expires_in_seconds": 100 * 365 * 24 * 3600,
                },
            )
        claims = pyjwt.decode(
            response.json()["access_token"], get_jwt_secret(), algorithms=["HS256"]
        )
        assert claims["exp"] - claims["iat"] == 600

    @pytest.mark.parametrize("found", [True, False])
    def test_bad_credentials_share_one_error(self, test_client, found):
        user = _user() if found else None
        with patch(_LOGIN_LOOKUP, new_callable=AsyncMock, return_value=user):
            response = test_client.post(
                "/auth/login", json={"identifier": "alice", "password": "wrong"}
            )
        assert response.status_code == 401
        assert response.json()["code"] == "InvalidCredentials"

    def test_logout_clears_cookie(self, test_client):
        response = test_client.post("/auth/logout")
        assert response.status_code == 200
        assert "access_token=" in response.headers["set-cookie"]

    def test_logout_revokes_the_token(self, test_client, sessions):
        token = _token()
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=_user()):
            assert test_client.get("/auth/whoami", headers=_auth(token)).status_code == 200
            test_client.post("/auth/logout", headers=_auth(token))
            response = test_client.get("/auth/whoami", headers=_auth(token))
        assert response.status_code == 401
        assert len(sessions.revoked) == 1

    def test_tokens_from_before_a_password_change_are_rejected(self, test_client):
        user = _user()
        old = _token(tv=0)
        user.token_version = 1  # password changed since the token was issued
        with patch(_LOOKUP, new_callable=AsyncMock, return_value=user):
            assert test_client.get("/auth/whoami", headers=_auth(old)).status_code == 401
            assert test_client.get("/auth/whoami", headers=_auth(_token(tv=1))).status_code == 200

    def test_repeated_failures_pause_sign_in(self, test_client, monkeypatch, sessions):
        monkeypatch.setenv("C2AI_LOGIN_MAX_FAILURES_PER_ACCOUNT", "3")
        with patch(_LOGIN_LOOKUP, new_callable=AsyncMock, return_value=_user()):
            for _ in range(3):
                bad = test_client.post(
                    "/auth/login", json={"identifier": "alice", "password": "wrong"}
                )
                assert bad.status_code == 401
            blocked = test_client.post(
                "/auth/login", json={"identifier": "alice", "password": "S3cret!pw"}
            )
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "TooManyLoginAttempts"
        assert int(blocked.headers["Retry-After"]) > 0

    def test_success_resets_the_account_counter(self, test_client, sessions):
        with patch(_LOGIN_LOOKUP, new_callable=AsyncMock, return_value=_user()):
            test_client.post("/auth/login", json={"identifier": "alice", "password": "wrong"})
            ok = test_client.post(
                "/auth/login", json={"identifier": "alice", "password": "S3cret!pw"}
            )
        assert ok.status_code == 200
        assert "account:alice" not in sessions.failures
        assert sessions.failures["client:testclient"] == 1

"""Authentication: token lifecycle, DB-backed authorization, and session endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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


def _user(identifier: str = "alice", user_type: str = "Admin", password: str = "S3cret!pw") -> User:
    return User(
        identifier=identifier,
        password=PasswordHasher().hash(password),
        first_name="Alice",
        last_name="Admin",
        metadata_={"user_type": user_type, "role": "Executive", "needs_password_reset": False},
        created_by="system",
    )


def _token(identifier: str = "alice", metadata: dict | None = None, ttl: int = 3600) -> str:
    return create_jwt(
        payload={"identifier": identifier, "service": "athena", "metadata": metadata or {}},
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
            {"identifier": "alice", "exp": int(datetime.now(timezone.utc).timestamp()) - 5},
            get_jwt_secret(),
            algorithm="HS256",
        )
        with pytest.raises(AppException) as error:
            decode_jwt(token)
        assert error.value.status_code == 401
        assert error.value.code == "TokenExpired"

    def test_legacy_token_without_exp_uses_expired_string(self):
        past = format_athena_datetime(datetime.now(timezone.utc) - timedelta(minutes=1))
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

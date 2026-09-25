"""Who may call what: Admins change things, Users look.

Admins create and delete application templates, deploy, update, move,
terminate and cancel, and manage GitHub connections and people. A User
sees what runs on the tiers, reads metrics and logs, and troubleshoots.

The table is every route the API has. A new route fails this test until it
is placed in one of the three sets - so the question is always asked.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.routing import APIRoute

from c2ai.app import app
from c2ai.auth import jwt
from c2ai.auth.jwt import create_jwt
from c2ai.models.user import User

ADMIN = {
    # Deploying and everything after it.
    ("POST", "/api/deploy"),
    ("POST", "/api/deploy/update"),
    ("POST", "/api/deploy/move-tier"),
    ("POST", "/api/deploy/terminate"),
    ("POST", "/api/pipeline/cancel"),
    ("GET", "/api/github/branches"),
    ("GET", "/api/github/tags"),
    ("POST", "/api/registered-applications/{application_id}/deployments"),
    ("POST", "/api/registered-applications/{application_id}/tiers/{tier}/deployments"),
    ("POST", "/api/registered-applications/deployments/{deployment_id}/upgrade"),
    ("POST", "/api/registered-applications/deployments/{deployment_id}/terminate"),
    ("POST", "/api/registered-applications/deployments/{deployment_id}/rollback"),
    ("GET", "/api/registered-applications/deployments"),
    ("GET", "/api/registered-applications/deployments/{deployment_id}"),
    ("GET", "/api/registered-applications/{application_id}/github-tags"),
    ("GET", "/api/registered-applications/{application_id}/image-tags"),
    # Application templates.
    ("GET", "/api/registered-applications"),
    ("POST", "/api/registered-applications/github"),
    ("POST", "/api/registered-applications/container"),
    ("GET", "/api/registered-applications/{application_id}"),
    ("DELETE", "/api/registered-applications/{application_id}"),
    ("GET", "/api/registered-applications/{application_id}/secrets"),
    ("POST", "/api/registered-applications/{application_id}/secrets"),
    ("PATCH", "/api/registered-applications/{application_id}/secrets/{secret_id}"),
    ("DELETE", "/api/registered-applications/{application_id}/secrets/{secret_id}"),
    ("GET", "/api/registered-applications/llm-models"),
    ("GET", "/api/registered-applications/llm-models/pricing"),
    ("GET", "/api/github-connections"),
    ("POST", "/api/github-connections"),
    ("POST", "/api/github-connections/validate"),
    # People.
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users/{user_id}"),
    ("DELETE", "/api/users/{user_id}"),
    ("POST", "/api/users/{user_id}/reset-password"),
    ("GET", "/users/"),
    ("POST", "/users/"),
    ("PATCH", "/users/"),
    ("DELETE", "/users/"),
    ("GET", "/users/reset_password/{user_name}"),
    ("POST", "/users/reset_password/{user_name}"),
}

SIGNED_IN = {
    # What is on the tiers, and what is happening to it.
    ("GET", "/api/pipeline/active"),
    ("GET", "/api/pipeline/status"),
    ("GET", "/api/pipeline/history"),
    ("GET", "/api/financial/costs"),
    # Metrics, logs and troubleshooting.
    ("GET", "/api/metrics"),
    ("GET", "/api/v2/metrics"),
    ("GET", "/api/v2/metrics/application"),
    ("GET", "/api/logs/deployment"),
    ("POST", "/jobs"),
    ("GET", "/jobs/{job_id}"),
    ("GET", "/jobs/{job_id}/report.pdf"),
    ("POST", "/report"),
    # Your own account.
    ("GET", "/auth/whoami"),
    ("PATCH", "/users/update_password"),
}

OPEN = {
    ("GET", "/api"),
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/metrics"),  # its own bearer token (C2AI_METRICS_TOKEN)
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/forgot-password"),
    # Called by the pipeline with a per-operation callback token.
    ("POST", "/api/registered-applications/deployments/{deployment_id}/progress"),
}


def _needs(dependant) -> set[str]:
    found = set()
    for dependency in dependant.dependencies:
        if dependency.call is jwt.require_admin:
            found.add("admin")
        elif dependency.call in (jwt.get_current_user_token, jwt.get_user_on_any_password):
            found.add("signed in")
        found |= _needs(dependency)
    return found


def _routes() -> dict[tuple[str, str], str]:
    table = {}
    for route in app.routes:
        # The built UI's fallback (present only when frontend/dist exists).
        if not isinstance(route, APIRoute) or route.path == "/{full_path:path}":
            continue
        needs = _needs(route.dependant)
        level = "admin" if "admin" in needs else "signed in" if needs else "open"
        for method in route.methods - {"HEAD"}:
            table[(method, route.path)] = level
    return table


def test_every_route_is_placed_and_admins_alone_change_things():
    expected = {
        **{route: "admin" for route in ADMIN},
        **{route: "signed in" for route in SIGNED_IN},
        **{route: "open" for route in OPEN},
    }
    actual = _routes()
    unplaced = sorted(set(actual) - set(expected))
    assert not unplaced, f"New routes to place in ADMIN, SIGNED_IN or OPEN: {unplaced}"
    wrong = {route: (actual[route], level) for route, level in expected.items() if actual.get(route) != level}
    assert not wrong, f"(actual, expected): {wrong}"


def _account(user_type: str) -> User:
    return User(
        id=uuid4(),
        identifier="viewer@example.com",
        password=PasswordHasher().hash("unused"),
        first_name="Vera",
        last_name="Viewer",
        user_type=user_type,
        is_superuser=False,
        token_version=0,
        metadata_={"needs_password_reset": False},
        created_by="system",
    )


@pytest.fixture
def signed_in_user():
    """A bearer token for an account whose stored role is User."""

    token = create_jwt(
        payload={"identifier": "viewer@example.com", "service": "athena", "tv": 0},
        expires_in=timedelta(minutes=5),
    )
    with (
        patch.object(jwt, "get_user_by_identifier", AsyncMock(return_value=_account("user"))),
        patch.object(jwt, "is_token_revoked", AsyncMock(return_value=False)),
    ):
        yield {"Authorization": f"Bearer {token}"}


DEPLOY_BODY = {
    "branch": "main",
    "subdomain": "amberd-acme-ada",
    "customer_name": "acme",
    "domain": "amberd.ai",
    "env_instance": "ada",
    "tier": 1,
}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/deploy", DEPLOY_BODY),
        ("post", "/api/deploy/update", DEPLOY_BODY),
        ("post", "/api/deploy/move-tier", {"subdomain": "amberd-acme-ada", "tier": 2}),
        ("post", "/api/deploy/terminate", {"subdomain": "amberd-acme-ada"}),
        ("post", "/api/pipeline/cancel", {"pipeline_run_id": "run-1"}),
        ("get", "/api/github/branches?repo=devops", None),
        ("delete", f"/api/registered-applications/{uuid4()}", None),
        ("post", f"/api/registered-applications/{uuid4()}/tiers/1/deployments", {}),
    ],
)
def test_a_user_is_refused_what_changes_things(test_client, signed_in_user, method, path, body):
    kwargs = {"headers": signed_in_user} if body is None else {"headers": signed_in_user, "json": body}
    response = getattr(test_client, method)(path, **kwargs)
    assert response.status_code == 403  # not 401: they stay signed in
    assert response.json()["code"] == "AdminPrivilegesRequired"

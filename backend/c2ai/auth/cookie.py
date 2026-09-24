"""Auth cookie helpers.

Environment variables:
- ``ATHENA_AUTH_COOKIE_NAME``   cookie name (default ``access_token``)
- ``ATHENA_COOKIE_SAMESITE``    ``lax`` | ``strict`` | ``none`` (default ``lax``)
- ``ATHENA_COOKIE_SECURE``      ``true``/``false``; defaults to true when
                                SameSite is ``none`` (browsers require it)
- ``ATHENA_AUTH_COOKIE_DOMAIN`` optional cookie domain
"""

from __future__ import annotations

from fastapi import Response
from starlette.requests import Request

from c2ai.config import get_settings


def auth_cookie_name() -> str:
    return get_settings().auth_cookie_name


def _domain() -> str | None:
    return get_settings().auth_cookie_domain.strip() or None


def get_token_from_cookie_or_header(
    request: Request,
    *,
    cookie_name: str | None = None,
    header_name: str = "Authorization",
    scheme: str = "Bearer",
) -> str | None:
    """Return the token from the auth cookie, else from ``Authorization: Bearer``."""

    token = request.cookies.get(cookie_name or auth_cookie_name())
    if token:
        return token
    header = request.headers.get(header_name)
    prefix = f"{scheme} "
    if header and header.startswith(prefix):
        return header[len(prefix):].strip() or None
    return None


def set_auth_cookie(response: Response, token: str, *, max_age: int) -> None:
    """Set the HttpOnly auth cookie for ``max_age`` seconds."""

    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_is_secure,
        samesite=settings.cookie_samesite,
        path="/",
        domain=_domain(),
    )


def clear_auth_cookie(response: Response) -> None:
    """Instruct the browser to delete the auth cookie."""

    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        domain=_domain(),
        secure=settings.cookie_is_secure,
        samesite=settings.cookie_samesite,
    )

"""Auth cookie helpers.

Environment variables:
- ``ATHENA_AUTH_COOKIE_NAME``   cookie name (default ``access_token``)
- ``ATHENA_COOKIE_SAMESITE``    ``lax`` | ``strict`` | ``none`` (default ``lax``)
- ``ATHENA_COOKIE_SECURE``      ``true``/``false``; defaults to true when
                                SameSite is ``none`` (browsers require it)
- ``ATHENA_AUTH_COOKIE_DOMAIN`` optional cookie domain
"""

from __future__ import annotations

import os

from fastapi import Response
from starlette.requests import Request

AUTH_COOKIE_NAME = os.getenv("ATHENA_AUTH_COOKIE_NAME", "access_token")
_SAMESITE_VALUES = {"lax", "strict", "none"}


def _samesite() -> str:
    value = os.getenv("ATHENA_COOKIE_SAMESITE", "lax").strip().lower()
    return value if value in _SAMESITE_VALUES else "lax"


def _secure(samesite: str) -> bool:
    raw = os.getenv("ATHENA_COOKIE_SECURE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return samesite == "none"
    return samesite == "none"


def _domain() -> str | None:
    return os.getenv("ATHENA_AUTH_COOKIE_DOMAIN", "").strip() or None


def get_token_from_cookie_or_header(
    request: Request,
    *,
    cookie_name: str = AUTH_COOKIE_NAME,
    header_name: str = "Authorization",
    scheme: str = "Bearer",
) -> str | None:
    """Return the token from the auth cookie, else from ``Authorization: Bearer``."""

    token = request.cookies.get(cookie_name)
    if token:
        return token
    header = request.headers.get(header_name)
    prefix = f"{scheme} "
    if header and header.startswith(prefix):
        return header[len(prefix):].strip() or None
    return None


def set_auth_cookie(response: Response, token: str, *, max_age: int) -> None:
    """Set the HttpOnly auth cookie for ``max_age`` seconds."""

    samesite = _samesite()
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_secure(samesite),
        samesite=samesite,
        path="/",
        domain=_domain(),
    )


def clear_auth_cookie(response: Response) -> None:
    """Instruct the browser to delete the auth cookie."""

    samesite = _samesite()
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        domain=_domain(),
        secure=_secure(samesite),
        samesite=samesite,
    )

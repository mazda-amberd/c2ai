# pylint: disable=import-error
"""Cookie-based auth helpers for Athena (FastAPI).

Provides:
- `OAuth2PasswordBearerWithCookie`: dependency that first checks a cookie
  (default: "access_token"), and falls back to Authorization: Bearer.
- Helpers to set/clear auth cookie.

Environment variables:
- ATHENA_AUTH_COOKIE_NAME (default: access_token)
- ATHENA_COOKIE_SAMESITE in {lax, strict, none} (default: lax)
- ATHENA_COOKIE_SECURE (default: auto; true if samesite=none)
- ATHENA_AUTH_COOKIE_MAX_AGE_SECONDS (default: 3600)
- ATHENA_AUTH_COOKIE_DOMAIN (optional)
"""

from __future__ import annotations

from typing import Optional

from fastapi import Response
from starlette.requests import Request


def get_token_from_cookie_or_header(
    request: Request,
    *,
    cookie_name: str = "access_token",
    header_name: str = "Authorization",
    scheme: str = "Bearer",
    auto_error: bool = True,
) -> Optional[str]:
    """
    Fetch token from cookie (preferred) or Authorization header.

    Returns token string or None if not found (unless auto_error=True and
    existing code raises elsewhere).

    Args:
        request (Request): FastAPI request object.
        cookie_name (str): Name of the cookie to check first.
        header_name (str): Name of the header to check if cookie not found.
        scheme (str): Expected scheme in the Authorization header.
        auto_error (bool): If True, raise HTTPException if no token found.

    Returns:
        Optional[str]: The extracted token or None.
    """
    token = request.cookies.get(cookie_name)
    if token:
        return token

    auth = request.headers.get(header_name)
    if not auth:
        return None

    prefix = f"{scheme} "
    if auth.startswith(prefix):
        return auth[len(prefix):].strip()

    return None


def set_auth_cookie(
    response: Response,
    token: str,
    *,
    cookie_name: str = "access_token",
    max_age: int = 30 * 24 * 60 * 60,
    httponly: bool = True,
    secure: bool = False,
    samesite: str = "lax",
    path: str = "/",
    domain: Optional[str] = None,
) -> None:
    """Set the auth cookie."""
    response.set_cookie(
        key=cookie_name,
        value=token,
        max_age=max_age,
        httponly=httponly,
        secure=secure,
        samesite=samesite,
        path=path,
        domain=domain,
    )


def clear_auth_cookie(
    response: Response,
    *,
    cookie_name: str = "access_token",
    path: str = "/",
    domain: Optional[str] = None,
    secure: bool = False,
    samesite: str = "lax",
) -> None:
    """
    Clear the auth cookie by instructing the client to delete it.
    """

    response.delete_cookie(
        key=cookie_name,
        path=path,
        domain=domain,
        secure=secure,
        samesite=samesite,
    )

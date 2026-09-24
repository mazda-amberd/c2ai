# pylint: disable=import-error
"""Auth debugging endpoints.

These endpoints help verify how the backend is receiving your token.
Remove/disable in production if you don't want them exposed.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from c2ai.auth.cookie import get_token_from_cookie_or_header
from c2ai.auth.jwt import decode_jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth Debug"])


@router.get("/whoami")
async def whoami(request: Request):
    """
    Returns the decoded JWT token information for the current user.

    Args:
        request (Request): FastAPI request object.

    Returns:
        dict: Decoded user information from the JWT token.
    """
    token = get_token_from_cookie_or_header(request,
                                            cookie_name="access_token",
                                            auto_error=True)
    user = decode_jwt(token)
    return {"identifier": user.identifier,
            "service": user.service,
            "metadata": user.metadata}


# pylint: disable=import-error
"""
FastAPI auth dependency helpers.

Provides reusable callables for resolving the current user from a JWT token.
"""

from __future__ import annotations

import logging

from jwt import DecodeError, ExpiredSignatureError
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.auth.jwt import decode_jwt
from c2ai.crud.user import get_user_by_identifier
from c2ai.core.exceptions import InvalidToken, TokenExpired, UserNotFound

logger = logging.getLogger(__name__)


async def get_current_user_identifier(token: str, db: AsyncSession) -> str:
    """
    Decode the JWT token and return the current user's identifier.

    Handles the special case for the super admin user.

    Args:
        token: JWT token string.
        db: Async database session.

    Returns:
        "admin" if the user is the super admin, otherwise the user's identifier.

    Raises:
        UserNotFound: If the token is missing, invalid, or the user does not exist.
        InvalidToken: If the token cannot be decoded.
        TokenExpired: If the token has expired.
    """
    if not token or not isinstance(token, str):
        raise UserNotFound("Missing or invalid token")

    try:
        curr_user_identifier = decode_jwt(token).identifier
    except DecodeError:
        raise InvalidToken()
    except ExpiredSignatureError:
        raise TokenExpired()

    curr_user = await get_user_by_identifier(db, curr_user_identifier)

    if (
        curr_user_identifier == "admin"
        and curr_user.first_name == "Super"
        and curr_user.last_name == "Admin"
    ):
        return "admin"

    return curr_user_identifier


async def get_current_user(token: str, db: AsyncSession):
    """
    Fetch the current user ORM object by token.

    Args:
        token: JWT token string.
        db: Async database session.

    Returns:
        The User ORM instance.

    Raises:
        UserNotFound: If the user does not exist.
    """
    curr_user_ident = await get_current_user_identifier(token=token, db=db)
    user = await get_user_by_identifier(db=db, identifier=curr_user_ident)
    if not user:
        raise UserNotFound(curr_user_ident)
    return user

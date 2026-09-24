# pylint: disable=import-error
"""
Exception handlers for FastAPI application.

Defines handlers for application-specific, HTTP, and validation errors.
All validation errors are handled by a single unified function.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from c2ai.core.exceptions import AppException

logger = logging.getLogger("uvicorn.error")


def _json_error(detail: Any, code: str):
    """
    Helper function to format error responses as JSON.

    Args:
        detail: Detailed error message or list of errors.
        code: Error code string.

    Returns:
        dict: JSON-serializable error response.
    """
    return {"detail": detail, "code": code}


async def app_exception_handler(request: Request, exc: AppException):
    """
    Handles custom AppException errors.

    Args:
        request: The incoming HTTP request.
        exc: The AppException instance.

    Returns:
        JSONResponse: Formatted error response.
    """
    logger.warning("AppException: %s %s", exc.code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content=_json_error(exc.detail, exc.code))


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Handles FastAPI HTTPException errors.

    Args:
        request: The incoming HTTP request.
        exc: The HTTPException instance.

    Returns:
        JSONResponse: Formatted error response.
    """
    code = getattr(exc, "code", f"HTTP_{exc.status_code}")
    logger.warning("HTTPException: %s %s", exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_json_error(exc.detail, code),
        headers=getattr(exc, "headers", None),
    )


def _normalize_validation_errors(exc: Any):
    """
    Normalize validation errors to a consistent structure.

    Args:
        exc: The exception instance containing validation errors.

    Returns:
        list: List of normalized error dictionaries.
    """
    if hasattr(exc, "errors") and callable(exc.errors):
        return [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    # Fallback for unexpected exception types routed here
    return [{"loc": None, "msg": str(exc), "type": exc.__class__.__name__}]


async def validation_error_handler(request: Request, exc: Exception):
    """
    Handles both FastAPI and Pydantic validation errors.

    Args:
        request: The incoming HTTP request.
        exc: The validation error exception.

    Returns:
        JSONResponse: Formatted error response with validation details.
    """
    name = exc.__class__.__name__
    errors = _normalize_validation_errors(exc)
    logger.warning("%s: %s", name, errors)
    return JSONResponse(status_code=422, content=_json_error(errors, name))


async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Handles all unhandled exceptions.

    Args:
        request: The incoming HTTP request.
        exc: The Exception instance.

    Returns:
        JSONResponse: Generic internal server error response.
    """
    logger.exception("Unhandled exception")
    return JSONResponse(status_code=500, content=_json_error("Internal server error", "InternalServerError"))


def attach_exception_handlers(app: FastAPI):
    """
    Attaches all custom exception handlers to the FastAPI application.

    Args:
        app: The FastAPI application instance.
    """
    app.add_exception_handler(AppException, app_exception_handler)
    # Starlette's base class also covers routing 404/405s, not just FastAPI raises.
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(PydanticValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

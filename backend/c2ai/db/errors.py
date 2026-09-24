"""Helpers for reading database constraint violations."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """Extract a PostgreSQL constraint name without depending on psycopg internals."""

    original = getattr(error, "orig", None)
    candidates = (
        original,
        getattr(original, "orig", None),
        getattr(original, "__cause__", None),
    )
    for candidate in candidates:
        constraint_name = getattr(candidate, "constraint_name", None)
        if constraint_name:
            return constraint_name
        diagnostic = getattr(candidate, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name:
            return constraint_name
    return None

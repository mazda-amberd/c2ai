# pylint: disable=import-error
"""
Pure validation helpers for identifiers and passwords.
"""

from __future__ import annotations

import re

from c2ai.config import get_settings


def validate_identifier(identifier: str) -> tuple[bool, str | None, str | None]:
    """
    Validate that the identifier is non-empty, trimmed, within the configured
    length range, and contains only permitted characters.

    Env vars (with fallback defaults):
        IDENTIFIER_MIN_LENGTH (3)
        IDENTIFIER_MAX_LENGTH (30)
        IDENTIFIER_ALLOWED_SYMBOLS ("@_+.")

    Args:
        identifier: The identifier string to validate.

    Returns:
        A 3-tuple of (is_valid, error_message, normalised_identifier).
        *error_message* and *normalised_identifier* are mutually exclusive:
        one will always be None.
    """
    settings = get_settings()
    min_length = settings.identifier_min_length
    max_length = settings.identifier_max_length
    symbols = settings.identifier_allowed_symbols

    if identifier is None or not isinstance(identifier, str):
        return False, "Identifier must be a string.", None

    identifier = identifier.strip()
    if not identifier:
        return False, "Identifier cannot be empty.", None

    length = len(identifier)
    if not (min_length <= length <= max_length):
        return (
            False,
            f"Identifier must be between {min_length} and {max_length} characters.",
            None,
        )

    allowed_pattern = re.compile(f"^[A-Za-z0-9{re.escape(symbols)}]+$")
    if not allowed_pattern.fullmatch(identifier):
        return (
            False,
            f"Identifier may only contain letters, digits, and only these symbols: '{symbols}'.",
            None,
        )

    return True, None, identifier


def validate_password(password: str) -> tuple[bool, str | None]:
    """
    Validate that the password meets complexity requirements.

    Rules:
    - At least 8 characters.
    - At least one uppercase letter, one lowercase letter, one digit, and
      one special character (from PASSWORD_SPECIALS env var).
    - No runs of 3 or more consecutive ascending or descending digits.

    Args:
        password: The password string to validate.

    Returns:
        A 2-tuple of (is_valid, error_message).
        *error_message* is None when the password is valid.
    """
    if password is None or not password.strip():
        return False, "Password cannot be empty or null."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must include at least one number."

    specials = get_settings().password_specials
    if not re.search(f"[{re.escape(specials)}]", password):
        return False, "Password must include at least one special character."

    for i in range(len(password) - 2):
        chunk = password[i : i + 3]
        if chunk.isdigit():
            if all(int(chunk[j]) + 1 == int(chunk[j + 1]) for j in range(2)):
                return (
                    False,
                    "Password must not contain sequences of 3 or more ascending numbers.",
                )
            if all(int(chunk[j]) - 1 == int(chunk[j + 1]) for j in range(2)):
                return (
                    False,
                    "Password must not contain sequences of 3 or more descending numbers.",
                )

    return True, None

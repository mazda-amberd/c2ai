# pylint: disable=import-error
"""
Password generation and verification helpers.
"""

from __future__ import annotations

import os
import secrets
import string
from random import SystemRandom


from c2ai.core.exceptions import PasswordValidationFailed



def generate_password() -> str:
    """
    Generate a cryptographically secure password.

    The password contains at least one uppercase letter, one lowercase letter,
    one digit, and one special character. Remaining characters are drawn from
    the full allowed set.

    Env vars (with fallback defaults):
        PASSWORD_PREFIX ("")
        PASSWORD_SPECIALS ("!@#$%^&*()_+-")
        PASSWORD_LENGTH (10)

    Returns:
        A generated password string.

    Raises:
        PasswordValidationFailed: If configuration is invalid.
    """
    prefix = os.getenv("PASSWORD_PREFIX", "")
    specials = os.getenv("PASSWORD_SPECIALS", "!@#$%^&*()_+-")
    if not specials:
        raise PasswordValidationFailed(
            "PASSWORD_SPECIALS must contain at least one character"
        )

    pass_len = os.getenv("PASSWORD_LENGTH", 10)
    try:
        length = int(pass_len)
    except (TypeError, ValueError):
        raise PasswordValidationFailed("PASSWORD_LENGTH must be an integer")

    if length <= len(prefix):
        raise PasswordValidationFailed(
            "Length must be greater than the prefix length"
        )

    allowed = string.ascii_letters + string.digits + specials
    tail_len = length - len(prefix)

    if tail_len < 4:
        raise PasswordValidationFailed(
            "Length must allow at least 4 chars after the prefix"
        )

    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(specials),
    ]
    rest = [secrets.choice(allowed) for _ in range(tail_len - 4)]
    tail = required + rest
    SystemRandom().shuffle(tail)
    return prefix + "".join(tail)

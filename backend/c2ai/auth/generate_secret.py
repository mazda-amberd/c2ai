# pylint: disable=import-error
"""Generate a strong ATHENA_AUTH_SECRET.

This prints a `.env`-ready line like:
  ATHENA_AUTH_SECRET="..."

Optionally updates an existing .env file in-place.

Usage (from backend/):
  python -m c2ai.auth.generate_secret            # print a new secret
  python -m c2ai.auth.generate_secret --write    # write/update backend/.env

Notes:
- Output is URL-safe / shell-safe and quoted.
- Default length is 64 characters.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


def generate_secret(*, length: int = 64) -> str:
    """
    Generate a high-entropy secret suitable for signing JWTs.

    Args:
        length (int): Desired length of the secret (minimum 32).

    Returns:
        str: The generated secret string.
    """

    if length < 32:
        raise ValueError("length must be >= 32")

    # token_urlsafe returns ~ (nbytes * 4 / 3) chars.
    # Choose nbytes so we get >= requested length, then trim.
    nbytes = max(32, int(length * 3 / 4) + 1)
    return secrets.token_urlsafe(nbytes)[:length]


def upsert_env_var(env_text: str, key: str, value: str) -> str:
    """
    Insert or replace KEY=... line while preserving other lines.

    Args:
        env_text (str): Existing .env file content.
        key (str): Environment variable name.
        value (str): Value to set.

    Returns:
        str: Updated .env file content.
    """

    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=).*?$", re.MULTILINE)
    new_line = f'{key}="{value}"'

    if pattern.search(env_text):
        return pattern.sub(new_line, env_text)

    # Append (ensure file ends with newline)
    suffix = "" if env_text.endswith("\n") or env_text == "" else "\n"
    return env_text + suffix + new_line + "\n"


def main() -> None:
    """
    Main function to parse arguments and generate/update ATHENA_AUTH_SECRET.
    """
    # Parse command-line arguments for length and write options
    parser = argparse.ArgumentParser(description="Generate ATHENA_AUTH_SECRET")
    # Specify secret length (default: 64) or from ATHENA_AUTH_SECRET_LENGTH env
    # var
    parser.add_argument(
        "--length",
        type=int,
        default=int(os.environ.get("ATHENA_AUTH_SECRET_LENGTH", "64")),
        help="Secret length (default: 64)",
    )
    # If --write is specified, update the env file in-place
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write/update the secret in an env file",
    )
    # Specify path to .env file (default: backend/.env) relative to this
    # script
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parents[2] / ".env"),
        help="Path to .env (default: backend/.env)",
    )

    args = parser.parse_args()

    secret = generate_secret(length=args.length)

    # If --write is specified, update the env file in-place
    if args.write:
        env_path = Path(args.env_file).expanduser().resolve()
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        updated = upsert_env_var(existing, "ATHENA_AUTH_SECRET", secret)
        env_path.write_text(updated, encoding="utf-8")
        print(f"Updated {env_path}:")

    print(f'ATHENA_AUTH_SECRET="{secret}"')


if __name__ == "__main__":
    main()


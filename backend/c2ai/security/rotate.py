"""Re-encrypt stored credentials under the current primary key.

    python -m c2ai.security.rotate               # re-encrypt everything not under the primary key
    python -m c2ai.security.rotate --check       # count values per key id, change nothing
    python -m c2ai.security.rotate --generate-key

The ``credentials.reencrypt`` job does the same once a day, so values written
by earlier versions (pgcrypto) or under an older key are migrated without
anyone running this by hand. Remove a key from C2AI_ENCRYPTION_KEYS only once
``--check`` shows no values under it.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.models.registered_application import (
    ApplicationLLMConfiguration,
    ContainerApplicationConfiguration,
    GitHubConnection,
)
from c2ai.security import crypto


@dataclass(frozen=True)
class _EncryptedColumn:
    model: type
    key: str
    value: str
    aad: str


COLUMNS = (
    _EncryptedColumn(GitHubConnection, "id", "access_token_encrypted", crypto.GITHUB_TOKEN),
    _EncryptedColumn(
        ContainerApplicationConfiguration,
        "application_version_id",
        "registry_password_encrypted",
        crypto.REGISTRY_PASSWORD,
    ),
    _EncryptedColumn(
        ApplicationLLMConfiguration,
        "application_version_id",
        "api_token_encrypted",
        crypto.LLM_API_TOKEN,
    ),
)


async def _rows(db: AsyncSession, column: _EncryptedColumn):
    key = getattr(column.model, column.key)
    value = getattr(column.model, column.value)
    result = await db.execute(select(key, value).where(value.is_not(None)))
    return result.all()


async def key_usage(session_factory: Callable[[], AsyncSession]) -> dict[str, int]:
    """How many stored values each key id protects ("pgcrypto" = earlier versions)."""

    counts: collections.Counter[str] = collections.Counter()
    async with session_factory() as db:
        for column in COLUMNS:
            for _key, blob in await _rows(db, column):
                counts[crypto.key_id(bytes(blob)) or "pgcrypto"] += 1
    return dict(counts)


async def reencrypt_all(session_factory: Callable[[], AsyncSession]) -> dict[str, int]:
    """Rewrite every value not encrypted with the primary key; return counts."""

    rewritten = 0
    async with session_factory() as db:
        for column in COLUMNS:
            for row_key, blob in await _rows(db, column):
                if not crypto.needs_reencryption(bytes(blob)):
                    continue
                plaintext = await crypto.decrypt_stored(db, bytes(blob), column=column.aad)
                await db.execute(
                    update(column.model)
                    .where(getattr(column.model, column.key) == row_key)
                    .values({column.value: crypto.encrypt(plaintext, column=column.aad)})
                )
                rewritten += 1
        await db.commit()
    return {"rewritten": rewritten, "primary_key": crypto.keyring().primary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report key usage only")
    parser.add_argument("--generate-key", action="store_true", help="print a new random key")
    args = parser.parse_args()
    if args.generate_key:
        print(crypto.generate_key())
        return

    from c2ai.db.session import AsyncSessionLocal

    if args.check:
        print(asyncio.run(key_usage(AsyncSessionLocal)))
    else:
        print(asyncio.run(reencrypt_all(AsyncSessionLocal)))


if __name__ == "__main__":
    main()

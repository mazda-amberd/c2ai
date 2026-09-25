"""Credential encryption in the application (AES-256-GCM with key ids).

Stored credentials (GitHub tokens, registry passwords, LLM API tokens) are
encrypted here, before they reach PostgreSQL, so the key never travels in a
SQL statement (where statement logging could record it). Each ciphertext
names the key that produced it, which makes rotation possible:

    C2AI_ENCRYPTION_KEYS="2026b:<base64 32 bytes>,2026a:<base64 32 bytes>"

The first key encrypts; every listed key can decrypt. After adding a new
first key, the ``credentials.reencrypt`` job rewrites old values and the old
key can be removed once ``python -m c2ai.security.rotate --check`` reports
nothing left under it.

Without ``C2AI_ENCRYPTION_KEYS`` the key is derived from the existing
``ATHENA_CREDENTIAL_ENCRYPTION_KEY`` passphrase (key id ``derived``), so an
upgrade needs no new configuration. Values written by earlier versions with
pgcrypto are still readable (decrypted by PostgreSQL with that passphrase)
and are rewritten by the same job.

Format: b"C2E1" | len(kid) (1 byte) | kid | nonce (12) | ciphertext+tag.
The column name is bound as associated data, so a ciphertext copied into a
different column does not decrypt.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from c2ai.config import get_settings
from c2ai.core.exceptions import ServiceUnavailableError

MAGIC = b"C2E1"
# Associated data per encrypted column.
GITHUB_TOKEN = "github_connections.access_token"
REGISTRY_PASSWORD = "registered_application_container_configs.registry_password"
LLM_API_TOKEN = "registered_application_llm_configs.api_token"
DERIVED_KEY_ID = "derived"
_NONCE_BYTES = 12


class CredentialDecryptionError(ServiceUnavailableError):
    def __init__(self, detail: str = "A stored credential could not be decrypted."):
        super().__init__(detail, code="CredentialDecryptionFailed")


@dataclass(frozen=True)
class Keyring:
    primary: str
    keys: dict[str, bytes]


def _derive(passphrase: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"c2ai-credential-encryption",
        info=b"aes-256-gcm v1",
    ).derive(passphrase.encode())


def parse_keys(raw: str) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        kid, _, encoded = item.partition(":")
        kid = kid.strip()
        if not kid or len(kid.encode()) > 255 or not encoded:
            raise ValueError("C2AI_ENCRYPTION_KEYS entries look like kid:base64key")
        try:
            key = base64.b64decode(encoded.strip(), validate=True)
        except binascii.Error as error:
            raise ValueError(f"Encryption key '{kid}' is not valid base64") from error
        if len(key) != 32:
            raise ValueError(f"Encryption key '{kid}' must be 32 bytes (AES-256)")
        keys[kid] = key
    return keys


@lru_cache(maxsize=4)
def _keyring(configured: str, passphrase: str) -> Keyring | None:
    keys = parse_keys(configured)
    if keys:
        primary = next(iter(keys))
        if passphrase:  # values written under the derived key stay readable
            keys.setdefault(DERIVED_KEY_ID, _derive(passphrase))
        return Keyring(primary=primary, keys=keys)
    if passphrase:
        return Keyring(primary=DERIVED_KEY_ID, keys={DERIVED_KEY_ID: _derive(passphrase)})
    return None


def keyring() -> Keyring:
    settings = get_settings()
    ring = _keyring(settings.encryption_keys, settings.credential_encryption_key.strip())
    if ring is None:
        raise ServiceUnavailableError(
            "Credential storage is not configured. Set C2AI_ENCRYPTION_KEYS "
            "(or ATHENA_CREDENTIAL_ENCRYPTION_KEY)."
        )
    return ring


def generate_key() -> str:
    """A new random key, base64-encoded, for C2AI_ENCRYPTION_KEYS."""

    return base64.b64encode(os.urandom(32)).decode()


def encrypt(plaintext: str, *, column: str) -> bytes:
    ring = keyring()
    kid = ring.primary.encode()
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(ring.keys[ring.primary]).encrypt(nonce, plaintext.encode(), column.encode())
    return MAGIC + bytes([len(kid)]) + kid + nonce + sealed


def is_app_encrypted(blob: bytes | None) -> bool:
    return bool(blob) and bytes(blob[:4]) == MAGIC


def key_id(blob: bytes) -> str | None:
    """The key id of an application ciphertext (None for legacy pgcrypto)."""

    if not is_app_encrypted(blob):
        return None
    length = blob[4]
    return bytes(blob[5 : 5 + length]).decode()


def decrypt(blob: bytes, *, column: str) -> str:
    kid = key_id(blob)
    if kid is None:
        raise CredentialDecryptionError("Legacy credential: use decrypt_stored().")
    key = keyring().keys.get(kid)
    if key is None:
        raise CredentialDecryptionError(
            f"A stored credential was encrypted with key '{kid}', which is not configured."
        )
    offset = 5 + len(kid.encode())
    nonce, sealed = blob[offset : offset + _NONCE_BYTES], blob[offset + _NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(bytes(nonce), bytes(sealed), column.encode()).decode()
    except InvalidTag as error:
        raise CredentialDecryptionError() from error


async def decrypt_stored(db: AsyncSession, blob: bytes | None, *, column: str) -> str | None:
    """Decrypt an application ciphertext, or a value earlier versions wrote with pgcrypto."""

    if blob is None:
        return None
    if is_app_encrypted(blob):
        return decrypt(bytes(blob), column=column)
    passphrase = get_settings().credential_encryption_key.strip()
    if not passphrase:
        raise CredentialDecryptionError(
            "A credential stored by an earlier version needs ATHENA_CREDENTIAL_ENCRYPTION_KEY."
        )
    result = await db.execute(select(func.pgp_sym_decrypt(bytes(blob), passphrase)))
    value = result.scalar_one()
    if not isinstance(value, str) or not value:
        raise CredentialDecryptionError()
    return value


def needs_reencryption(blob: bytes | None) -> bool:
    return blob is not None and key_id(bytes(blob)) != keyring().primary

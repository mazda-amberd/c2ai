"""Application-side credential encryption: format, key ids, rotation rules."""

import base64

import pytest

from c2ai.security import crypto


@pytest.fixture
def keys(monkeypatch):
    new, old = crypto.generate_key(), crypto.generate_key()
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k2:{new},k1:{old}")
    return new, old


def test_round_trip_names_the_primary_key(keys):
    blob = crypto.encrypt("s3cret", column=crypto.GITHUB_TOKEN)
    assert crypto.key_id(blob) == "k2"
    assert b"s3cret" not in blob
    assert crypto.decrypt(blob, column=crypto.GITHUB_TOKEN) == "s3cret"


def test_ciphertext_is_bound_to_its_column(keys):
    blob = crypto.encrypt("s3cret", column=crypto.GITHUB_TOKEN)
    with pytest.raises(crypto.CredentialDecryptionError):
        crypto.decrypt(blob, column=crypto.LLM_API_TOKEN)


def test_older_keys_still_decrypt_and_are_marked_for_rotation(keys, monkeypatch):
    _new, old = keys
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k1:{old}")
    blob = crypto.encrypt("rotate me", column=crypto.LLM_API_TOKEN)
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k2:{keys[0]},k1:{old}")
    assert crypto.decrypt(blob, column=crypto.LLM_API_TOKEN) == "rotate me"
    assert crypto.needs_reencryption(blob)
    assert not crypto.needs_reencryption(crypto.encrypt("x", column=crypto.LLM_API_TOKEN))


def test_a_removed_key_is_reported_by_name(keys, monkeypatch):
    blob = crypto.encrypt("s3cret", column=crypto.GITHUB_TOKEN)
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k9:{crypto.generate_key()}")
    with pytest.raises(crypto.CredentialDecryptionError, match="'k2'"):
        crypto.decrypt(blob, column=crypto.GITHUB_TOKEN)


def test_passphrase_only_derives_a_key(monkeypatch):
    monkeypatch.delenv("C2AI_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", "legacy passphrase")
    blob = crypto.encrypt("value", column=crypto.GITHUB_TOKEN)
    assert crypto.key_id(blob) == crypto.DERIVED_KEY_ID
    # Adding real keys later keeps derived-key values readable.
    monkeypatch.setenv("C2AI_ENCRYPTION_KEYS", f"k1:{crypto.generate_key()}")
    assert crypto.decrypt(blob, column=crypto.GITHUB_TOKEN) == "value"
    assert crypto.needs_reencryption(blob)


@pytest.mark.parametrize(
    "raw",
    ["nokey", "k1:not-base64!", f"k1:{base64.b64encode(b'short').decode()}"],
)
def test_malformed_keys_are_rejected(raw):
    with pytest.raises(ValueError):
        crypto.parse_keys(raw)


def test_unconfigured_storage_is_a_503(monkeypatch):
    monkeypatch.delenv("C2AI_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("ATHENA_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(crypto.ServiceUnavailableError):
        crypto.encrypt("value", column=crypto.GITHUB_TOKEN)

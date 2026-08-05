"""Per-user provider credentials.

The properties worth pinning are the ones whose failure is invisible: a
key stored in the clear, a key handed back out through the API, and one
user's key being spent on another user's turn.
"""
import os

import pytest

os.environ.setdefault("LLM_CREDENTIAL_KEY", "test-master-key-for-unit-tests")

from src.assistant.credentials import (  # noqa: E402
    SUPPORTED_PROVIDERS,
    CredentialEncryptionUnavailable,
    CredentialSummary,
    decrypt,
    encrypt,
    fingerprint,
    validate_provider,
)


def test_ciphertext_does_not_contain_the_secret():
    secret = "sk-ant-super-secret-value"
    token = encrypt(secret)
    assert secret not in token
    assert decrypt(token) == secret


def test_encryption_is_non_deterministic():
    """Two users with the same key must not produce identical ciphertext."""
    assert encrypt("same-key") != encrypt("same-key")


def test_fingerprint_is_stable_and_not_reversible():
    secret = "sk-ant-super-secret-value"
    fp = fingerprint(secret)
    assert fp == fingerprint(secret)
    # Deliberately not the last 4 characters: the tail of an API key is
    # still key material.
    assert fp not in secret
    assert secret not in fp
    assert len(fp) == 8


def test_refuses_to_encrypt_without_a_master_key(monkeypatch):
    """Never accept a secret we cannot protect."""
    monkeypatch.delenv("LLM_CREDENTIAL_KEY", raising=False)
    with pytest.raises(CredentialEncryptionUnavailable):
        encrypt("anything")


def test_rotated_master_key_reports_itself_clearly(monkeypatch):
    token = encrypt("sk-value")
    monkeypatch.setenv("LLM_CREDENTIAL_KEY", "a-completely-different-master-key")
    with pytest.raises(CredentialEncryptionUnavailable) as exc:
        decrypt(token)
    # The message must point at the master key, not at the user's key —
    # otherwise the next person debugs the wrong secret.
    assert "LLM_CREDENTIAL_KEY" in str(exc.value)


@pytest.mark.parametrize("given,expected", [
    ("anthropic", "anthropic"),
    ("Anthropic", "anthropic"),
    ("  MISTRAL  ", "mistral"),
    ("openai", "openai"),
])
def test_provider_normalisation(given, expected):
    assert validate_provider(given) == expected


@pytest.mark.parametrize("bad", ["", "gemini", "llama", "not-a-provider", None])
def test_unsupported_providers_are_refused(bad):
    with pytest.raises(ValueError):
        validate_provider(bad)


def test_summary_never_carries_the_secret():
    from datetime import datetime, timezone
    s = CredentialSummary(
        provider="anthropic", model=None, fingerprint="abcd1234",
        created_at=datetime.now(timezone.utc), last_used_at=None,
    )
    body = s.as_dict()
    assert set(body) == {"provider", "model", "fingerprint", "created_at", "last_used_at"}
    # No field anywhere in the serialised form may carry key material.
    assert "api_key" not in body and "secret" not in body


def test_supported_providers_are_the_ones_we_can_actually_talk_to():
    assert set(SUPPORTED_PROVIDERS) == {"anthropic", "mistral", "openai"}

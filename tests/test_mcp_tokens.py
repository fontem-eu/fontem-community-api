"""Personal access tokens for external MCP clients.

The properties that matter are all negative: the plaintext is never
stored, never re-readable, and a stray header never becomes a database
round-trip.
"""
import pytest

from src.assistant.mcp_tokens import (
    TOKEN_PREFIX,
    generate_token,
    hash_token,
    looks_like_mcp_token,
)


def test_tokens_are_unique_and_prefixed():
    a, b = generate_token(), generate_token()
    assert a != b
    # The prefix makes a leaked token greppable in logs and recognisable
    # to the user as ours rather than a provider key.
    assert a.startswith(TOKEN_PREFIX)
    assert len(a) > len(TOKEN_PREFIX) + 30


def test_hash_is_stable_and_hides_the_token():
    t = generate_token()
    assert hash_token(t) == hash_token(t)
    assert t not in hash_token(t)
    assert len(hash_token(t)) == 64          # sha256 hex


def test_different_tokens_hash_differently():
    assert hash_token(generate_token()) != hash_token(generate_token())


@pytest.mark.parametrize("value,expected", [
    (generate_token(), True),
    ("fontem_mcp_anything", True),
    ("sk-ant-something", False),          # a provider key, not ours
    ("Bearer fontem_mcp_x", False),       # header not stripped
    ("", False),
    (None, False),
])
def test_shape_check_before_touching_the_database(value, expected):
    """A stray Authorization header must not cost a query."""
    assert looks_like_mcp_token(value) is expected

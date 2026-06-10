"""Regression test for the DBAPIError → 400 exception handler.

Schemathesis against /capi/reports/{non-uuid}/sections turned up 500
responses with a traceback ending in asyncpg's `ValueError: invalid
UUID ...`. That exception bubbled up wrapped in SQLAlchemy's
DBAPIError and tripped the generic `Exception` handler. The handler
we just added catches DBAPIError, unwraps the underlying ValueError,
and returns a 400 with a human-readable detail instead.

Can't reproduce the real asyncpg bind failure in unit tests (the
test suite uses the in-memory repo) — so this test drives the
handler with a synthetic DBAPIError directly.
"""
from __future__ import annotations

import asyncpg.exceptions
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError


def test_dbapi_error_with_valueerror_returns_400(client: TestClient):
    """DBAPIError wrapping a ValueError becomes 400, not 500."""
    @client.app.get("/_test/uuid-bind")
    def _raise_bad_uuid():  # pragma: no cover - invoked via HTTP
        raise DBAPIError(
            statement="SELECT 1",
            params={},
            orig=ValueError("invalid UUID 'undefined': length must be between 32..36 characters, got 9"),
        )

    resp = client.get("/_test/uuid-bind")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "Invalid parameter" in body["detail"]
    assert "invalid UUID" in body["detail"]


def test_dbapi_error_without_valueerror_stays_500(client: TestClient):
    """DBAPIError wrapping something else (e.g. connection timeout)
    still falls through to the generic 500 handler — we don't want
    to over-broaden the 400 path."""
    @client.app.get("/_test/connection-drop")
    def _raise_connection_error():  # pragma: no cover - invoked via HTTP
        raise DBAPIError(
            statement="SELECT 1",
            params={},
            orig=ConnectionError("postgres is away for the weekend"),
        )

    resp = client.get("/_test/connection-drop")
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "Internal server error"


def test_dbapi_error_with_nested_valueerror_returns_400(client: TestClient):
    """The real shape asyncpg produces — DBAPIError's `orig` is
    SQLAlchemy's own wrapper, and the ValueError is two `__cause__`
    hops deeper. First scan missed this because exc.orig was only
    checked at the top level."""

    @client.app.get("/_test/uuid-bind-nested")
    def _raise_nested():  # pragma: no cover - invoked via HTTP
        try:
            try:
                raise ValueError("invalid UUID 'undefined': length must be between 32..36 characters, got 9")
            except ValueError as e1:
                # Simulate asyncpg.exceptions.DataError wrapping it.
                raise RuntimeError("asyncpg DataError") from e1
        except RuntimeError as e2:
            # Simulate SQLAlchemy's asyncpg dialect wrapper.
            raise DBAPIError(statement="SELECT 1", params={}, orig=e2) from e2

    resp = client.get("/_test/uuid-bind-nested")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "Invalid parameter" in body["detail"]
    assert "invalid UUID" in body["detail"]


def test_dbapi_error_with_asyncpg_data_error_returns_400(client: TestClient):
    """asyncpg's DataError family (null byte, numeric overflow, bad
    datetime) is the second 4xx-shaped failure asyncpg surfaces — not
    a ValueError, so the original handler returned 500 and Schemathesis
    on 2026-06-10 caught two real cases as Server Error: a null byte
    in a POST /groups body name field, and a null byte in a
    GET /issues entity_id query param. Both became 400 after this fix.
    """
    @client.app.get("/_test/null-byte")
    def _raise_null_byte():  # pragma: no cover - invoked via HTTP
        raise DBAPIError(
            statement="INSERT INTO foo (s) VALUES ($1)",
            params={"s": "x\x00y"},
            orig=asyncpg.exceptions.CharacterNotInRepertoireError(
                'invalid byte sequence for encoding "UTF8": 0x00',
            ),
        )

    resp = client.get("/_test/null-byte")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "Invalid value" in body["detail"]
    assert "CharacterNotInRepertoireError" in body["detail"]


def test_dbapi_error_with_asyncpg_numeric_overflow_returns_400(client: TestClient):
    """Confirm the broader DataError family (not just null bytes) lands
    in the 400 path — the 2026-05-10 int8-overflow case the generic
    handler used to log as an unhandled 500 is now a clean 400.
    """
    @client.app.get("/_test/int8-overflow")
    def _raise_overflow():  # pragma: no cover - invoked via HTTP
        raise DBAPIError(
            statement="SELECT * FROM foo LIMIT $1 OFFSET $2",
            params={"limit": 50, "offset": 10**40},
            orig=asyncpg.exceptions.NumericValueOutOfRangeError(
                "value out of int8 range",
            ),
        )

    resp = client.get("/_test/int8-overflow")
    assert resp.status_code == 400, resp.text
    assert "NumericValueOutOfRangeError" in resp.json()["detail"]

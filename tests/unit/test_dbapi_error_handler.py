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

"""Smoke test for /openapi.json.

Regression guard: FastAPI crashed /openapi.json with a 500 when the
``/assist/chat/stream`` handler had a ``-> StreamingResponse`` return
annotation under ``from __future__ import annotations`` (the string
ForwardRef tripped Pydantic during schema generation). If somebody
reintroduces that pattern on another streaming endpoint, this test
fails before DAST finds it.
"""
from __future__ import annotations


def test_openapi_json_is_well_formed(client):
    """/openapi.json returns 200 and a parseable OpenAPI document."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    # The shape is OpenAPI 3.x: top-level keys 'openapi', 'info', 'paths'.
    assert "openapi" in doc
    assert "paths" in doc
    # Sanity: the known streaming endpoint is listed.
    assert "/assist/chat/stream" in doc["paths"]

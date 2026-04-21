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


def test_authenticated_routers_declare_auth_responses(client):
    """401 / 403 / 429 are declared on authenticated endpoints.

    Schemathesis counted these as "undocumented status codes" until we
    attached AUTH_RESPONSES to every auth-requiring router. Pick a
    representative endpoint from each and check the response codes are
    in its OpenAPI definition — regresses if someone declares a new
    router without the shared `responses=`.
    """
    doc = client.get("/openapi.json").json()
    # One endpoint per authenticated router — if any is removed or
    # renamed the test guides you to the right place rather than a
    # confusing `KeyError: '/reports'`.
    samples = [
        "/reports",           # reports.router
        "/issues",            # issues.router
        "/groups",            # groups.router
        "/users/me",          # users.router
        "/assist/usage",      # assistant router
    ]
    for path in samples:
        spec = doc["paths"].get(path)
        assert spec is not None, f"{path} missing from /openapi.json"
        # Pick the first method defined on the path — all should carry
        # the same `responses` because they live on the same router.
        method_spec = next(iter(spec.values()))
        responses = method_spec.get("responses", {})
        for code in ("401", "403", "429"):
            assert code in responses, (
                f"{path} missing '{code}' in openapi responses — "
                f"router likely missing responses=AUTH_RESPONSES"
            )

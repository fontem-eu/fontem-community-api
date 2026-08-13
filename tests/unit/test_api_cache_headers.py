"""Nothing this API returns belongs in a cache.

Verified against the dast environment on 2026-08-13: /capi/investigations,
/capi/studio/projects and /capi/activity all answered 200 with a user's own
records and no cache directives at all, so a browser or an intermediary was
free to keep them and hand them to whoever asked next.

ZAP reports it as "Re-examine Cache-control Directives" (160 instances).
The objection that matters is not the count: these are somebody's private
records, and a response that does not say "no-store" is a response that may
be stored.
"""
import pytest


def test_an_api_response_says_no_store(client):
    r = client.get("/capi/activity")
    assert r.headers.get("cache-control") == "no-store"


def test_an_unauthenticated_response_says_it_too(client):
    # A 401 body is small, but the header set must not depend on the
    # outcome — that is how headers go missing on exactly the paths nobody
    # checks.
    r = client.get("/capi/investigations")
    assert r.status_code in (200, 401, 403, 404, 422)
    assert r.headers.get("cache-control") == "no-store"


def test_the_existing_security_headers_are_still_there(client):
    # The middleware sets three things; adding one must not drop the others.
    r = client.get("/capi/activity")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_a_route_with_its_own_opinion_about_caching_keeps_it(client):
    # setdefault, not assignment: a route that has a considered opinion
    # about caching must be able to keep it. Registered on the live app the
    # fixture already built, so this pins the middleware's behaviour rather
    # than a second app that shares none of its wiring.
    from fastapi.responses import JSONResponse  # pylint: disable=import-outside-toplevel
    from src.api.app import app  # pylint: disable=import-outside-toplevel

    @app.get("/capi/_cache_probe")
    async def _probe():  # pragma: no cover - exercised through the client
        return JSONResponse({"ok": True},
                            headers={"Cache-Control": "public, max-age=60"})

    try:
        r = client.get("/capi/_cache_probe")
        assert r.headers.get("cache-control") == "public, max-age=60"
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "path", None) != "/capi/_cache_probe"
        ]

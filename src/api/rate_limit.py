"""Per-endpoint IP rate limiter.

Defense in depth on top of nginx's general rate limiting (gmr-web's
rate-limit.conf — 2 req/s burst, 1 req/s sustained per IP on /capi/).
This layer adds tighter limits on auth endpoints specifically, so
brute-force and credential-stuffing get throttled before they can
exhaust account-level lockout retries.

Tests disable this limiter via ``limiter.enabled = False`` in
``tests/conftest.py`` so unit-test bursts don't trip it.
"""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request: Request) -> str:
    """Resolve the real client IP behind the gmr-web reverse proxy.

    Without this, ``slowapi.util.get_remote_address`` returns
    ``request.client.host`` — which behind nginx is the nginx pod's
    own IP. Every user in the cluster then shares the same per-route
    bucket, and any moderate concurrency (a smoke run + one human
    signing in at the same time) trips the 10/minute /auth/login
    limit *globally* rather than per-attacker. That's how STORY-12
    in the smoke suite started 404'ing on the read view: the test
    couldn't refresh its session under load, the SPA's 401-clears-
    token branch fired, and the follow-up GET went anonymous.

    nginx forwards the original client address via
    ``X-Forwarded-For``; the leftmost entry is the original
    client and each proxy in the chain appends to the right.
    We trust that header here because /capi/ is only reachable
    through nginx, which always sets it.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)

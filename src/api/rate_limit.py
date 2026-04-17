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

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

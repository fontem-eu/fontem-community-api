"""Tests for the rate-limit key function.

Background: behind the gmr-web reverse proxy + the linkerd
sidecar, ``request.client.host`` reports the API pod's own IP for
every request — so the SlowAPI default ``get_remote_address`` was
keying the rate limit on a single value across the whole cluster.
A few concurrent /auth/login attempts would trip the 10/minute
budget *globally* and 429 every subsequent caller. The fix reads
the X-Forwarded-For header (set by nginx) and falls back to the
remote address only when that header is absent.
"""
# pylint: disable=missing-function-docstring
from types import SimpleNamespace

from src.api import rate_limit
from src.api.rate_limit import _client_ip


def _request(*, headers=None, host="10.42.3.55"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=host),
    )


class TestClientIp:
    def test_returns_leftmost_xff_when_present(self):
        req = _request(
            headers={"x-forwarded-for": "203.0.113.5, 10.42.0.4, 10.42.3.87"},
        )
        assert _client_ip(req) == "203.0.113.5"

    def test_falls_back_to_x_real_ip_when_xff_missing(self):
        req = _request(headers={"x-real-ip": "203.0.113.99"})
        assert _client_ip(req) == "203.0.113.99"

    def test_falls_back_to_remote_address_when_neither_header(self):
        req = _request(host="10.42.3.55")
        assert _client_ip(req) == "10.42.3.55"

    def test_handles_xff_with_whitespace(self):
        req = _request(headers={"x-forwarded-for": "  203.0.113.5  ,  10.42.0.4"})
        assert _client_ip(req) == "203.0.113.5"

    def test_handles_single_xff_value(self):
        req = _request(headers={"x-forwarded-for": "203.0.113.5"})
        assert _client_ip(req) == "203.0.113.5"

    def test_xff_takes_precedence_over_x_real_ip(self):
        req = _request(headers={
            "x-forwarded-for": "203.0.113.5",
            "x-real-ip": "198.51.100.42",
        })
        assert _client_ip(req) == "203.0.113.5"

    def test_empty_xff_falls_through(self):
        # An empty XFF (uncommon but defensively handled) should not
        # be treated as the client IP — fall through to the next signal.
        req = _request(headers={"x-forwarded-for": ""}, host="10.42.3.55")
        assert _client_ip(req) == "10.42.3.55"

    def test_xff_only_commas_falls_through(self):
        req = _request(headers={"x-forwarded-for": " , , "}, host="10.42.3.55")
        assert _client_ip(req) == "10.42.3.55"


def test_scaled_multiplies_every_limit(monkeypatch):
    """Non-prod envs raise every per-IP limit with one knob; prod stays at 1."""
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MULTIPLIER", 1)
    assert rate_limit.scaled("10/minute") == "10/minute"
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MULTIPLIER", 1000)
    assert rate_limit.scaled("3/minute") == "3000/minute"
    assert rate_limit.scaled("20/hour") == "20000/hour"

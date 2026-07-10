"""CSP violation report endpoint — anonymous, 204, best-effort."""
from __future__ import annotations


class TestCspReport:
    def test_valid_report_returns_204(self, client):
        r = client.post("/csp-report", json={"csp-report": {
            "violated-directive": "img-src",
            "blocked-uri": "https://fontem.eu/fontem-prod/x.png",
            "document-uri": "https://www.fontem.eu/users/abc",
        }})
        assert r.status_code == 204, r.text

    def test_garbage_body_still_204(self, client):
        r = client.post("/csp-report", content=b"not json",
                        headers={"content-type": "application/csp-report"})
        assert r.status_code == 204

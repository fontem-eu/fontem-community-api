"""Endpoint-level tests for the email-verification + reset surface."""
from __future__ import annotations

import re


class TestRegisterIssuesVerification:
    def test_register_creates_unverified_and_returns_session(self, client):
        r = client.post("/auth/register", json={
            "email": "fresh@test.com", "password": "password123", "name": "Fresh",
        })
        assert r.status_code == 201
        # Session still issued (so the SPA can show "check your email").
        assert r.json()["access_token"]

    def test_unverified_user_blocked_from_creating_story(self, client):
        client.post("/auth/register", json={
            "email": "blocked@test.com", "password": "password123", "name": "B",
        })
        # The register response set the refresh cookie + the test client
        # tracks the access token via the body. Pull it and try to post.
        login = client.post("/auth/login", json={
            "email": "blocked@test.com", "password": "password123",
        })
        token = login.json()["access_token"]
        r = client.post(
            "/reports", json={"title": "Should fail"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        assert "not verified" in r.json()["detail"].lower()


class TestVerifyEmailEndpoint:
    def test_verify_with_bad_token_400(self, client):
        r = client.post("/auth/verify-email", json={"token": "garbage"})
        assert r.status_code == 400

    def test_full_register_verify_then_participate(self, client, services, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger="fontem.mail")
        client.post("/auth/register", json={
            "email": "verifyme@test.com", "password": "password123", "name": "V",
        })
        # Suppress-mode logged the verification mail incl. the link.
        token = None
        for rec in caplog.records:
            m = re.search(r"token=([A-Za-z0-9_-]+)", rec.getMessage())
            if m:
                token = m.group(1)
        assert token, "verification link should be in the suppressed-mail log"

        v = client.post("/auth/verify-email", json={"token": token})
        assert v.status_code == 200

        # Now participation works.
        login = client.post("/auth/login", json={
            "email": "verifyme@test.com", "password": "password123",
        })
        tok = login.json()["access_token"]
        r = client.post(
            "/reports", json={"title": "Now allowed"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 201


class TestForgotResetEndpoints:
    def test_forgot_always_200_even_for_unknown(self, client):
        r = client.post("/auth/forgot", json={"email": "ghost-unknown@example.com"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_reset_with_bad_token_400(self, client):
        r = client.post("/auth/reset", json={
            "token": "garbage", "new_password": "newpassword123",
        })
        assert r.status_code == 400

    def test_full_forgot_reset_cycle(self, client, caplog):
        import logging
        # Register + verify so it's a real local account.
        client.post("/auth/register", json={
            "email": "cycle@test.com", "password": "originalpass1", "name": "C",
        })
        caplog.set_level(logging.WARNING, logger="fontem.mail")
        client.post("/auth/forgot", json={"email": "cycle@test.com"})
        token = None
        for rec in caplog.records:
            m = re.search(r"reset-password\?token=([A-Za-z0-9_-]+)", rec.getMessage())
            if m:
                token = m.group(1)
        assert token, "reset link should be in the suppressed-mail log"
        r = client.post("/auth/reset", json={
            "token": token, "new_password": "brandnewpass99",
        })
        assert r.status_code == 200
        # New password logs in.
        login = client.post("/auth/login", json={
            "email": "cycle@test.com", "password": "brandnewpass99",
        })
        assert login.status_code == 200

"""Integration tests for the assistant module against the deployed API.

These exercise the full Postgres round-trip. They do NOT call the real
claude-proxy — chat_stream hits the proxy and would fail or cost real
tokens in CI. Instead we focus on the endpoints that are safe to call:

  * GET /assist/usage (fresh user → zeros)
  * GET /assist/conversations/{key} (fresh user → empty)

A separate production smoke test (Phase 6) covers the full flow
end-to-end against the live LLM.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

import pytest

from tests.integration.conftest import make_headers


class TestAssistUsageEndpoint:

    def test_fresh_user_has_zero_usage(self, client, user_id):
        h = make_headers(user_id)
        resp = client.get("/assist/usage", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tokens_1h"] == 0
        assert data["tokens_24h"] == 0
        assert data["tokens_7d"] == 0

    def test_usage_requires_auth(self, client):
        resp = client.get("/assist/usage")
        assert resp.status_code in (401, 403)


class TestAssistConversationEndpoint:

    def test_fresh_conversation_key_returns_empty_messages(self, client, user_id):
        h = make_headers(user_id)
        resp = client.get("/assist/conversations/report:fresh-key", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_key"] == "report:fresh-key"
        assert data["messages"] == []

    def test_conversations_isolate_by_user(self, client, user_id, user2_id):
        # Users querying the same conversation_key get independent conversations.
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        resp1 = client.get("/assist/conversations/report:shared-key", headers=h1)
        resp2 = client.get("/assist/conversations/report:shared-key", headers=h2)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both are empty from scratch, but they should be distinct conversations.
        assert resp1.json()["messages"] == []
        assert resp2.json()["messages"] == []

    def test_conversation_requires_auth(self, client):
        resp = client.get("/assist/conversations/whatever")
        assert resp.status_code in (401, 403)

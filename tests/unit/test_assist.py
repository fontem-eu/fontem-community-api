"""Endpoint-level tests for the assistant router.

These use the TestClient + dependency overrides to replace the live
proxy with a fake. They verify the HTTP contract only — business
logic of the turn is already covered by test_service.py.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

import asyncio

import pytest

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService
from tests.conftest import make_headers, seed_user


class _FakeProxy:
    def __init__(self, events: list[str] | None = None) -> None:
        self._events = events or [
            "event: chunk\ndata: {\"text\": \"Hello\"}\n\n",
            "event: chunk\ndata: {\"text\": \" world\"}\n\n",
        ]

    async def stream(self, payload):
        for line in self._events:
            yield line


@pytest.fixture(autouse=True)
def fake_assistant(services):
    """Inject a fake-proxy-backed AssistantService into the services dict.

    The dishka InMemoryProvider picks this up and provides it via
    FromDishka[AssistantService] to the router endpoints.
    """
    repo = InMemoryAssistRepository()
    proxy = _FakeProxy()
    service = AssistantService(
        repo=repo,
        proxy_client=proxy,
        base_system_prompt="You are a test assistant.",
        turn_limits=TurnLimits(),
        context_char_budget=8000,
    )
    services["assistant_service"] = service
    services["assist_repo"] = repo
    yield repo


class TestAssistRouter:

    def test_chat_stream_returns_sse(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat/stream",
            json={
                "message": "What are the top contractors in Germany?",
                "conversation_key": "report:abc",
                "context_block": "Report: Germany contractors",
            },
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "Hello" in body
        assert "world" in body

    def test_chat_stream_requires_auth(self, client, fake_assistant):
        resp = client.post(
            "/assist/chat/stream",
            json={
                "message": "hi",
                "conversation_key": "k",
                "context_block": "",
            },
        )
        assert resp.status_code in (401, 403)

    def test_chat_stream_rejects_empty_message(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat/stream",
            json={"message": "", "conversation_key": "k", "context_block": ""},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 422

    def test_usage_starts_at_zero(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.get("/assist/usage", headers=make_headers("user-1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["tokens_1h"] == 0
        assert data["tokens_24h"] == 0
        assert data["tokens_7d"] == 0

    def test_usage_reflects_recorded_turn(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        client.post(
            "/assist/chat/stream",
            json={
                "message": "hello world of pharma contracting",
                "conversation_key": "k",
                "context_block": "",
            },
            headers=h,
        )
        data = client.get("/assist/usage", headers=h).json()
        assert data["tokens_1h"] > 0
        assert data["tokens_24h"] == data["tokens_1h"]

    def test_usage_isolates_users(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-2")
        )
        client.post(
            "/assist/chat/stream",
            json={"message": "q", "conversation_key": "k", "context_block": ""},
            headers=make_headers("user-1"),
        )
        data2 = client.get("/assist/usage", headers=make_headers("user-2")).json()
        assert data2["tokens_1h"] == 0

    def test_get_conversation_returns_empty_for_new_key(
        self, client, services, fake_assistant
    ):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.get(
            "/assist/conversations/report:fresh",
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_get_conversation_returns_recorded_messages(
        self, client, services, fake_assistant
    ):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        client.post(
            "/assist/chat/stream",
            json={
                "message": "first question",
                "conversation_key": "report:abc",
                "context_block": "",
            },
            headers=h,
        )
        resp = client.get(
            "/assist/conversations/report:abc",
            headers=h,
        )
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "first question"
        assert data["messages"][1]["role"] == "assistant"

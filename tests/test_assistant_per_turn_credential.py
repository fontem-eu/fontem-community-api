"""The key must travel with the request, never with the client.

The proxy client is an APP-scoped singleton shared by every request. If a
user's key were stored on it, the next request — a different user — would
spend it. That is the failure this file exists to prevent, and it is the
kind that produces a support ticket about somebody else's bill rather
than a stack trace.
"""
import json

import pytest

from src.assistant.mistral_client import MistralProxyClient


def _events(blocks):
    out = []
    for b in blocks:
        ev = body = None
        for line in b.strip().split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                body = json.loads(line[6:])
        out.append((ev, body))
    return out


@pytest.mark.asyncio
async def test_no_key_at_all_asks_the_user_to_add_one():
    client = MistralProxyClient(api_key="", model="m", gmr_api_url="http://x")
    events = _events([b async for b in client.stream({"message": "hi"})])
    assert events
    ev, body = events[0]
    assert ev == "error"
    assert body["code"] == "no_credential"
    # The message has to tell them what to actually do about it.
    assert "Account settings" in body["error"]


@pytest.mark.asyncio
async def test_platform_key_alone_is_not_a_no_credential_error():
    """A deployment still holding a platform key keeps working."""
    client = MistralProxyClient(api_key="platform-key", model="m", gmr_api_url="http://x")
    events = _events([b async for b in client.stream({"message": "hi"})])
    codes = [b.get("code") for _, b in events if isinstance(b, dict)]
    assert "no_credential" not in codes


@pytest.mark.asyncio
async def test_user_credential_satisfies_a_client_with_no_platform_key():
    """The whole point: the user's own key powers the turn."""
    client = MistralProxyClient(api_key="", model="m", gmr_api_url="http://x")
    blocks = [b async for b in client.stream({
        "message": "hi",
        "credential": {"provider": "mistral", "api_key": "user-key", "model": "user-model"},
    })]
    codes = [b.get("code") for _, b in _events(blocks) if isinstance(b, dict)]
    assert "no_credential" not in codes


@pytest.mark.asyncio
async def test_a_users_key_does_not_persist_onto_the_shared_client():
    """One turn with a user key must not arm the next turn with it."""
    client = MistralProxyClient(api_key="", model="m", gmr_api_url="http://x")
    async for _ in client.stream({
        "message": "hi",
        "credential": {"provider": "mistral", "api_key": "user-a-key", "model": None},
    }):
        pass
    # A second turn, different user, no credential: must refuse rather
    # than quietly spend user A's key.
    events = _events([b async for b in client.stream({"message": "hi"})])
    assert events[0][0] == "error"
    assert events[0][1]["code"] == "no_credential"
    assert client._api_key == ""          # noqa: SLF001 — the whole point

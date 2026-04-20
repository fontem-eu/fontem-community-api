"""Tests for MistralProxyClient.

The client's contract is the same as ClaudeProxyClient's: ``stream(payload)``
yields complete SSE event blocks. These tests pin:

  1. Plain text response → one ``chunk`` and a ``usage`` block.
  2. Tool-call round-trip → ``status`` with ``phase=tool_use``, the tool
     gets dispatched to the GMR API, and the loop continues to a final
     text reply.
  3. ``propose_edit`` tool call emits the ``proposal`` payload on the
     ``status`` event (the frontend depends on this shape).
  4. Missing API key / missing message are graceful errors.
  5. Max-iteration ceiling prevents runaway tool loops.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access
from __future__ import annotations

import json
from typing import Any

import pytest

from src.assistant.mistral_client import MistralProxyClient


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, dict):
            return self._body
        return json.loads(self._body)


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` that returns scripted responses."""

    def __init__(self, script: list[_FakeResponse]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def post(self, url, headers=None, json=None, **_):  # pylint: disable=redefined-outer-name,unused-argument
        self.calls.append({"method": "POST", "url": url, "json": json})
        return self._script.pop(0)

    async def get(self, url, params=None, **_):
        self.calls.append({"method": "GET", "url": url, "params": params})
        return self._script.pop(0)


def _ai(content: str) -> _FakeResponse:
    """Assistant message with plain text (finish_reason=stop)."""
    return _FakeResponse(200, {
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def _tc(name: str, args: dict, call_id: str = "call_1") -> _FakeResponse:
    """Assistant message with one tool call (finish_reason=tool_calls)."""
    return _FakeResponse(200, {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    })


def _parse_events(blocks: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE blocks into (event, data-dict) tuples."""
    out = []
    for b in blocks:
        event = None
        data = None
        for line in b.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if event and data is not None:
            out.append((event, json.loads(data)))
    return out


@pytest.mark.asyncio
async def test_plain_text_response_emits_one_chunk_and_usage():
    script = [_ai("AAPL is Apple Inc's NASDAQ ticker.")]
    fake = _FakeAsyncClient(script)

    client = MistralProxyClient(
        api_key="k", client_factory=lambda: fake,
    )
    blocks = [b async for b in client.stream({"system": "sys", "message": "ticker?"})]
    events = _parse_events(blocks)

    # At least: connecting, thinking, streaming, chunk, usage
    names = [e for e, _ in events]
    assert "chunk" in names
    assert "usage" in names
    chunk = next(d for e, d in events if e == "chunk")
    assert chunk["text"] == "AAPL is Apple Inc's NASDAQ ticker."
    usage = next(d for e, d in events if e == "usage")
    assert usage == {"input_tokens": 10, "output_tokens": 5}


@pytest.mark.asyncio
async def test_tool_call_round_trip_to_search_endpoint():
    # Turn 1: model asks to call search_entities.
    # Turn 2: (after tool result is fed back) model replies with text.
    script = [
        _tc("mcp__gmr__search_entities", {"query": "Apple", "limit": 3}, call_id="c1"),
        _FakeResponse(200, '{"entities":[{"name":"Apple Inc.","ticker":"AAPL"}]}'),
        _ai("Found Apple Inc. (AAPL)."),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake-api",
        client_factory=lambda: fake,
    )

    blocks = [b async for b in client.stream({"system": "s", "message": "find apple"})]
    events = _parse_events(blocks)

    # Verify the GMR API was hit with the right params
    get_calls = [c for c in fake.calls if c["method"] == "GET"]
    assert len(get_calls) == 1
    assert get_calls[0]["url"] == "http://fake-api/search"
    assert get_calls[0]["params"] == {"q": "Apple", "limit": 3}

    # A tool_use status event was emitted with the detail string
    tool_status = [d for e, d in events if e == "status" and d.get("phase") == "tool_use"]
    assert len(tool_status) == 1
    assert tool_status[0]["tool"] == "mcp__gmr__search_entities"
    assert "Apple" in tool_status[0]["detail"]

    # Final chunk contains the model's reply
    chunk_events = [d for e, d in events if e == "chunk"]
    assert any("Apple Inc" in c["text"] for c in chunk_events)


@pytest.mark.asyncio
async def test_propose_edit_forwards_args_as_proposal():
    """The frontend reads `proposal` off the status event to render the card."""
    args = {
        "action": "add_section",
        "content": "<p>Analysis of Siemens contracts.</p>",
    }
    script = [
        _tc("mcp__gmr__propose_edit", args),
        _ai("I've proposed the edit above."),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(api_key="k", client_factory=lambda: fake)

    blocks = [b async for b in client.stream({"system": "s", "message": "propose"})]
    events = _parse_events(blocks)

    tool_status = [d for e, d in events if e == "status" and d.get("phase") == "tool_use"]
    assert len(tool_status) == 1
    assert tool_status[0]["tool"] == "mcp__gmr__propose_edit"
    assert tool_status[0]["proposal"] == args

    # No HTTP GET happened — propose_edit is a pure notification
    assert not any(c["method"] == "GET" for c in fake.calls)


@pytest.mark.asyncio
async def test_missing_api_key_emits_error():
    client = MistralProxyClient(api_key="", client_factory=lambda: _FakeAsyncClient([]))
    blocks = [b async for b in client.stream({"system": "s", "message": "hi"})]
    events = _parse_events(blocks)
    assert any(e == "error" for e, _ in events)


@pytest.mark.asyncio
async def test_missing_message_emits_error():
    client = MistralProxyClient(api_key="k", client_factory=lambda: _FakeAsyncClient([]))
    blocks = [b async for b in client.stream({"system": "s", "message": ""})]
    events = _parse_events(blocks)
    assert any(e == "error" for e, _ in events)


@pytest.mark.asyncio
async def test_api_error_status_emits_error_event():
    script = [_FakeResponse(500, "upstream boom")]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(api_key="k", client_factory=lambda: fake)

    blocks = [b async for b in client.stream({"system": "s", "message": "hi"})]
    events = _parse_events(blocks)
    err = next(d for e, d in events if e == "error")
    assert "500" in err["error"]


@pytest.mark.asyncio
async def test_max_iterations_bounds_runaway_tool_loop():
    # The model keeps asking for tools forever — the client must stop.
    tool_resp = _FakeResponse(200, "{}")
    never_ending = []
    for i in range(10):
        never_ending.append(_tc("mcp__gmr__search_entities", {"query": f"q{i}"}, f"c{i}"))
        never_ending.append(tool_resp)
    fake = _FakeAsyncClient(never_ending)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        max_iterations=3, client_factory=lambda: fake,
    )
    blocks = [b async for b in client.stream({"system": "s", "message": "go"})]
    events = _parse_events(blocks)

    # Exactly 3 POSTs were made before giving up
    post_calls = [c for c in fake.calls if c["method"] == "POST"]
    assert len(post_calls) == 3
    # And we emitted an error citing the ceiling
    err = next(d for e, d in events if e == "error")
    assert "Max tool iterations" in err["error"]

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
async def test_max_iterations_emits_truncated_status_not_error():
    """Loop exhaustion is not a hard error — the user got partial output,
    they need to know to retry with a more focused question. Surface as
    `status` phase=truncated so the frontend can render a soft notice."""
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

    post_calls = [c for c in fake.calls if c["method"] == "POST"]
    assert len(post_calls) == 3
    # No error event — exhaustion is now a `status` with phase=truncated
    assert not any(e == "error" for e, _ in events)
    truncated = [d for e, d in events if e == "status" and d.get("phase") == "truncated"]
    assert len(truncated) == 1
    assert "max tool iterations" in truncated[0]["detail"].lower()


# ── Phase 1+2+4 revamp tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_appends_todays_date():
    """The model must know what year it is — otherwise it flags 2026 as
    `unusually forward-dated`. We inject the date on every turn."""
    script = [_ai("ok")]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(api_key="k", client_factory=lambda: fake)
    [_ async for _ in client.stream({"system": "be helpful", "message": "hi"})]

    body = fake.calls[0]["json"]
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    assert "Today's date is " in sys_msg["content"]
    # ISO date format
    assert "20" in sys_msg["content"][-12:]  # 2026-… or later
    # Original system content preserved
    assert "be helpful" in sys_msg["content"]


@pytest.mark.asyncio
async def test_investigate_entity_dispatches_company_then_authority():
    """investigate_entity is the canonical getter. It tries Company first,
    falls back to Authority on 404, and returns one composite payload."""
    script = [
        _tc("mcp__gmr__investigate_entity", {"entity_id": "abc-123"}, "c1"),
        # Company endpoint says 404
        _FakeResponse(404, ""),
        # Authority endpoint hits (this is a Portuguese authority)
        _FakeResponse(200, {"authority_id": "abc-123",
                            "name": "Metro Mondego, S. A.",
                            "country": "PRT"}),
        # Contracts endpoint
        _FakeResponse(200, {"contracts": [{"value_eur": 986546.64}]}),
        # Graph endpoint
        _FakeResponse(200, {"nodes": [{"id": "abc-123"}], "edges": []}),
        _ai("Done."),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )

    blocks = [b async for b in client.stream({"system": "s", "message": "tell me about abc-123"})]
    events = _parse_events(blocks)

    get_urls = [c["url"] for c in fake.calls if c["method"] == "GET"]
    # Tries company first, then authority
    assert get_urls[0] == "http://fake/companies/abc-123"
    assert get_urls[1] == "http://fake/authorities/abc-123"
    # Then contracts (authority path) and graph
    assert any("authorities/abc-123/contracts" in u for u in get_urls)
    assert any("graph/abc-123" in u for u in get_urls)

    # Final chunk produced
    assert any(e == "chunk" for e, _ in events)


@pytest.mark.asyncio
async def test_per_turn_tool_call_dedup():
    """Identical (name, args) calls within one turn return the cached
    result — no duplicate HTTP round-trip, no duplicate token spend."""
    script = [
        _tc("mcp__gmr__search_entities", {"query": "Apple"}, "c1"),
        _FakeResponse(200, '{"companies":[{"gmr_id":"a1","name":"Apple Inc."}]}'),
        # Model asks for the SAME thing again — should be served from cache
        _tc("mcp__gmr__search_entities", {"query": "Apple"}, "c2"),
        # NB: no scripted response for the duplicate get — if the client
        # hits the API again the script underflows and the test errors.
        _ai("Found Apple."),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )

    blocks = [b async for b in client.stream({"system": "s", "message": "find apple twice"})]
    events = _parse_events(blocks)

    # Only ONE GET to /search even though the model called the tool twice
    get_calls = [c for c in fake.calls if c["method"] == "GET"
                 and c["url"].endswith("/search")]
    assert len(get_calls) == 1
    # Both tool_use status events still fired (so the user sees the activity)
    tool_status = [d for e, d in events if e == "status" and d.get("phase") == "tool_use"]
    assert len(tool_status) == 2


@pytest.mark.asyncio
async def test_status_detail_substitutes_human_name_for_uuid():
    """After search_entities surfaces an id→name mapping, subsequent
    tool calls with that id render with the entity's name in the detail
    string the user sees."""
    script = [
        _tc("mcp__gmr__search_entities", {"query": "Metro Mondego"}, "c1"),
        _FakeResponse(200, json.dumps({
            "authorities": [{"authority_id": "uuid-xyz",
                              "name": "Metro Mondego, S. A.",
                              "country": "PRT"}],
        })),
        # Now the model calls investigate_entity with the UUID
        _tc("mcp__gmr__investigate_entity", {"entity_id": "uuid-xyz"}, "c2"),
        _FakeResponse(404, ""),  # not a Company
        _FakeResponse(200, {"authority_id": "uuid-xyz", "name": "Metro Mondego, S. A.",
                            "country": "PRT"}),
        _FakeResponse(200, {"contracts": []}),
        _FakeResponse(200, {"nodes": [], "edges": []}),
        _ai("done"),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )

    blocks = [b async for b in client.stream({"system": "s", "message": "investigate"})]
    events = _parse_events(blocks)

    investigate_status = [
        d for e, d in events
        if e == "status" and d.get("phase") == "tool_use"
        and d.get("tool") == "mcp__gmr__investigate_entity"
    ]
    assert len(investigate_status) == 1
    # The detail string should contain the human name, not the UUID
    detail = investigate_status[0]["detail"]
    assert "Metro Mondego" in detail
    assert "uuid-xyz" not in detail


@pytest.mark.asyncio
async def test_proposal_budget_disclosure_when_many_edits():
    """When the assistant emits >8 propose_edit calls in one turn, the
    final user-facing message gets a "I proposed N edits" footer so the
    user knows to review them in order."""
    script: list[_FakeResponse] = []
    for i in range(9):
        script.append(_tc("mcp__gmr__propose_edit",
                          {"action": "add_section",
                           "content": f"<p>section {i}</p>"},
                          f"c{i}"))
    script.append(_ai("Here's the structure."))
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", max_iterations=20,
        client_factory=lambda: fake,
    )
    blocks = [b async for b in client.stream({"system": "s", "message": "draft"})]
    events = _parse_events(blocks)

    chunk = next(d for e, d in events if e == "chunk")
    assert "9 edits" in chunk["text"] or "9 edits" in chunk["text"].lower()


@pytest.mark.asyncio
async def test_proposal_budget_silent_when_few_edits():
    """For ≤8 proposals, the user-facing message stays clean (no footer)."""
    script: list[_FakeResponse] = []
    for i in range(3):
        script.append(_tc("mcp__gmr__propose_edit",
                          {"action": "add_section",
                           "content": f"<p>section {i}</p>"},
                          f"c{i}"))
    script.append(_ai("Done."))
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", max_iterations=20,
        client_factory=lambda: fake,
    )
    blocks = [b async for b in client.stream({"system": "s", "message": "draft"})]
    events = _parse_events(blocks)

    chunk = next(d for e, d in events if e == "chunk")
    assert "edits" not in chunk["text"] or "I proposed" not in chunk["text"]


@pytest.mark.asyncio
async def test_legacy_tools_still_callable_but_not_advertised():
    """Old saved conversations may have called get_company / get_authority
    / get_contracts / explore_graph. Those still work in _execute_tool
    (back-compat), but the model only sees the canonical tool surface."""
    from src.assistant.mistral_client import _TOOLS  # pylint: disable=import-outside-toplevel
    advertised_names = {t["function"]["name"] for t in _TOOLS}
    # Canonical surface
    assert "mcp__gmr__investigate_entity" in advertised_names
    assert "mcp__gmr__search_entities" in advertised_names
    assert "mcp__gmr__find_paths" in advertised_names
    assert "mcp__gmr__propose_edit" in advertised_names
    # Legacy NOT advertised
    assert "mcp__gmr__get_company" not in advertised_names
    assert "mcp__gmr__get_authority" not in advertised_names
    assert "mcp__gmr__get_contracts" not in advertised_names
    assert "mcp__gmr__explore_graph" not in advertised_names

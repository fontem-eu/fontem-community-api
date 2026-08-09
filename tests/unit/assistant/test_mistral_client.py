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
# pylint: disable=protected-access,expression-not-assigned
# ── ``[_ async for _ in stream(...)]`` is the test-side drain idiom
#    for an async-generator; the list is intentionally discarded.
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

    def raise_for_status(self) -> None:
        """Mimic httpx.Response.raise_for_status — raise on 4xx/5xx so
        the freshness fetcher's try/except actually trips."""
        if self.status_code >= 400:
            import httpx as _httpx  # pylint: disable=import-outside-toplevel
            raise _httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` that returns scripted responses.

    The freshness endpoint (``/data-quality/freshness``) is hit
    once at the top of every ``stream`` call. To keep the existing tests
    readable, we auto-respond with an empty sources list unless the
    test explicitly scripts a freshness response by passing
    ``freshness_response``.
    """

    def __init__(
        self,
        script: list[_FakeResponse],
        freshness_response: _FakeResponse | None = None,
    ) -> None:
        self._script = list(script)
        self._freshness_response = freshness_response or _FakeResponse(
            200, {"sources": [], "generated_at": None},
        )
        # Empty by default: the catalogue is a nicety, and a test that does
        # not opt into it should see the same prompt it always did.
        self._catalogue_response = _FakeResponse(
            200, {"producers": [], "datasets": []},
        )
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
        # Context blocks are fetched per turn and are not part of the
        # scripted tool exchange. Matching them here keeps every test from
        # having to script a response it does not care about — and matching
        # on the real paths means a path regression breaks these tests
        # rather than being silently swallowed, which is how the old
        # source-freshness 404 survived so long.
        if "/catalogue" in url:
            return self._catalogue_response
        if "/openapi.json" in url:
            # Generated tool schemas. No annotated endpoints in the fake, so
            # the assistant falls back to its hand-written surface.
            return _FakeResponse(200, {"paths": {}})
        if "/data-quality/graph" in url or "/atlas/datasets" in url:
            # Pre-/catalogue fallback shape. Answered here so a deployment
            # that predates /catalogue does not consume scripted turns.
            return _FakeResponse(200, {"nodes": {}} if "graph" in url else [])
        if "freshness" in url:
            return self._freshness_response
        return self._script.pop(0)



def _is_context_fetch(url: str) -> bool:
    """Per-turn context blocks, not the business call a test is asserting on.

    Kept as one predicate so adding another context source does not mean
    hunting down every filter that has to learn about it — which is how the
    old source-freshness path stayed wrong in three places at once.
    """
    return any(part in url for part in
               ("freshness", "/catalogue", "/data-quality/graph",
                "/atlas/datasets", "/openapi.json"))


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
    # Turn 2: it replies with text, having only searched — that is a stall,
    #         so the client pushes it on rather than accepting the answer.
    # Turn 3: it investigates, and only then is allowed to answer.
    script = [
        _tc("mcp__gmr__search_entities", {"query": "Apple", "limit": 3}, call_id="c1"),
        _FakeResponse(200, '{"entities":[{"name":"Apple Inc.","ticker":"AAPL"}]}'),
        _ai("I found some names."),
        _tc("mcp__gmr__find_paths", {"from_id": "a", "to_id": "b"}, call_id="c2"),
        _FakeResponse(200, '{"paths":[]}'),
        _ai("Found Apple Inc. (AAPL)."),
    ]
    fake = _FakeAsyncClient(script)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake-api",
        client_factory=lambda: fake,
    )

    blocks = [b async for b in client.stream({"system": "s", "message": "find apple"})]
    events = _parse_events(blocks)

    # Verify the GMR API was hit with the right params. Filter out
    # the freshness probe that now fires at the top of every stream().
    get_calls = [
        c for c in fake.calls
        if c["method"] == "GET" and not _is_context_fetch(c["url"])
    ]
    # The search itself — asserted by URL rather than by position, since
    # the turn now continues past it rather than stopping there.
    search_calls = [c for c in get_calls if c["url"].endswith("/search")]
    assert len(search_calls) == 1
    assert search_calls[0]["params"] == {"q": "Apple", "limit": 3}

    # A tool_use status event was emitted with the detail string
    tool_status = [d for e, d in events if e == "status" and d.get("phase") == "tool_use"]
    assert [t["tool"] for t in tool_status] == [
        "mcp__gmr__search_entities",
        "mcp__gmr__find_paths",
    ]
    assert "Apple" in tool_status[0]["detail"]

    # Searching alone is a stall: the client pushed the model on instead of
    # accepting "I found some names" as the answer.
    posts = [c for c in fake.calls if c["method"] == "POST"]
    assert any(c["json"].get("tool_choice") == "required" for c in posts), (
        "a stalled turn must be continued with a forced tool call"
    )
    thinking = [d for e, d in events if e == "thinking"]
    assert any("found some names" in t["text"] for t in thinking), (
        "the abandoned reasoning should be surfaced, not discarded"
    )

    # Final chunk contains the model's reply
    chunk_events = [d for e, d in events if e == "chunk"]
    assert any("Apple Inc" in c["text"] for c in chunk_events)


@pytest.mark.asyncio
async def test_propose_edit_forwards_args_as_proposal():
    """The frontend reads `proposal` off the status event to render the card."""
    args = {
        "action": "insert_content",
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

    # No HTTP GET against business endpoints — propose_edit is a pure
    # notification. (The freshness probe is allowed.)
    business_gets = [
        c for c in fake.calls
        if c["method"] == "GET" and not _is_context_fetch(c["url"])
    ]
    assert not business_gets


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

    # First POST is the call to Mistral (the freshness fetch is a GET).
    post_call = next(c for c in fake.calls if c["method"] == "POST")
    body = post_call["json"]
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

    get_urls = [
        c["url"] for c in fake.calls
        if c["method"] == "GET" and not _is_context_fetch(c["url"])
    ]
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
        # Having only searched, that reply is a stall, so the turn is
        # pushed on once. Something other than a search has to happen
        # before the answer is accepted.
        _tc("mcp__gmr__find_paths", {"from_id": "a1", "to_id": "b2"}, "c3"),
        _FakeResponse(200, '{"paths":[]}'),
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
    # Both search tool_use events still fired (so the user sees the
    # activity even though the second was served from cache). The
    # find_paths call after the continuation is counted separately —
    # what matters here is that the duplicate search was not hidden.
    tool_status = [d for e, d in events if e == "status" and d.get("phase") == "tool_use"]
    searches = [t for t in tool_status if t["tool"] == "mcp__gmr__search_entities"]
    assert len(searches) == 2


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
                          {"action": "insert_content",
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
                          {"action": "insert_content",
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


# ── Source-freshness injection tests ─────────────────────────────────


def test_format_freshness_summary_renders_each_source_once():
    """One bullet per source, alphabetical by id, including coverage
    range and a freshness note (or `STALE` flag for old loads)."""
    from src.assistant.mistral_client import _format_freshness_summary  # pylint: disable=import-outside-toplevel
    summary = _format_freshness_summary([
        {
            "id": "sanctions", "label": "EU consolidated sanctions",
            "coverage_start": "2026-01-01", "coverage_end": "2026-04-29",
            "record_count": 3015, "expected_cadence_hours": 25,
            "age_hours": 2.0, "stale": False,
        },
        {
            "id": "openfigi", "label": "OpenFIGI tickers",
            "coverage_start": None, "coverage_end": None,
            "record_count": 12345, "expected_cadence_hours": 200,
            "age_hours": 600.0, "stale": True,
        },
    ])
    assert summary  # not empty
    # Header tells the model what this is for.
    assert "coverage" in summary.lower()
    # Alphabetical: openfigi before sanctions.
    assert summary.index("openfigi") < summary.index("sanctions")
    # Coverage range surfaced when we have it.
    assert "2026-01-01 → 2026-04-29" in summary
    # Stale flag is loud — capital STALE so the model can't miss it.
    assert "STALE" in summary
    # Record counts get thousands separators for legibility.
    assert "3,015" in summary or "12,345" in summary


def test_format_freshness_summary_empty_on_no_sources():
    """Defensive: an empty list produces an empty string so the caller
    can short-circuit injection (no half-empty section in the prompt)."""
    from src.assistant.mistral_client import _format_freshness_summary  # pylint: disable=import-outside-toplevel
    assert _format_freshness_summary([]) == ""


@pytest.mark.asyncio
async def test_freshness_summary_injected_into_system_prompt():
    """When the data-quality endpoint returns sources, those source
    bullets must appear in the system message of the first POST to
    Mistral. This is what gives the assistant grounded coverage
    statements ('I checked sanctions through 2026-04-29 …')."""
    freshness = _FakeResponse(200, {
        "sources": [
            {
                "id": "sanctions", "label": "EU consolidated sanctions",
                "coverage_start": "2026-01-01", "coverage_end": "2026-04-29",
                "record_count": 3015, "expected_cadence_hours": 25,
                "age_hours": 2.0, "stale": False,
            },
        ],
        "generated_at": "2026-04-29T09:00:00+00:00",
    })
    fake = _FakeAsyncClient([_ai("ok")], freshness_response=freshness)
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )
    [_ async for _ in client.stream({"system": "be helpful", "message": "hi"})]

    body = fake.calls[-1]["json"]  # the POST to Mistral
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    assert "EU consolidated sanctions" in sys_msg["content"]
    assert "2026-01-01 → 2026-04-29" in sys_msg["content"]
    # Original system content + today's date still present too.
    assert "be helpful" in sys_msg["content"]
    assert "Today's date is " in sys_msg["content"]


@pytest.mark.asyncio
async def test_freshness_fetch_failure_is_silent():
    """A 500 from the data-quality endpoint must not surface as an
    error to the user — the chat continues with no coverage block.
    Best-effort monitoring data shouldn't sink the chat."""
    fake = _FakeAsyncClient(
        [_ai("ok")],
        freshness_response=_FakeResponse(500, "boom"),
    )
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )
    blocks = [b async for b in client.stream({"system": "s", "message": "hi"})]
    events = _parse_events(blocks)
    # No error event surfaced from the freshness probe.
    assert not any(e == "error" for e, _ in events)
    # And the system prompt didn't pick up a stale coverage block.
    body = fake.calls[-1]["json"]
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    assert "Data coverage" not in sys_msg["content"]


@pytest.mark.asyncio
async def test_freshness_summary_cached_across_turns():
    """Within the cache TTL, subsequent stream() calls must NOT re-hit
    the data-quality endpoint. One client instance, two turns, one
    freshness GET."""
    fake = _FakeAsyncClient([_ai("ok"), _ai("ok2")])
    client = MistralProxyClient(
        api_key="k", gmr_api_url="http://fake",
        client_factory=lambda: fake,
    )
    [_ async for _ in client.stream({"system": "s", "message": "first"})]
    [_ async for _ in client.stream({"system": "s", "message": "second"})]

    freshness_calls = [
        c for c in fake.calls
        if c["method"] == "GET" and "freshness" in c["url"]
    ]
    assert len(freshness_calls) == 1


# ── GMR_API_INTERNAL rename regression ────────────────────────────────


def test_default_gmr_api_url_points_at_fontem_api_not_the_stale_gmr_name():
    """The pre-rename default was http://gmr-api.gmr.svc.cluster.local —
    that DNS name is now NXDOMAIN in every fontem-* namespace, which
    silently broke every MCP/assistant tool call (every search,
    get_company, get_contracts, ...). The deployment manifest now sets
    GMR_API_INTERNAL explicitly, but a fresh dev/test env still falls
    back to this default. Pin that the default targets the post-rename
    Service so a future change can't quietly reintroduce the stale
    name.
    """
    from src.assistant.mistral_client import _DEFAULT_GMR_API  # pylint: disable=import-outside-toplevel
    assert "gmr-api" not in _DEFAULT_GMR_API
    assert _DEFAULT_GMR_API == "http://fontem-api"


def test_env_override_wins_over_default():
    """GMR_API_INTERNAL env var override (set by the chart per-env) must
    take precedence over the in-code default. Pins the two-layer
    contract: default for dev/test, env override for cluster.
    """
    import importlib  # pylint: disable=import-outside-toplevel
    import os  # pylint: disable=import-outside-toplevel
    from unittest.mock import patch  # pylint: disable=import-outside-toplevel
    custom_url = "http://fontem-api.fontem-prod.svc.cluster.local"
    with patch.dict(os.environ, {"GMR_API_INTERNAL": custom_url}, clear=False):
        # llm_service.py reads at import time — reload to pick up the
        # patched env.
        from src.services import llm_service  # pylint: disable=import-outside-toplevel
        importlib.reload(llm_service)
        from src.services.llm_service import GMR_API_URL  # pylint: disable=import-outside-toplevel
        assert GMR_API_URL == custom_url

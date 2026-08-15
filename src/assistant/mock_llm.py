"""A model that does exactly what the test told it to.

The assistant e2e tests were asserting a 1.7B's judgement. ASSIST-23 asks
for a grounded contract count, which needs `search_entities` then
`investigate_entity` with the id the first one returned; the 1.7B skips the
search and invents an id perhaps half the time, so the test measured the
model rather than the platform. Worse, non-prod serves only the 1.7B while
production defaults to the 4B, so the gate was never measuring what ships
either way.

This is an OpenAI-compatible chat-completions endpoint that plays a scripted
part. Everything else in the turn is real: the same PydanticAI agent, the
same tool schemas, the same ToolRuntime dispatch against the same fontem-api,
the same SSE contract. Only the token generation is replaced — so a test can
assert the exact sequence of tool calls, and a failure means a tool broke
rather than that a small model had an off day.

It is *not* a recording. Each step reads the tool results already in the
conversation and derives the next call from them: the id handed to
`investigate_entity` is the one `search_entities` actually returned. A test
built on it therefore still fails when a tool starts answering differently,
which is the entire point of running it against a deployed environment.

Off unless ``ASSIST_MOCK_MODEL`` is set. The router 404s when disabled and
the model id is not selectable, so the code shipping in the production image
cannot be reached there — asserted by a test, because "it is behind a flag"
is a claim about configuration and configuration drifts.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

#: Reserved id. Deliberately not in ``LOCAL_MODELS`` — it must never appear
#: in the picker, and a user who somehow stored it in a live environment
#: gets the default back rather than a scripted answer.
MOCK_MODEL_ID = "mock-e2e"

#: What a test puts in its prompt to choose a script. Explicit rather than
#: inferred from the wording: a scenario that triggers on a phrase would
#: fire on a real question that happens to contain it.
SCENARIO_MARKER = "E2E-SCENARIO:"


def enabled() -> bool:
    """Whether the mock model exists in this environment at all."""
    return os.environ.get("ASSIST_MOCK_MODEL", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ── reading the conversation ───────────────────────────────────


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, list):  # content parts
                return " ".join(p.get("text", "") for p in content
                                if isinstance(p, dict))
            return str(content or "")
    return ""


def scenario_of(messages: list[dict]) -> str:
    """Which script this turn is running, '' when none was asked for."""
    text = _last_user_text(messages)
    if SCENARIO_MARKER not in text:
        return ""
    tail = text.split(SCENARIO_MARKER, 1)[1].strip()
    # The name is the first token; the rest of the prompt is prose for the
    # transcript's benefit.
    return tail.split()[0].strip(".,;:").lower() if tail else ""


def _tool_results(messages: list[dict]) -> list[tuple[str, str]]:
    """(tool name, raw result) for every tool message, oldest first.

    OpenAI-style histories put the name on the tool message; PydanticAI
    sends it, but the assistant message's tool_calls are the authority when
    it does not, so both are consulted.
    """
    names: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = (call.get("function") or {})
            if call.get("id"):
                names[call["id"]] = fn.get("name", "")
    out: list[tuple[str, str]] = []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        name = msg.get("name") or names.get(msg.get("tool_call_id", ""), "")
        out.append((name, str(msg.get("content") or "")))
    return out


#: Where search_entities puts its hits, most useful first. It does not
#: return one list — it returns one per entity type:
#:   {"query": …, "companies": [...], "authorities": [...],
#:    "persons": [...], "lobbyists": [...]}
#: Learned by running the tool against testing, after a first version
#: guessed "results"/"entities" and the e2e failed with the real payload
#: attached. Which is what the e2e is for.
_HIT_LISTS = ("companies", "authorities", "persons", "lobbyists",
              "results", "entities", "matches", "items")

#: Whatever the id is called in a hit, in order of specificity.
_ID_KEYS = ("gmr_id", "entity_id", "authority_id", "id")


def _first_entity_id(raw: str) -> str:
    """Pull an id out of whatever search_entities answered.

    Tolerant on shape and strict on outcome: an empty string here becomes a
    visible MOCK-FAIL in the answer rather than a fabricated id — which is
    the behaviour this whole battery exists to catch a model doing.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    for hit in _candidate_hits(data):
        for key in _ID_KEYS:
            value = hit.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _candidate_hits(data) -> list[dict]:
    """Every result object in the payload, best list first."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    hits: list[dict] = []
    for key in _HIT_LISTS:
        value = data.get(key)
        if isinstance(value, list):
            hits += [x for x in value if isinstance(x, dict)]
    if hits:
        return hits
    # Nothing recognised: take any list of objects rather than fail on a
    # key we have not seen before. The id lookup below still decides.
    for value in data.values():
        if isinstance(value, list):
            hits += [x for x in value if isinstance(x, dict)]
    return hits


def _contract_count(raw: str) -> str:
    """The count investigate_entity reported, as a string, or ''."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    props = data.get("props") if isinstance(data.get("props"), dict) else data
    for key in ("contract_count", "contracts", "total_contracts"):
        value = props.get(key)
        if isinstance(value, int):
            return str(value)
    return ""


# ── the scripts ────────────────────────────────────────────────


#: A route that exists in fontem-web's manifest. Pinned here so the script
#: cannot ask for somewhere the app cannot go.
NAVIGATE_TARGET = "/about"

#: What the toolchain script looks for. Named once so the script and its
#: tests cannot disagree about the spelling of a tool.
SEARCH = "mcp__gmr__search_entities"
INVESTIGATE = "mcp__gmr__investigate_entity"


def _toolchain_step(messages: list[dict]) -> dict:
    """Search → investigate → read a doc → navigate → answer.

    Each step is chosen by what has already come back, not by a counter, so
    the script cannot drift out of step with the conversation when a tool
    fails and the agent retries.
    """
    results = _tool_results(messages)
    done = [name for name, _ in results]
    if SEARCH not in done:
        return {"tool": SEARCH, "args": {"query": "Siemens AG", "limit": 5}}
    return _toolchain_after_search(results, done)


def _toolchain_after_search(results: list[tuple[str, str]],
                            done: list[str]) -> dict:
    """The rest of the chain, once a search has answered."""
    search_raw = next(raw for name, raw in results if name == SEARCH)
    entity_id = _first_entity_id(search_raw)
    if not entity_id:
        # Say so instead of inventing one. A test reading this text fails
        # with the reason attached — the opposite of what a small model does
        # when it cannot find an id.
        return {"text": "MOCK-FAIL: search_entities returned no usable id. "
                        f"raw={search_raw[:200]}"}

    if INVESTIGATE not in done:
        return {"tool": INVESTIGATE,
                "args": {"entity_id": entity_id, "contract_limit": 5}}

    investigate_raw = next(raw for name, raw in results if name == INVESTIGATE)
    count = _contract_count(investigate_raw)
    if not count:
        return {"text": "MOCK-FAIL: investigate_entity reported no contract "
                        f"count. raw={investigate_raw[:200]}"}

    if "get_doc" not in done:
        return {"tool": "get_doc", "args": {"article_id": "methodology"}}
    if "navigate" not in done:
        # `/about` because it exists. The first version used `/companies`,
        # which is not a route in the manifest — so navigation was correctly
        # refused, no consent prompt was drawn, and the e2e failed on the
        # platform doing the right thing. The server validates the path
        # against the routes the CLIENT sent, which is exactly the check
        # that caught this.
        return {"tool": "navigate",
                "args": {"path": NAVIGATE_TARGET,
                         "reason": "showing where these figures come from"}}
    return {"text": f"Siemens AG has {count} EU procurement contract(s) in "
                    f"the graph (entity {entity_id})."}


def _echo_step(messages: list[dict]) -> dict:
    """No tools, one deterministic sentence. For turns that only need a
    reply — checking the stream itself rather than the tool loop."""
    return {"text": "MOCK-OK: " + _last_user_text(messages)[:200]}


SCRIPTS = {
    "toolchain": _toolchain_step,
    "echo": _echo_step,
}


def next_step(messages: list[dict]) -> dict:
    """What the model does next: {"tool", "args"} or {"text"}."""
    script = SCRIPTS.get(scenario_of(messages))
    if script is None:
        return {"text": "MOCK-OK: no scenario requested; nothing to do."}
    return script(messages)


# ── OpenAI wire format ─────────────────────────────────────────


def _chunk(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def _envelope(model: str, delta: dict, finish: str | None) -> dict:
    return {
        "id": "chatcmpl-mock-" + uuid.uuid4().hex[:12],
        "object": "chat.completion.chunk",
        # A fixed clock would be tidier, but some clients reject 0.
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def stream_chunks(step: dict, model: str) -> list[str]:
    """The SSE body for one decision, in OpenAI's streaming shape."""
    if "tool" in step:
        call = {
            "index": 0,
            "id": "call_" + uuid.uuid4().hex[:16],
            "type": "function",
            "function": {"name": step["tool"],
                         "arguments": json.dumps(step.get("args") or {})},
        }
        return [
            _chunk(_envelope(model, {"role": "assistant",
                                     "tool_calls": [call]}, None)),
            _chunk(_envelope(model, {}, "tool_calls")),
            "data: [DONE]\n\n",
        ]
    text = step.get("text", "")
    # Split so the client's incremental assembly is exercised rather than
    # handed one whole string it could pass by accident.
    parts = [text[i:i + 24] for i in range(0, len(text), 24)] or [""]
    out = [_chunk(_envelope(model, {"role": "assistant", "content": ""}, None))]
    out += [_chunk(_envelope(model, {"content": p}, None)) for p in parts]
    out.append(_chunk(_envelope(model, {}, "stop")))
    out.append("data: [DONE]\n\n")
    return out


def completion(step: dict, model: str) -> dict:
    """The non-streaming shape, for clients that ask for one."""
    if "tool" in step:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_" + uuid.uuid4().hex[:16],
                "type": "function",
                "function": {"name": step["tool"],
                             "arguments": json.dumps(step.get("args") or {})},
            }],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": step.get("text", "")}
        finish = "stop"
    return {
        "id": "chatcmpl-mock-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

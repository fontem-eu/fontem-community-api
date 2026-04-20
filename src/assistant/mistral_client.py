# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals,too-many-statements,too-few-public-methods
"""Mistral chat-completions client, exposed as a ``ProxyClient``.

Drop-in replacement for ``ClaudeProxyClient``.  The service layer only
knows about the ``stream(payload) -> async iter of SSE blocks`` shape, so
swapping the underlying provider is a matter of wiring a different
implementation here.

Why this exists:
  * The Claude CLI proxy is an OAuth subprocess. It's fragile in CI — the
    OAuth token expires, the subprocess has surprising failure modes, and
    there is no API-key fallback that the DI currently selects.
  * Mistral offers an OpenAI-compatible chat-completions API keyed by a
    flat API key — no OAuth, no subprocess, no keepalive daemon.
  * Keeping the event shape identical to claude-proxy.py means the
    frontend (which parses ``event: status`` / ``chunk`` / ``usage``)
    is unaffected.

Tool set mirrors the MCP tool names the old setup exposed
(``mcp__gmr__*``) so the frontend's ``propose_edit`` proposal-rendering
code path continues to fire.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator

import httpx


# ── Tool schemas (OpenAI / Mistral function-calling format) ────────────
#
# Names keep the ``mcp__gmr__`` prefix on purpose: the frontend looks
# for that prefix when deciding whether to render a proposal card.
_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__search_entities",
            "description": (
                "Search for companies, authorities, or persons by name, ticker, "
                "or keyword. Use this first when the user mentions an entity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__get_company",
            "description": "Get full company profile by GMR UUID.",
            "parameters": {
                "type": "object",
                "properties": {"gmr_id": {"type": "string"}},
                "required": ["gmr_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__get_contracts",
            "description": "List EU procurement contracts for a company or authority.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__explore_graph",
            "description": "Traverse the knowledge graph from an entity up to N hops.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "depth": {"type": "integer", "description": "1-3"},
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__find_paths",
            "description": "Find shortest paths between two entities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                },
                "required": ["from_id", "to_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__propose_edit",
            "description": (
                "Propose an edit to the user's report. The frontend renders the "
                "proposal and asks the user to apply it — the tool itself does "
                "not mutate state. Supported actions: add_section, update_section, "
                "update_title, update_abstract, insert_widget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_section", "update_section",
                            "update_title", "update_abstract", "insert_widget",
                        ],
                    },
                    "content": {"type": "string", "description": "HTML content"},
                    "section_index": {"type": "integer"},
                    "title": {"type": "string"},
                    "abstract": {"type": "string"},
                    "widget_type": {
                        "type": "string",
                        "enum": ["graph_explorer", "contracts_table", "entity_profile"],
                    },
                    "entityId": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    },
]


_TOOL_LABELS = {
    "mcp__gmr__search_entities": "Searching entities",
    "mcp__gmr__get_company": "Looking up company",
    "mcp__gmr__get_contracts": "Fetching contracts",
    "mcp__gmr__explore_graph": "Exploring graph",
    "mcp__gmr__find_paths": "Finding connections",
    "mcp__gmr__propose_edit": "Proposing report edit",
}


# Default Mistral endpoint. Overridable for tests / self-hosted gateways.
_MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
_DEFAULT_MODEL = "mistral-small-latest"
_DEFAULT_GMR_API = "http://gmr-api.gmr.svc.cluster.local"
_MAX_TOOL_ITERATIONS = 5


def _sse(event: str, data: dict) -> str:
    """Serialize an SSE event block (one per ``yield``)."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _tool_detail(name: str, args: dict) -> str:
    label = _TOOL_LABELS.get(name, name)
    hint = args.get("query") or args.get("gmr_id") or args.get("entity_id") or ""
    return f'{label}: "{hint}"' if hint else label


class MistralProxyClient:
    """Mistral chat-completions with a bounded tool-use loop.

    Emits exactly the same SSE event vocabulary as the Claude CLI proxy
    (``status`` / ``chunk`` / ``usage`` / ``error`` / ``done``) so the
    frontend assistant panel consumes it unchanged.

    Tool calls are dispatched to the GMR REST API; ``propose_edit`` is a
    pure notification — the frontend executes the actual report mutation
    with the user's auth when the user clicks "Apply".
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        api_url: str = _MISTRAL_URL,
        gmr_api_url: str = _DEFAULT_GMR_API,
        max_iterations: int = _MAX_TOOL_ITERATIONS,
        timeout: float = 120.0,
        client_factory=None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._api_url = api_url
        self._gmr_api_url = gmr_api_url.rstrip("/")
        self._max_iter = max_iterations
        self._timeout = timeout
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=timeout)
        )

    async def stream(self, payload: dict) -> AsyncIterator[str]:  # NOSONAR S3776: provider-loop
        """Execute a chat turn and yield SSE event blocks."""
        start = time.time()
        system = payload.get("system", "")
        message = payload.get("message", "")

        if not message:
            yield _sse("error", {"error": "Missing message"})
            return
        if not self._api_key:
            yield _sse("error", {"error": "MISTRAL_API_KEY not configured"})
            return

        yield _sse("status", {
            "phase": "connecting",
            "detail": "Starting assistant...",
            "elapsed": 0,
        })

        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ]

        input_tokens = 0
        output_tokens = 0

        try:
            async with self._client_factory() as client:
                for _ in range(self._max_iter):
                    yield _sse("status", {
                        "phase": "thinking",
                        "detail": "Processing your request...",
                        "elapsed": round(time.time() - start, 1),
                    })

                    resp = await client.post(
                        self._api_url,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self._model,
                            "messages": messages,
                            "tools": _TOOLS,
                            "tool_choice": "auto",
                        },
                    )
                    if resp.status_code != 200:
                        yield _sse("error", {
                            "error": f"Mistral API {resp.status_code}: {resp.text[:200]}",
                        })
                        return

                    data = resp.json()
                    usage = data.get("usage") or {}
                    input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                    output_tokens += int(usage.get("completion_tokens", 0) or 0)

                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    finish = choice.get("finish_reason", "stop")
                    content = msg.get("content") or ""
                    tool_calls = msg.get("tool_calls") or []

                    # Echo the assistant turn into history for the next round.
                    assistant_entry: dict = {
                        "role": "assistant",
                        "content": content,
                    }
                    if tool_calls:
                        assistant_entry["tool_calls"] = tool_calls
                    messages.append(assistant_entry)

                    if finish != "tool_calls" or not tool_calls:
                        if content:
                            yield _sse("status", {
                                "phase": "streaming",
                                "detail": "Writing response...",
                                "elapsed": round(time.time() - start, 1),
                            })
                            yield _sse("chunk", {"text": content})
                        yield _sse("usage", {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        })
                        return

                    for tc in tool_calls:
                        func = tc.get("function") or {}
                        name = func.get("name", "")
                        try:
                            args = json.loads(func.get("arguments") or "{}")
                        except (ValueError, TypeError):
                            args = {}

                        status: dict = {
                            "phase": "tool_use",
                            "tool": name,
                            "detail": _tool_detail(name, args),
                            "elapsed": round(time.time() - start, 1),
                        }
                        # Forward propose_edit args so the frontend renders the card.
                        if name == "mcp__gmr__propose_edit":
                            status["proposal"] = args
                        yield _sse("status", status)

                        result = await self._execute_tool(client, name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id") or "",
                            "name": name,
                            "content": result,
                        })

                yield _sse("error", {
                    "error": f"Max tool iterations ({self._max_iter}) reached",
                })
                yield _sse("usage", {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                })
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            yield _sse("error", {"error": str(exc)[:200]})

    async def _execute_tool(
        self, client: httpx.AsyncClient, name: str, args: dict,
    ) -> str:
        """Dispatch a tool call to the GMR API and return its body as text."""
        try:
            if name == "mcp__gmr__search_entities":
                r = await client.get(
                    f"{self._gmr_api_url}/search",
                    params={"q": args.get("query", ""), "limit": args.get("limit", 5)},
                )
            elif name == "mcp__gmr__get_company":
                r = await client.get(
                    f"{self._gmr_api_url}/companies/{args.get('gmr_id', '')}",
                )
            elif name == "mcp__gmr__get_contracts":
                eid = args.get("entity_id", "")
                limit = args.get("limit", 20)
                r = await client.get(
                    f"{self._gmr_api_url}/companies/{eid}/contracts",
                    params={"limit": limit},
                )
                if r.status_code == 404:
                    r = await client.get(
                        f"{self._gmr_api_url}/authorities/{eid}/contracts",
                        params={"limit": limit},
                    )
            elif name == "mcp__gmr__explore_graph":
                r = await client.get(
                    f"{self._gmr_api_url}/graph/{args.get('entity_id', '')}",
                    params={"depth": args.get("depth", 1)},
                )
            elif name == "mcp__gmr__find_paths":
                r = await client.get(
                    f"{self._gmr_api_url}/graph/paths/find",
                    params={
                        "from": args.get("from_id", ""),
                        "to": args.get("to_id", ""),
                    },
                )
            elif name == "mcp__gmr__propose_edit":
                # Pure notification; the frontend applies the edit with user auth.
                return json.dumps({"proposed": True, "action": args.get("action")})
            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

            if r.status_code >= 400:
                return json.dumps({"error": f"API {r.status_code}"})
            return r.text
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            return json.dumps({"error": str(exc)[:200]})


def from_env() -> "MistralProxyClient":
    """Build a client from the standard env vars."""
    return MistralProxyClient(
        api_key=os.environ.get("MISTRAL_API_KEY", ""),
        model=os.environ.get("MISTRAL_MODEL", _DEFAULT_MODEL),
        api_url=os.environ.get("MISTRAL_API_URL", _MISTRAL_URL),
        gmr_api_url=os.environ.get("GMR_API_INTERNAL", _DEFAULT_GMR_API),
    )

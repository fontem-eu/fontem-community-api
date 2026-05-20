"""LLM service — proxies chat requests to Claude via subprocess or API."""
from __future__ import annotations

import json
import os

import httpx

# Tool definitions for Claude — maps to GMR REST API endpoints
TOOLS = [
    {
        "name": "search_entities",
        "description": "Search for companies, authorities, or persons by name or keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (name, ticker, keyword)"},
                "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company",
        "description": "Get full company profile including contracts, directors, and corporate group.",
        "input_schema": {
            "type": "object",
            "properties": {"gmr_id": {"type": "string", "description": "Company GMR UUID"}},
            "required": ["gmr_id"],
        },
    },
    {
        "name": "get_contracts",
        "description": "Get procurement contracts for a company or authority.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Company or Authority ID"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "explore_graph",
        "description": "Traverse the entity relationship graph from a starting node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Starting entity UUID"},
                "depth": {"type": "integer", "description": "Traversal depth (1-3)", "default": 1},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "find_paths",
        "description": "Find connections (shortest paths) between two entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_id": {"type": "string", "description": "Source entity UUID"},
                "to_id": {"type": "string", "description": "Target entity UUID"},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "suggest_visualization",
        "description": (
            "Suggest a visualization to embed in the report based on the "
            "conversation context. Returns a pocket-ready config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "widget_type": {
                    "type": "string",
                    "enum": ["graph_explorer", "contracts_table", "entity_profile"],
                    "description": "Type of visualization",
                },
                "entity_id": {"type": "string", "description": "Entity to visualize"},
                "caption": {"type": "string", "description": "Caption for the visualization"},
            },
            "required": ["widget_type", "entity_id"],
        },
    },
]

GMR_API_URL = os.environ.get(
    "GMR_API_INTERNAL", "http://gmr-api.gmr.svc.cluster.local"
)

SYSTEM_PROMPT = """You are a research assistant embedded in the GMR Knowledge Graph platform.
You help journalists, researchers, and citizens investigate connections between companies,
public authorities, and persons in the context of EU public procurement and corporate transparency.

You have access to tools that query the GMR graph database (3M+ companies, 700K+ contracts).
Use them to ground your answers in real data. Always cite specific entities and values.

When the user asks about an entity, search for it first, then explore its connections.
When suggesting visualizations, use the suggest_visualization tool to create embeddable widgets.

Keep responses concise and factual. Use bullet points for lists.
If data is unavailable, say so clearly — never hallucinate numbers."""


async def _execute_tool(name: str, args: dict) -> str:
    """Execute a tool call against the GMR API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        if name == "search_entities":
            r = await client.get(
                f"{GMR_API_URL}/search",
                params={"q": args["query"], "limit": args.get("limit", 5)},
            )
        elif name == "get_company":
            r = await client.get(f"{GMR_API_URL}/companies/{args['gmr_id']}")
        elif name == "get_contracts":
            eid = args["entity_id"]
            r = await client.get(
                f"{GMR_API_URL}/companies/{eid}/contracts",
                params={"limit": args.get("limit", 20)},
            )
            if r.status_code == 404:
                r = await client.get(
                    f"{GMR_API_URL}/authorities/{eid}/contracts",
                    params={"limit": args.get("limit", 20)},
                )
        elif name == "explore_graph":
            r = await client.get(
                f"{GMR_API_URL}/graph/{args['entity_id']}",
                params={"depth": args.get("depth", 1)},
            )
        elif name == "find_paths":
            r = await client.get(
                f"{GMR_API_URL}/graph/paths/find",
                params={"from": args["from_id"], "to": args["to_id"]},
            )
        elif name == "suggest_visualization":
            # This doesn't call an API — it returns a widget config
            return json.dumps({
                "widget_type": args["widget_type"],
                "entity_id": args["entity_id"],
                "caption": args.get("caption", ""),
                "embeddable": True,
            })
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

        if r.status_code >= 400:
            return json.dumps({"error": f"API returned {r.status_code}"})
        return r.text


CLAUDE_PROXY_URL = os.environ.get(
    "CLAUDE_PROXY_URL", "http://claude-proxy.devspaces.svc.cluster.local:8090"
)


class LLMService:
    """Handles LLM chat via Claude CLI proxy or Anthropic API fallback."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self._proxy_url = CLAUDE_PROXY_URL

    async def _chat_via_proxy(
        self, user_message: str, system: str,
    ) -> dict | None:
        """Call the Claude CLI proxy (flat subscription cost).

        The proxy runs Claude CLI with MCP tools configured, so Claude
        can directly call search_entities, get_company, etc.
        We just pass the user's message and system prompt.
        """
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._proxy_url}/chat",
                    json={"message": user_message, "system": system},
                )
                if resp.status_code != 200:
                    return None  # Fall back to API
                data = resp.json()
                if data.get("error"):
                    return None
                return {
                    "content": data.get("content", ""),
                    "tool_calls_made": 0,
                    "suggestions": [],
                    "messages": [],
                }
        except (httpx.ConnectError, httpx.TimeoutException):
            return None  # Proxy unreachable — fall back to API

    # NOSONAR S3776: tool-use loop with fallback branches
    # pylint: disable-next=too-many-locals
    async def chat(
        self,
        user_message: str,
        history: list[dict] | None = None,
        report_context: str | None = None,
    ) -> dict:
        """Send a message and get a response, potentially with tool calls."""
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})

        system = SYSTEM_PROMPT
        if report_context:
            system += f"\n\nCurrent report context:\n{report_context}"

        # Try Claude CLI proxy first (flat subscription)
        result = await self._chat_via_proxy(user_message, system)
        if result is not None:
            result["messages"] = messages
            return result

        # Fallback: Anthropic API
        if not self._api_key:
            return {
                "content": "LLM assistant is temporarily unavailable. Please try again later.",
                "tool_calls_made": 0,
                "suggestions": [],
                "messages": messages,
            }

        # Call Anthropic Messages API with tool use
        tools_for_api = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in TOOLS
        ]

        async with httpx.AsyncClient(timeout=120.0) as client:
            tool_calls_made = 0
            suggestions = []

            # Tool use loop (max 5 iterations)
            for _ in range(5):
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "max_tokens": 4096,
                        "system": system,
                        "messages": messages,
                        "tools": tools_for_api,
                    },
                )

                if resp.status_code != 200:
                    return {
                        "content": f"LLM API error: {resp.status_code}",
                        "tool_calls_made": tool_calls_made,
                        "suggestions": suggestions,
                        "messages": messages,
                    }

                result = resp.json()
                stop_reason = result.get("stop_reason", "end_turn")

                # Collect text and tool_use blocks
                assistant_content = result.get("content", [])
                messages.append({"role": "assistant", "content": assistant_content})

                if stop_reason != "tool_use":
                    # Final response — extract text
                    text_parts = [
                        b["text"] for b in assistant_content if b.get("type") == "text"
                    ]
                    return {
                        "content": "\n".join(text_parts),
                        "tool_calls_made": tool_calls_made,
                        "suggestions": suggestions,
                        "messages": messages,
                    }

                # Execute tool calls
                tool_results = []
                for block in assistant_content:
                    if block.get("type") == "tool_use":
                        tool_calls_made += 1
                        tool_result = await _execute_tool(
                            block["name"], block["input"]
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": tool_result,
                        })
                        # Track suggestions
                        if block["name"] == "suggest_visualization":
                            suggestions.append(json.loads(tool_result))

                messages.append({"role": "user", "content": tool_results})

            # Max iterations reached
            return {
                "content": (
                    "I've made several tool calls but couldn't fully resolve the "
                    "query. Please try a more specific question."
                ),
                "tool_calls_made": tool_calls_made,
                "suggestions": suggestions,
                "messages": messages,
            }

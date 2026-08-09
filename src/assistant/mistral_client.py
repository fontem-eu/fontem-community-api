# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals,too-many-statements,too-few-public-methods
# pylint: disable=too-many-instance-attributes,too-many-branches
"""Mistral chat-completions client, exposed as a ``ProxyClient``.

Drop-in replacement for ``ClaudeProxyClient``.  The service layer only
knows about the ``stream(payload) -> async iter of SSE blocks`` shape, so
swapping the underlying provider is a matter of wiring a different
implementation here.

Tool surface (the assistant revamp landed here):
  * ``investigate_entity`` is the canonical entity-detail tool — one call
    returns label + props + contracts + graph neighbourhood. Replaces the
    earlier four narrow getters which mistral occasionally picked the
    wrong one of.
  * ``search_entities``, ``find_paths``, ``propose_edit`` remain.
  * The legacy four narrow getters (``get_company`` / ``get_authority`` /
    ``get_contracts`` / ``explore_graph``) stay implemented in
    :py:meth:`_execute_tool` so old saved conversations keep working,
    but they are NOT advertised in :data:`_TOOLS` — the model only sees
    the canonical surface.

Key behaviours:
  * The system prompt is augmented every turn with the **current date**
    so the model can reason about whether dates returned by tools are
    past / present / future.
  * Tool-call dedup: within one turn, identical ``(name, args)`` calls
    short-circuit to the cached result, so a model that asks the same
    thing twice doesn't pay (in latency or tokens) for it.
  * Per-turn entity-name cache: when a tool surfaces an id → name, that
    mapping is used to substitute human-readable labels into the
    ``status`` events the frontend renders.
  * On loop exhaustion, emit a ``status`` event with ``phase=truncated``
    (not ``error``) so the frontend can render a "ran out of budget"
    notice instead of a hard failure.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from src.assistant.catalogue import CatalogueCache

from src.assistant import local_models, navigation
from src.assistant.entities import (
    _build_summary,
    _capture_names,
    entity_name,
)


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
                "Search for companies, authorities, persons, or lobbyists "
                "by name, ticker, or keyword. Use this first when the user "
                "mentions an entity. Returns up to `limit` matches across "
                "all entity types — pick one and call investigate_entity "
                "to drill in. The graph holds duplicates: country "
                "subsidiaries, spellings, punctuation. For counts and "
                "totals, search the variants and iterate every hit rather "
                "than reporting the first."
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
            "name": "mcp__gmr__investigate_entity",
            "description": (
                "Get the full investigation packet for an entity: its props, "
                "its EU procurement contracts, and its graph neighbourhood. "
                "Works for Companies, Authorities, Lobbyists, and Persons — "
                "the tool dispatches by label internally so the caller does "
                "NOT need to know which type the id belongs to. Use this "
                "after `search_entities`. The response carries a `summary` "
                "field with a short prose précis suitable for direct quoting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "GMR UUID returned by search_entities",
                    },
                    "depth": {
                        "type": "integer",
                        "description": (
                            "Graph hops (1-3). Default 1 — usually plenty. "
                            "Bump only when the user explicitly asks about "
                            "second-degree connections."
                        ),
                    },
                    "contract_limit": {
                        "type": "integer",
                        "description": "Max contracts to include (default 20).",
                    },
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp__gmr__find_paths",
            "description": (
                "Find shortest paths between two entities. Use it to "
                "surface the intermediary — the shared owner, common "
                "supplier or lobbyist sitting between two parties who "
                "appear unconnected."
            ),
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
                "Propose an edit to the user's report. The frontend renders "
                "the proposal as an Apply/Reject card — the tool itself "
                "does NOT mutate state. Supported actions: insert_content "
                "(append HTML to the document), insert_widget (insert an "
                "interactive widget node), update_title, update_abstract. "
                "Call it once the analysis is done and you have specific "
                "prose to add or replace, not to think out loud. "
                "Multiple proposals sharing a `group_id` render as a "
                "single grouped card the user can accept/reject as a "
                "unit (use this when emitting a coherent set of edits "
                "for one logical report unit)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        # Pinned in PROPOSE_EDIT_ACTIONS below so the
                        # parity test in tests/unit/assistant/
                        # test_propose_edit_schema.py can cross-check
                        # against the JS-side ASSISTANT_ADVERTISED_ACTIONS.
                        # If you add or remove an action here, update
                        # both PROPOSE_EDIT_ACTIONS *and* useEditProposals.js.
                        "enum": [
                            "insert_content",
                            "insert_widget",
                            "update_title",
                            "update_abstract",
                        ],
                    },
                    "content": {"type": "string", "description": "HTML content"},
                    "title": {"type": "string"},
                    "abstract": {"type": "string"},
                    "widget_type": {
                        "type": "string",
                        "enum": ["graph_explorer", "contracts_table", "entity_profile"],
                    },
                    "entityId": {"type": "string"},
                    "depth": {"type": "integer"},
                    "group_id": {
                        "type": "string",
                        "description": (
                            "Optional. Proposals sharing the same group_id "
                            "render as a single review card."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
]

# Canonical action enum for the propose_edit tool. Pinned for the
# schema-parity test (Python ↔ JS) and exposed so callers don't have
# to walk the _TOOLS structure to find it.
PROPOSE_EDIT_ACTIONS = (
    "insert_content",
    "insert_widget",
    "update_title",
    "update_abstract",
)
# Legacy actions accepted from old chat history but no longer
# advertised to the model. The frontend keeps them as aliases for
# `insert_content` (see useEditProposals.js).
PROPOSE_EDIT_LEGACY_ACTIONS = ("add_section", "update_section")


_TOOL_LABELS = {
    "mcp__gmr__search_entities": "Searching entities",
    "mcp__gmr__investigate_entity": "Investigating",
    "mcp__gmr__find_paths": "Finding connections",
    "mcp__gmr__propose_edit": "Proposing report edit",
    # Legacy tools — still implemented for old conversations, but no
    # longer advertised in _TOOLS.
    "mcp__gmr__get_company": "Looking up company",
    "mcp__gmr__get_authority": "Looking up authority",
    "mcp__gmr__get_contracts": "Fetching contracts",
    "mcp__gmr__explore_graph": "Exploring graph",
}


# Default Mistral endpoint. Overridable for tests / self-hosted gateways.
_MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
_DEFAULT_MODEL = "mistral-small-latest"

#: The cluster-local llama.cpp server (Qwen3-4B). Shared by every
#: environment — see gitops/infra/llm-service.yaml. Empty when unset, in
#: which case the assistant needs a user-supplied key as before.
_LOCAL_URL = ""
_LOCAL_MODEL = "qwen3-4b"
#: Provider id for the built-in model. Deliberately not a vendor name:
#: which weights we host is an operational detail, and users should not
#: have to re-pick a provider when it changes.
LOCAL_PROVIDER = "local"
_DEFAULT_GMR_API = "http://fontem-api"
# Bumped 5 → 10. Five is too tight for "investigate this multi-subsidiary
# corporate group" prompts; ten is enough for most real questions and is
# still capped well below the model's own context budget.
_MAX_TOOL_ITERATIONS = 10

#: How many times a single turn may be pushed onward after the model
#: stops mid-investigation. Measured on Qwen3-4B: handed search results
#: and asked to continue, it chains to investigate_entity of its own
#: accord 5 times in 21. With tool_choice="required" it does so 18 times
#: in 21. Prompting did not move the unforced number at all.
_MAX_FORCED_CONTINUATIONS = 2

#: Tools that only yield names and ids. Stopping straight after one of
#: these means the model has nothing concrete to cite yet.
_SHALLOW_TOOLS = frozenset({"mcp__gmr__search_entities"})


def _tool_names() -> frozenset[str]:
    """Names of every registered tool, for spotting announced-but-unmade
    calls. Derived rather than hardcoded so a new tool cannot silently
    fall outside the check."""
    out = {t["function"]["name"] for t in _TOOLS if "function" in t}
    out.add(navigation.NAVIGATE_TOOL_NAME)
    return frozenset(out)


_TOOL_NAMES = _tool_names()
# Cap how many proposals one chat turn can emit before we tell the user
# "I proposed N edits — review them in order". Stays out of the way for
# small reports, surfaces a checklist for big ones.
_PROPOSAL_BUDGET_DISCLOSE = 8
# How long to cache the per-source freshness summary in memory. The
# ETL loaders update :DataSource markers at most once a day for the
# most-frequent sources; a 5-minute cache keeps the assistant from
# hammering /data-quality/source-freshness on every chat turn while
# still picking up new loader runs within the same session.
_FRESHNESS_TTL_SECONDS = 300
# Hard timeout on the freshness fetch — this call sits on the user's
# critical path (we wait for it before sending the first model
# request), so we'd rather show "freshness: unavailable" than make
# the user stare at a spinner because the data-quality endpoint is
# slow.
_FRESHNESS_FETCH_TIMEOUT = 5.0


def _turn_tools(nav_routes: list, has_editor: bool) -> list[dict]:
    """The tool surface for one turn, scoped to the user's context."""
    tools = navigation.scope_tools(_TOOLS, has_editor=has_editor)
    if nav_routes:
        tools = tools + [navigation.navigate_tool_schema()]
    return tools


def _stalled_mid_chain(tools_used: set[str], content: str) -> bool:
    """True when the model stopped before it had anything to cite.

    Two shapes, both seen in production:

      * it ran a search, which returns names and ids and nothing else,
        and then wrote a summary of those names as if it were an answer;
      * it announced the call it was about to make — "vou usar a função
        mcp__gmr__search_entities" — and then emitted no call at all.

    The second is detected by looking for a registered tool name in the
    prose. That is deliberately a substring match on the identifiers
    rather than a phrase match: the announcement arrives in whatever
    language the user is speaking, and the tool names do not translate.
    """
    if any(name in content for name in _TOOL_NAMES):
        return True
    return bool(tools_used) and tools_used <= _SHALLOW_TOOLS


def _sse(event: str, data: dict) -> str:
    """Serialize an SSE event block (one per ``yield``)."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _tool_detail(name: str, args: dict, name_cache: dict[str, str]) -> str:
    """Build a human-readable status detail for a tool invocation.

    Substitutes UUIDs for entity names when the per-turn `name_cache`
    has them, so the user sees "Investigating: Metro Mondego, S. A."
    instead of a UUID."""
    label = _TOOL_LABELS.get(name, name)
    raw = (
        args.get("query")
        or args.get("entity_id")
        or args.get("gmr_id")
        or args.get("from_id")
        or ""
    )
    if not raw:
        return label
    pretty = name_cache.get(raw, raw)
    return f'{label}: "{pretty}"'


def _system_prompt_with_today(base: str) -> str:
    """Append a single line giving the model today's date so it can
    reason about whether a tool-returned date is past or future."""
    today = datetime.now(timezone.utc).date().isoformat()
    if not base:
        return f"Today's date is {today}."
    return f"{base.rstrip()}\n\nToday's date is {today}."


def _format_coverage(cov_start: str | None, cov_end: str | None) -> str:
    """Render a coverage window into the bullet's middle column."""
    if cov_start and cov_end:
        return f"{cov_start} → {cov_end}"
    if cov_end:
        return f"through {cov_end}"
    return "no date range"


def _format_freshness(age_h: float | None, stale: bool) -> str:
    """Render an age-in-hours into a compact "loaded N <unit> ago" hint."""
    if age_h is None:
        base = "freshness unknown"
    elif age_h < 48:
        base = f"loaded {age_h:.1f}h ago"
    elif age_h < 24 * 60:
        base = f"loaded {age_h / 24:.0f}d ago"
    else:
        base = f"loaded {age_h / (24 * 7):.0f}w ago"
    return base + ", STALE" if stale else base


def _format_freshness_summary(sources: list[dict]) -> str:
    """Compress a /data-quality/source-freshness response into a short
    block the model can quote when reasoning about coverage.

    Emits one bulleted line per source — coverage range when available,
    a freshness note when stale — in deterministic alphabetical order.
    Returns ``""`` when the input is empty (callers skip injection in
    that case so the system prompt doesn't get a half-empty section).
    """
    if not sources:
        return ""
    lines: list[str] = []
    for src in sorted(sources, key=lambda s: s.get("id") or ""):
        sid = src.get("id") or ""
        label = src.get("label") or sid or "unknown"
        rows = src.get("record_count") or 0
        coverage = _format_coverage(src.get("coverage_start"), src.get("coverage_end"))
        freshness = _format_freshness(src.get("age_hours"), bool(src.get("stale")))
        lines.append(f"- {label} ({sid}): {coverage}, {rows:,} rows, {freshness}")
    header = (
        "Data coverage at the time of this turn (cite these ranges when "
        "the user asks about scope; flag STALE sources to the user):"
    )
    return header + "\n" + "\n".join(lines)


class MistralProxyClient:
    """Mistral chat-completions with a bounded, deduped tool-use loop.

    Emits SSE events: ``status`` / ``chunk`` / ``usage`` / ``error`` /
    ``done`` (the last is appended by the router). Frontend consumes
    this unchanged.

    See the module docstring for the full revamp summary.
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
        local_url: str = _LOCAL_URL,
        local_model: str = _LOCAL_MODEL,
        local_timeout: float = 300.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._api_url = api_url
        self._local_url = local_url.rstrip("/")
        self._local_model = local_model
        # Generation on CPU runs at single-digit tokens/sec, so a long
        # answer legitimately takes minutes. The hosted-provider timeout
        # would abort a turn that was working fine.
        self._local_timeout = local_timeout
        self._gmr_api_url = gmr_api_url.rstrip("/")
        self._max_iter = max_iterations
        self._timeout = timeout
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=timeout)
        )
        # In-memory freshness cache: (cached_at_unix, summary_str).
        # Keyed nowhere — one client instance only ever talks to one
        # fontem-api, and the cache lives on the instance.
        self._freshness_cache: tuple[float, str] | None = None
        # What the platform holds, generated from its own registries. Same
        # best-effort contract as the coverage block above.
        self._catalogue = CatalogueCache()

    async def _get_catalogue_block(self, client: httpx.AsyncClient) -> str:
        """What data exists, for the system prompt."""
        return await self._catalogue.get(client, self._gmr_api_url)

    async def _get_freshness_summary(self, client: httpx.AsyncClient) -> str:
        """Return the formatted source-freshness block for system prompt
        injection, fetching from the data-quality API or returning the
        cached value when warm.

        Best-effort: a fetch failure logs nothing back to the user and
        returns ``""`` so the model just doesn't get a coverage block
        for this turn. Better to ship a useful answer with one less
        sentence than to fail because monitoring metadata wasn't
        available.
        """
        now = time.monotonic()
        if (
            self._freshness_cache is not None
            and now - self._freshness_cache[0] < _FRESHNESS_TTL_SECONDS
        ):
            return self._freshness_cache[1]
        try:
            resp = await client.get(
                f"{self._gmr_api_url}/data-quality/freshness",
                timeout=_FRESHNESS_FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            summary = _format_freshness_summary(payload.get("sources") or [])
        except (httpx.HTTPError, ValueError):
            summary = ""
        self._freshness_cache = (now, summary)
        return summary

    def _route_for(
        self, cred: dict, local_model_id: str | None = None
    ) -> tuple[str, str, str, float] | None:
        """Pick the endpoint for this turn: (url, api_key, model, timeout).

        Three cases, in order:

          1. The user picked a hosted provider and supplied a key — spend
             theirs.
          2. The user picked the built-in model, or picked nothing at all
             — use the cluster-local server. No key, nobody billed.
          3. Neither is available — return None and the caller explains
             itself.

        Case 2 is why this exists. The assistant used to be unusable
        until you pasted an API key, which meant almost nobody used it.

        Returns None rather than raising so the caller can emit a legible
        SSE error instead of a stack trace mid-stream.
        """
        provider = (cred.get("provider") or "").strip().lower()
        key = cred.get("api_key") or ""

        if provider and provider != LOCAL_PROVIDER and key:
            return (
                self._api_url,
                key,
                cred.get("model") or self._model,
                self._timeout,
            )
        if self._local_url:
            # The caller picks from a curated list of ids, never a model
            # name. local_models.resolve maps the id to what llama-server
            # calls it and falls back to the default for anything else, so
            # an arbitrary string cannot reach the server.
            return (
                f"{self._local_url}/v1/chat/completions",
                "",
                local_models.resolve(local_model_id).served_name,
                self._local_timeout,
            )
        if key:
            return (
                self._api_url,
                key,
                cred.get("model") or self._model,
                self._timeout,
            )
        if self._api_key:
            return self._api_url, self._api_key, self._model, self._timeout
        return None

    async def stream(self, payload: dict) -> AsyncIterator[str]:  # NOSONAR S3776: provider-loop
        """Execute a chat turn and yield SSE event blocks."""
        start = time.time()
        system = _system_prompt_with_today(payload.get("system", ""))
        # Where the user is, and what pages exist. The manifest is generated
        # by the frontend from its own router and sent with the turn, so it
        # cannot disagree with what this build of the app actually serves.
        nav = payload.get("nav") or {}
        nav_routes = nav.get("routes") or []
        # An editing surface is registered when the caller sent a report
        # context to work on. Drives which tools the model is offered.
        has_editor = bool(payload.get("has_editor"))
        message = payload.get("message", "")

        if not message:
            yield _sse("error", {"error": "Missing message"})
            return
        # The caller's own key wins over the platform key. Read per turn
        # and never stored on self: this client is an APP-scoped singleton
        # shared across requests, so keeping a key on the instance would
        # spend one user's credential on another user's turn.
        cred = payload.get("credential") or {}
        route = self._route_for(cred, payload.get("local_model_id"))
        if route is None:
            yield _sse("error", {
                "error": (
                    "No LLM provider configured. Add your own API key in "
                    "Account settings to use the assistant."
                ),
                "code": "no_credential",
            })
            return
        api_url, api_key, model, timeout = route

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
        # Per-turn caches. Keys live for ONE chat turn only (ie. one
        # call to .stream()), so ids that update mid-conversation are
        # never staler than one user turn.
        tool_cache: dict[str, str] = {}
        name_cache: dict[str, str] = {}
        proposal_count = 0
        # Tools invoked so far this turn, and how many times we have made
        # the model carry on after it tried to stop early.
        tools_used: set[str] = set()
        forced_continuations = 0
        force_tool_choice = False

        try:
            async with self._client_factory() as client:
                # Fetch coverage summary once per turn (cached across
                # turns for `_FRESHNESS_TTL_SECONDS`). Inject AFTER the
                # date line so the model sees them as a single
                # "context-as-of-now" block. Empty string when the
                # data-quality API is unreachable — the chat still
                # works, just without coverage grounding.
                # The catalogue goes first: knowing the data EXISTS is what
                # stops the model answering "we don't have that" about eight
                # population datasets. Coverage ranges refine an answer the
                # model is already willing to attempt.
                catalogue = await self._get_catalogue_block(client)
                freshness = await self._get_freshness_summary(client)
                for block in (catalogue, freshness):
                    if block:
                        messages[0]["content"] = (
                            messages[0]["content"].rstrip() + "\n\n" + block
                        )
                completed_normally = False
                for _iter_no in range(self._max_iter):
                    yield _sse("status", {
                        "phase": "thinking",
                        "detail": "Processing your request...",
                        "elapsed": round(time.time() - start, 1),
                    })

                    headers = {"Content-Type": "application/json"}
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    resp = await client.post(
                        api_url,
                        headers=headers,
                        timeout=timeout,
                        json={
                            "model": model,
                            "messages": messages,
                            # navigate is offered only when the client sent a
                            # site map. Advertising a tool whose every call we
                            # would have to reject teaches the model to
                            # distrust its own tools.
                            # Scoped to where the user actually is: no
                            # propose_edit without an editor, no navigate
                            # without a site map.
                            "tools": _turn_tools(nav_routes, has_editor),
                            # "required" only on a forced continuation.
                            # Left on permanently it would keep calling
                            # tools when there is genuinely nothing left
                            # to look up, and never answer.
                            "tool_choice": (
                                "required" if force_tool_choice else "auto"
                            ),
                        },
                    )
                    if resp.status_code != 200:
                        yield _sse("error", {
                            "error": f"Mistral API {resp.status_code}: {resp.text[:200]}",
                        })
                        return

                    # One-shot: this request carried it, the next must
                    # decide for itself whether another tool is needed.
                    force_tool_choice = False

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

                    # Models narrate what they are about to do before doing
                    # it. That commentary used to be appended to history and
                    # never shown, so a turn that took a minute looked like
                    # a blank screen. Emit it as it happens — it is a
                    # separate event from `chunk` so the frontend can render
                    # it as working-out rather than as the answer.
                    if tool_calls and content:
                        yield _sse("thinking", {"text": content})

                    if finish != "tool_calls" or not tool_calls:
                        # The model wants to stop. Sometimes it has not
                        # done the work yet: it searches, gets a list of
                        # names, and writes a summary of the names — or
                        # worse, narrates the call it is about to make and
                        # then makes nothing. Unforced it carries on 5
                        # times in 21; asked to, 18 in 21.
                        if (
                            forced_continuations < _MAX_FORCED_CONTINUATIONS
                            and _stalled_mid_chain(tools_used, content)
                        ):
                            forced_continuations += 1
                            # Show the aborted reasoning rather than
                            # discarding it — it is the most legible part
                            # of what the assistant is doing.
                            if content:
                                yield _sse("thinking", {"text": content})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "You have not finished. Call the next "
                                    "tool now — investigate the entities "
                                    "you named. Do not describe the call "
                                    "and do not summarise yet."
                                ),
                            })
                            force_tool_choice = True
                            continue
                        if content:
                            # Append a proposal-budget disclosure when the
                            # model emitted many proposals so the user knows
                            # to review them in order.
                            if proposal_count > _PROPOSAL_BUDGET_DISCLOSE:
                                content += (
                                    f"\n\n_(I proposed {proposal_count} edits — "
                                    "review them in order in your editor.)_"
                                )
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
                        completed_normally = True
                        return

                    for tc in tool_calls:
                        func = tc.get("function") or {}
                        if func.get("name"):
                            tools_used.add(func["name"])
                        name = func.get("name", "")
                        try:
                            args = json.loads(func.get("arguments") or "{}")
                        except (ValueError, TypeError):
                            args = {}

                        if name == "mcp__gmr__propose_edit":
                            proposal_count += 1

                        status: dict = {
                            "phase": "tool_use",
                            "tool": name,
                            "detail": _tool_detail(name, args, name_cache),
                            "elapsed": round(time.time() - start, 1),
                        }
                        # Forward propose_edit args so the frontend renders the card.
                        if name == "mcp__gmr__propose_edit":
                            status["proposal"] = args
                        yield _sse("status", status)

                        # Per-turn tool-call dedup. Identical (name, args)
                        # calls return the cached result instead of paying
                        # the round-trip again.
                        cache_key = name + "|" + json.dumps(args, sort_keys=True)
                        if name == navigation.NAVIGATE_TOOL_NAME:
                            # Runs in the browser, not here: emit the
                            # instruction and tell the model it landed.
                            # Deliberately NOT cached — asking to go
                            # somewhere twice in a turn should move the user
                            # twice, and a cached "ok" would strand them on
                            # the first page.
                            result, emit = navigation.navigate_result(
                                args.get("path", ""), nav_routes,
                            )
                            if emit:
                                yield _sse("navigate", emit)
                        elif cache_key in tool_cache:
                            result = tool_cache[cache_key]
                        else:
                            result = await self._execute_tool(client, name, args)
                            tool_cache[cache_key] = result
                            # Keep our id→name mapping fresh from every result.
                            try:
                                _capture_names(name_cache, json.loads(result))
                            except (ValueError, TypeError):
                                pass

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id") or "",
                            "name": name,
                            "content": result,
                        })

                # Loop exhausted without a final response. Emit a
                # `truncated` status (not error) so the frontend can render
                # a "ran out of budget" notice and offer to retry.
                if not completed_normally:
                    yield _sse("status", {
                        "phase": "truncated",
                        "detail": (
                            f"Reached max tool iterations ({self._max_iter}). "
                            "Try a more focused question."
                        ),
                        "elapsed": round(time.time() - start, 1),
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
            elif name == "mcp__gmr__investigate_entity":
                # New canonical getter — composes profile + contracts +
                # graph in one response, dispatching by label internally.
                return await self._investigate(
                    client, args.get("entity_id", ""),
                    depth=int(args.get("depth", 1) or 1),
                    contract_limit=int(args.get("contract_limit", 20) or 20),
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
            # Legacy tools — kept callable so old saved conversations still
            # function. They are NOT advertised in `_TOOLS`.
            elif name == "mcp__gmr__get_company":
                r = await client.get(
                    f"{self._gmr_api_url}/companies/{args.get('gmr_id', '')}",
                )
            elif name == "mcp__gmr__get_authority":
                r = await client.get(
                    f"{self._gmr_api_url}/authorities/{args.get('authority_id', '')}",
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
            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

            if r.status_code >= 400:
                return json.dumps({"error": f"API {r.status_code}"})
            return r.text
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            return json.dumps({"error": str(exc)[:200]})

    async def _investigate(
        self,
        client: httpx.AsyncClient,
        entity_id: str,
        *,
        depth: int = 1,
        contract_limit: int = 20,
    ) -> str:
        """Resolve an entity by id (across labels), pull profile + contracts +
        graph neighbourhood, return one composite JSON payload.

        Dispatch order: try Company, then Authority. The chosen label is
        included in the response so the model knows what it's looking at.
        """
        depth = max(1, min(depth, 3))
        # Try Company first — most-common path for procurement awardees.
        label = "Company"
        profile_resp = await client.get(
            f"{self._gmr_api_url}/companies/{entity_id}",
        )
        if profile_resp.status_code == 404:
            label = "Authority"
            profile_resp = await client.get(
                f"{self._gmr_api_url}/authorities/{entity_id}",
            )
        if profile_resp.status_code >= 400:
            return json.dumps({
                "error": f"entity {entity_id} not found",
                "tried_labels": ["Company", "Authority"],
            })

        try:
            props = profile_resp.json()
        except (ValueError, TypeError):
            props = {}

        # A 200 is not proof the entity exists. fontem-api answers
        # /companies/<anything> with a skeleton — the id echoed back and
        # every other field null — so an id that was never in the graph
        # comes back looking like a real, empty company.
        #
        # Left unchecked the assistant then reports "X is a Company
        # (unknown country) with no EU procurement contracts in the graph"
        # about something that does not exist. That is worse than an
        # error: it is a confident negative finding, indistinguishable
        # from a real one, manufactured by us and handed to the model as
        # fact. On a platform whose whole claim is that figures trace back
        # to a source, it is the worst failure available.
        #
        # A real entity always has a name. Nothing else in the skeleton
        # distinguishes it.
        if not entity_name(props):
            return json.dumps({
                "error": f"entity {entity_id} not found",
                "detail": (
                    "The id did not match any entity. Do not report this as "
                    "an entity with no contracts — it is not an entity. Use "
                    "an id returned by search_entities."
                ),
                "tried_labels": ["Company", "Authority"],
            })

        # Contracts — endpoint is per-label.
        contracts_url = (
            f"{self._gmr_api_url}/companies/{entity_id}/contracts"
            if label == "Company"
            else f"{self._gmr_api_url}/authorities/{entity_id}/contracts"
        )
        contracts_resp = await client.get(
            contracts_url, params={"limit": contract_limit},
        )
        try:
            contracts = (
                contracts_resp.json()
                if contracts_resp.status_code < 400 else []
            )
        except (ValueError, TypeError):
            contracts = []
        if isinstance(contracts, dict) and "contracts" in contracts:
            contracts = contracts["contracts"]
        contract_count = len(contracts) if isinstance(contracts, list) else 0

        # Graph neighbourhood (depth 1 unless caller bumped).
        graph_resp = await client.get(
            f"{self._gmr_api_url}/graph/{entity_id}",
            params={"depth": depth},
        )
        try:
            graph = (
                graph_resp.json()
                if graph_resp.status_code < 400 else {}
            )
        except (ValueError, TypeError):
            graph = {}

        return json.dumps({
            "label": label,
            "entity_id": entity_id,
            "props": props,
            "summary": _build_summary(label, props, contract_count),
            "contracts": contracts,
            "contract_count": contract_count,
            "graph": graph,
        })


def from_env() -> "MistralProxyClient":
    """Build a client from the standard env vars."""
    return MistralProxyClient(
        api_key=os.environ.get("MISTRAL_API_KEY", ""),
        model=os.environ.get("MISTRAL_MODEL", _DEFAULT_MODEL),
        api_url=os.environ.get("MISTRAL_API_URL", _MISTRAL_URL),
        gmr_api_url=os.environ.get("GMR_API_INTERNAL", _DEFAULT_GMR_API),
        local_url=os.environ.get("LOCAL_LLM_URL", _LOCAL_URL),
        local_model=os.environ.get("LOCAL_LLM_MODEL", _LOCAL_MODEL),
    )

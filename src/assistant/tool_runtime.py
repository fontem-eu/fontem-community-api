# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals,too-few-public-methods
"""The assistant's tool surface: schemas, execution, and result shaping.

No model lives here and no loop runs here. Both executors — PydanticAI and
LangGraph — delegate to :class:`ToolRuntime`, so a tool behaves identically
whichever is driving.

This module used to be the third executor: a hand-written Mistral
chat-completions loop with its own tool dispatch, dedup, stall detection and
forced continuation. That loop is gone. It was the only path carrying forced
continuation on a stalled chain, which is a real capability lost — recorded
here rather than in a changelog nobody reads, because the frameworks do not
reproduce it and a turn that gives up mid-plan now simply gives up.

What stayed is everything that was never the framework's business:

  * ``investigate_entity`` — the canonical entity-detail tool. One call
    returns label + props + contracts + graph neighbourhood, and it resolves
    the label by NAME rather than status code, because fontem-api answers
    /companies/<anything> and /authorities/<anything> with a 200 skeleton.
  * ``search_entities`` and ``propose_edit``.
  * The legacy narrow getters (``get_company`` / ``get_authority`` /
    ``get_contracts`` / ``explore_graph``) stay implemented in
    :py:meth:`execute_tool` so old saved conversations keep replaying, but
    they are NOT advertised in :data:`_TOOLS` — the model only sees the
    canonical surface.
  * The current date, appended to the system prompt every turn, so the model
    can tell whether a date a tool returned is past or future.
  * The per-turn entity-name cache that turns ids into readable labels in
    the ``status`` events the panel renders.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from src.assistant import doc_tools, generated_tools, legacy_tools, probe_tools
from src.assistant.freshness import _format_freshness_summary
from src.assistant.catalogue import CatalogueCache

from src.assistant import (
    local_models, mock_llm, navigation, studio_tools, tool_budget, tool_trace,
)
from src.assistant.entities import (
    _build_summary, _capture_names, entity_name,
    slim_contract, slim_graph, slim_props,
)
from src.services import audit_context


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
                "Finds companies, authorities, persons and lobbyists by "
                "name, ticker or keyword. Returns up to `limit` matches "
                "across all entity types, each with its id, name, country "
                "and label. The graph contains duplicate entities — country "
                "subsidiaries, spelling and punctuation variants — so "
                "several results may be the same organisation."
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
                "Returns everything held about one entity: its properties, "
                "its EU procurement contracts and its graph neighbourhood. "
                "Works for Companies, Authorities, Lobbyists and Persons — "
                "it dispatches by label internally, so the caller does not "
                "need to know which type an id belongs to. The response "
                "carries a `summary` field holding a short prose précis."
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
                "Proposes an edit to the user's report. Renders as an "
                "Apply/Reject card in the panel; the tool does not mutate "
                "the document itself. Actions: insert_content appends HTML "
                "to the document, insert_widget inserts an interactive "
                "widget node, update_title and update_abstract replace "
                "those fields. Proposals sharing a `group_id` render as one "
                "card the user accepts or rejects as a unit."
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

PROPOSAL_TOOL_ACTIONS = doc_tools.PROPOSAL_TOOL_ACTIONS
WIDGET_TYPES = doc_tools.WIDGET_TYPES

_TOOLS.extend(doc_tools.DOC_TOOLS)
_TOOLS.extend(probe_tools.PROBE_TOOLS)




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
    "mcp__gmr__read_document": "Reading the document",
    "mcp__gmr__set_title": "Proposing a title",
    "mcp__gmr__set_abstract": "Proposing an abstract",
    "mcp__gmr__replace_body": "Proposing a rewrite",
    "mcp__gmr__insert_widget": "Proposing a widget",
    "mcp__gmr__query_graph": "Probing the data store",
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

#: Named here so the fallback below reads as a decision rather than a
#: magic string.
DEFAULT_LOCAL_MODEL_ID = local_models.DEFAULT_MODEL_ID

#: Where a bring-your-own-key turn is sent, per provider.
#:
#: The hand-written loop that used to live in this module sent EVERY
#: provider's key to Mistral's URL, so an OpenAI key could only ever come
#: back 401. Both of these speak the OpenAI chat-completions protocol at
#: these bases, so routing them correctly costs one dict. Anthropic is
#: absent deliberately: it is offered in the UI but does not speak this
#: protocol, and the executors reject it with a legible message rather than
#: sending a key somewhere it cannot work.
PROVIDER_BASE_URLS = {
    "mistral": "https://api.mistral.ai/v1",
    "openai": "https://api.openai.com/v1",
}


@dataclass(frozen=True)
class Route:
    """Where one turn is sent, and on whose key."""

    base_url: str
    api_key: str
    model: str
    local: bool
    timeout: float


def _callers_own_provider(cred: dict, provider: str, key: str,
                          default_model: str) -> tuple[Route | None, str]:
    """Their key, their provider, their bill."""
    base = PROVIDER_BASE_URLS.get(provider)
    if base is None:
        return None, (
            f"provider {provider!r} is not supported by this engine; "
            "remove the key to use the built-in model"
        )
    # A hosted provider answers in seconds; the local one generates on CPU and
    # legitimately takes minutes, which is why the timeouts differ by an order
    # of magnitude.
    return Route(base, key, cred.get("model") or default_model,
                 local=False, timeout=120.0), ""


def _mock_selected(local_model_id: str | None, mock_url: str) -> bool:
    """Whether the scripted e2e model is both asked for and available.

    Requires the flag AND a URL, so a half-configured environment falls
    through to the real models rather than to a dead address.
    """
    return bool(
        (local_model_id or "").strip().lower() == mock_llm.MOCK_MODEL_ID
        and mock_url and mock_llm.enabled()
    )


def _platform_hosted(chosen) -> Route | None:
    """A built-in hosted model the platform pays for, if it is configured.

    Both a key and a base URL are required. A model naming a provider we have
    no base URL for must fall through to the local server, not be sent to
    whichever provider happens to be first in the table.
    """
    if not chosen.hosted:
        return None
    platform_key = local_models.hosted_key(chosen.provider)
    base = local_models.hosted_base_url(chosen.provider)
    if not (platform_key and base):
        return None
    # Our key, our bill, and the same timeout gap as any hosted provider.
    return Route(base, platform_key, chosen.served_name,
                 local=False, timeout=120.0)


def _cluster_local(local_url: str, chosen) -> Route:
    """The cluster-local server. No key: it must never be handed a secret.

    A hosted id reaching here means its key is not configured — `_platform_hosted`
    declined it. Its served_name is a provider's name ("openai/gpt-oss-120b"),
    which llama-server has never heard of, so fall back to the default rather
    than asking for a model that cannot exist. A preference outliving its key
    must degrade, not 404.
    """
    served = (local_models.resolve(DEFAULT_LOCAL_MODEL_ID)
              if chosen.hosted else chosen).served_name
    return Route(local_url.rstrip("/") + "/v1", "", served,
                 local=True, timeout=300.0)


def resolve_route(
    cred: dict | None,
    *,
    local_url: str,
    local_model_id: str | None,
    default_model: str,
    mock_url: str = "",
) -> tuple[Route | None, str]:
    """Pick the endpoint for a turn. Returns (route, error) — one or the other.

    This is the piece worth testing directly: it decides whether a request
    carries a user's secret to a third party or stays inside the cluster.
    Getting it wrong is not a visible bug, it is a leak.

    Order, and it is the whole contract:
      1. The caller supplied a key for a hosted provider — spend theirs.
      2. The scripted e2e model, which stands in for the cluster-local one.
      3. A built-in hosted model the platform pays for.
      4. The cluster-local server, with NO key attached.
      5. None of those — an error the caller can render, not an exception
         mid-stream.

    Case 4 is why this exists: the assistant used to be unusable until you
    pasted an API key, which meant almost nobody used it.

    Steps 2 and 3 sit after the credential branch, never before it. Ahead of
    it, a user with their own provider key whose stored id happened to match
    would have their turn answered by something other than the provider they
    are paying for; a test says so.
    """
    cred = cred or {}
    provider = (cred.get("provider") or "").strip().lower()
    key = cred.get("api_key") or ""

    if provider and provider != LOCAL_PROVIDER and key:
        return _callers_own_provider(cred, provider, key, default_model)

    if _mock_selected(local_model_id, mock_url):
        return Route(mock_url.rstrip("/") + "/v1", "", mock_llm.MOCK_MODEL_ID,
                     local=True, timeout=60.0), ""

    # `resolve` falls back to the default for unknown ids, so the hosted branch
    # is only reached for an id that really is hosted AND has a key — an
    # unconfigured environment falls through to llama-server rather than to a
    # 401 mid-stream.
    chosen = local_models.resolve(local_model_id)
    hosted = _platform_hosted(chosen)
    if hosted is not None:
        return hosted, ""

    if local_url:
        return _cluster_local(local_url, chosen), ""

    return None, "no model is available: set LOCAL_LLM_URL or supply a key"


#: Cluster-internal service address. Plain http on purpose: this never
#: leaves the cluster, the hop is inside the service mesh, and there is no
#: TLS terminator in front of it to speak to.
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
    """The tool surface for one turn, scoped to the user's context.

    navigate goes FIRST, and that is load bearing rather than cosmetic.

    Appending it made qwen3-4b stop calling it entirely. Same five tools,
    same prompt, same model, measured on the eval fixture: navigate last ->
    0 tool calls on "where do I see the maps?"; navigate first -> it calls
    it. The model was not confused about the destination either — with
    navigate last it answered "you can see them on the /map page", having
    matched the route description correctly, then returned prose in 4.8s
    instead of the 14.3s a tool-calling turn takes. It settled on text
    almost immediately rather than deliberating and declining.

    Larger models are less position-sensitive (qwen3-8b scored 100% either
    way), which is exactly why this went unnoticed: it looks like a small
    model being incapable rather than an array being built in the wrong
    order. There is no error, no warning — navigation just quietly stops.
    """
    # One definition of what is offered, shared with the framework engines
    # via engine_tools.OFFERED_BUILTINS. _TOOLS stays the full set — the
    # executor still serves saved conversations that call find_paths and the
    # retired getters — but the model is shown the short list.
    # pylint: disable=import-outside-toplevel
    from src.assistant.engine_tools import OFFERED_BUILTINS
    offered = [t for t in _TOOLS if t["function"]["name"] in OFFERED_BUILTINS]
    tools = list(navigation.scope_tools(offered, has_editor=has_editor))
    tools = tools + list(studio_tools.STUDIO_TOOLS)
    if nav_routes:
        return [navigation.navigate_tool_schema()] + tools
    return tools


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


def _record_call(traced: list | None, call_id: str, name: str, args: dict,
                 result: str, started: float, raw_len: int) -> None:
    """Queue the trace for this call. The closures cannot yield; this rides
    along and the stream loop emits it."""
    if traced is None:
        return
    traced.append(tool_trace.trace(
        name, args, result, time.time() - started,
        raw_len=raw_len or None, call_id=call_id,
    ))


class ToolRuntime:
    """Executes the assistant's tools. Owns no loop and talks to no model.

    Both executors delegate here, so a tool behaves identically whichever
    one is driving: same schemas, same fontem-api calls, same result
    shapes, same 200-skeleton trap avoided in exactly one place.

    This is what remains of the hand-written provider loop that used to
    live in this module — the loop went, the tool surface stayed, because
    the tools were never the framework's business.
    """

    def __init__(
        self,
        gmr_api_url: str = _DEFAULT_GMR_API,
        timeout: float = 120.0,
        client_factory=None,
    ) -> None:
        self._gmr_api_url = gmr_api_url.rstrip("/")
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
        # Tool schemas derived from the API's own spec. Loaded once per
        # process: the spec changes only on deploy, and a deploy makes a
        # new pod.
        self._generated: list[dict] | None = None

    async def _get_generated_tools(self, client: httpx.AsyncClient) -> list[dict]:
        """Schemas for endpoints marked x-agent-tool. Empty on any failure."""
        if self._generated is None:
            self._generated = await generated_tools.fetch_tools(
                client, self._gmr_api_url)
        return self._generated

    async def _execute_generated(self, client: httpx.AsyncClient, name: str,
                                 args: dict) -> str:
        """Delegate to the shared executor, which needs no client state."""
        return await generated_tools.execute(
            client, self._gmr_api_url, self._generated or [], (name, args))

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

    async def dispatch(
        self, client, name: str, args: dict, *,
        studio, nav_routes: list, pending_nav: list,
        budget: list[int], name_cache: dict,
        traced: list | None = None, audit=None,
        allowed: frozenset[str] | None = None,
        doc=None,
    ) -> tuple[str, int]:
        """Run one tool call. Returns (what the model sees, raw result length).

        Every call gets an id, minted here because here is the one place
        that sees the call before it runs. It goes three ways: into the
        `tool_result` event the panel renders, into the conversation row the
        service persists, and into whatever the tool writes while it runs —
        so an activity entry can name the exact call that caused it.

        Both executors call this, so a tool behaves identically whichever is
        driving — and the three special cases below stop being copy-pasted
        into two closures that then drift.

        A raw length of 0 means "nothing came back from fontem-api": the
        Studio and navigate paths answer locally and are not worth a trace
        bubble.
        """
        started = time.time()
        call_id = uuid.uuid4().hex
        # A second lock on the same door. `turn_tool_specs` already withholds
        # everything but navigate from a signed-out caller, so a well-behaved
        # turn never arrives here with anything else — but the spec list is
        # built partly from tools fetched over HTTP from fontem-api at turn
        # time, and "the model was only offered safe tools" is an argument
        # about a prompt, not a guarantee about execution. This is the
        # guarantee, and it sits ahead of every dispatch branch below so no
        # later reordering can get in front of it.
        if allowed is not None and name not in allowed:
            out = json.dumps({
                "error": f"{name} is not available to signed-out visitors",
                "hint": "sign in to use this tool",
            })
            _record_call(traced, call_id, name, args, out, started, 0)
            return out, 0
        # Anything the tool writes from here is attributable to this call,
        # not merely to the turn. Scoped, so the id comes off again when the
        # call ends — a later write belongs to the turn, not to whichever
        # tool happened to run last.
        # Ambient by default: the context is a contextvar set by whoever is
        # driving, so the executor does not have to carry it. The parameter
        # stays for tests that want to hand in their own — and it must not
        # travel in the payload dict, which some proxy clients serialise to
        # JSON and which a module does not survive.
        scope = (audit or audit_context).tool_call(call_id, name)
        with scope:
            return await self._dispatch_inner(
                client, name, args, studio=studio, nav_routes=nav_routes,
                pending_nav=pending_nav, budget=budget, name_cache=name_cache,
                traced=traced, call_id=call_id, started=started, doc=doc,
            )

    async def _dispatch_inner(
        self, client, name: str, args: dict, *,
        studio, nav_routes: list, pending_nav: list,
        budget: list[int], name_cache: dict,
        traced: list | None, call_id: str, started: float,
        doc=None,
    ) -> tuple[str, int]:
        """The dispatch itself, once provenance is in scope."""
        if name == "mcp__gmr__read_document":
            # Server-side, as the asking user, against the report this
            # conversation is bound to. `doc` is absent on non-report
            # conversations, where there is no document to read.
            if doc is None:
                out = json.dumps({
                    "error": "no document is open in this conversation",
                })
                _record_call(traced, call_id, name, args, out, started, 0)
                return out, 0
            out = await doc.read()
            capped, budget[0] = tool_budget.cap_tool_result(out, budget[0])
            _record_call(traced, call_id, name, args, capped, started, len(out))
            return capped, len(out)

        if name in studio_tools.STUDIO_ACTIONS:
            # Server-side, as the asking user. The service checks access on
            # every call, so this cannot reach a project the user could not
            # open themselves.
            if studio is None:
                out = json.dumps({
                    "error": "the Data Studio is not available for this turn",
                })
                _record_call(traced, call_id, name, args, out, started, 0)
                return out, 0
            # The turn's own client and the API it already talks to, handed
            # over so a Studio write can be checked against the same engines
            # the user's Run button uses before it is saved.
            out = await studio.execute(name, args, client=client,
                                       api_url=self._gmr_api_url)
            _record_call(traced, call_id, name, args, out, started, 0)
            return out, 0

        if name == navigation.NAVIGATE_TOOL_NAME:
            # Runs in the browser, not here. The result is only the model's
            # receipt; the panel moves because of the `navigate` SSE event,
            # and the closures cannot yield one — so the emit rides along in
            # `pending_nav` and the stream loop sends it. Dropping it is what
            # made the assistant claim to navigate while the page stayed put.
            result, emit = navigation.navigate_result(
                args.get("path", ""), nav_routes,
            )
            if emit:
                pending_nav.append(emit)
            _record_call(traced, call_id, name, args, result, started, 0)
            return result, 0

        raw = await self.execute_tool(client, name, args)
        # Keep the id->name mapping fresh from every result, so the status
        # line says "Investigating Siemens Energy AG/ADR" rather than a UUID.
        # Read from the FULL result, before the budget cap truncates it.
        try:
            _capture_names(name_cache, json.loads(raw))
        except (ValueError, TypeError):
            pass
        capped, budget[0] = tool_budget.cap_tool_result(raw, budget[0])
        _record_call(traced, call_id, name, args, capped, started, len(raw))
        return capped, len(raw)

    async def execute_tool(
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
            elif (name == probe_tools.PROBE_TOOL_NAME
                  or name == "mcp__gmr__propose_edit"
                  or name in doc_tools.FIELD_PROPOSALS
                  or name == "mcp__gmr__insert_widget"):
                # Locally-answered tools, one delegating return: the probe
                # passes through the guarded proxies (its result rides the
                # shared budget cap like any other), and proposals are
                # notifications the frontend applies with user auth.
                return await self._local_tool(client, name, args)
            elif name in legacy_tools.LEGACY_TOOLS:
                r = await legacy_tools.fetch(
                    client, self._gmr_api_url, name, args)
            elif any(t["function"]["name"] == name
                     for t in (self._generated or [])):
                return await self._execute_generated(client, name, args)
            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

            return (json.dumps({"error": f"API {r.status_code}"})
                    if r.status_code >= 400 else r.text)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            return json.dumps({"error": str(exc)[:200]})

    async def _local_tool(
        self, client: httpx.AsyncClient, name: str, args: dict,
    ) -> str:
        """Tools answered here rather than by a fontem-api GET."""
        if name == probe_tools.PROBE_TOOL_NAME:
            return await probe_tools.execute(client, self._gmr_api_url, args)
        return await self._propose(client, name, args)

    async def _propose(
        self, client: httpx.AsyncClient, name: str, args: dict,
    ) -> str:
        """One proposal, validated to the standard its verb declares."""
        if name == "mcp__gmr__propose_edit":
            # Legacy: replays from stored conversations. Never advertised.
            return json.dumps({"proposed": True, "action": args.get("action")})
        if name in doc_tools.FIELD_PROPOSALS:
            field = doc_tools.FIELD_PROPOSALS[name]
            if not str(args.get(field) or "").strip():
                return json.dumps(
                    {"error": f"{field} is required and was empty"})
            return json.dumps({"proposed": True,
                               "action": PROPOSAL_TOOL_ACTIONS[name]})
        return await self._validate_widget(client, args)

    async def _validate_widget(
        self, client: httpx.AsyncClient, args: dict,
    ) -> str:
        """Refuse a widget that would not render, before it becomes a card.

        The user's Apply button must never be the discovery mechanism for a
        typo'd widget type or an entity id the model invented. The entity
        check goes through `_resolve_profile`, which knows the skeleton-200
        trap: a name proves existence, a 200 alone proves nothing.
        """
        widget_type = str(args.get("widget_type") or "")
        if widget_type not in WIDGET_TYPES:
            return json.dumps({
                "error": f"unknown widget_type {widget_type!r}",
                "hint": f"one of: {', '.join(WIDGET_TYPES)}",
            })
        entity_id = str(args.get("entityId") or "").strip()
        if not entity_id:
            return json.dumps({"error": "entityId is required"})
        label, profile = await self._resolve_profile(client, entity_id)
        if not label:
            return json.dumps({
                "error": f"no entity with id {entity_id!r}",
                "hint": "resolve the id with search_entities first",
            })
        depth = args.get("depth")
        if depth is not None and not 1 <= int(depth) <= 3:
            return json.dumps({"error": "depth must be between 1 and 3"})
        return json.dumps({
            "proposed": True,
            "action": PROPOSAL_TOOL_ACTIONS["mcp__gmr__insert_widget"],
            # The resolved name rides back so the model can narrate the
            # card ("added a graph view of Siemens AG") without a second
            # lookup.
            "entity_name": profile.get("name"),
        })

    #: The labels an id may resolve to, and the endpoint that serves each.
    #: Order is dispatch order — companies are the common case.
    _PROFILE_ENDPOINTS = (("Company", "companies"), ("Authority", "authorities"))

    async def _resolve_profile(
        self, client: httpx.AsyncClient, entity_id: str,
    ) -> tuple[str, dict]:
        """Find which label an id belongs to. ("", {}) when it belongs to none.

        Dispatch is on the NAME, not on the status code.

        A 200 is not proof the entity exists. fontem-api answers
        /companies/<anything> AND /authorities/<anything> with a skeleton —
        the id echoed back and every other field null — so an id that was
        never in the graph comes back looking like a real, empty company.

        Left unchecked the assistant then reports "X is a Company (unknown
        country) with no EU procurement contracts in the graph" about
        something that does not exist. That is worse than an error: it is a
        confident negative finding, indistinguishable from a real one,
        manufactured by us and handed to the model as fact. On a platform
        whose whole claim is that figures trace back to a source, it is the
        worst failure available.

        The skeleton is also why a `404 -> try Authority` fallthrough cannot
        work: /companies never 404s, so the Authority leg was unreachable
        and EVERY authority was diagnosed as a nonexistent company. Metro
        Mondego — 1 contract, €986,546 — came back as "no known entity".

        So each endpoint is tried in turn and the first that yields a name
        wins: a real entity always has one, and nothing else in the
        skeleton distinguishes it.
        """
        for label, path in self._PROFILE_ENDPOINTS:
            resp = await client.get(f"{self._gmr_api_url}/{path}/{entity_id}")
            if resp.status_code >= 400:
                continue
            try:
                body = resp.json()
            except (ValueError, TypeError):
                continue
            if isinstance(body, dict) and entity_name(body):
                return label, body
        return "", {}

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
        label, props = await self._resolve_profile(client, entity_id)

        if not label:
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
        contracts_shown = len(contracts) if isinstance(contracts, list) else 0
        # The graph's OWN total, not the size of the page we just fetched.
        #
        # This used to be len(contracts), which is capped by contract_limit —
        # so the assistant told a user "Siemens AG … with 5 EU procurement
        # contract(s) in the graph" when the graph said 8, and understated
        # every entity with more contracts than the limit. The true figure
        # was already sitting in props["contract_count"], unread. On a
        # platform whose claim is that figures trace to a source, a
        # confidently wrong figure is the worst defect available.
        total = props.get("contract_count") if isinstance(props, dict) else None
        contract_count = total if isinstance(total, int) else contracts_shown

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
            "props": slim_props(props),
            "summary": _build_summary(label, props, contract_count),
            "contracts": ([slim_contract(c) for c in contracts]
                          if isinstance(contracts, list) else contracts),
            # Both numbers, because they differ and the difference matters:
            # the model must not describe a page as the whole set.
            "contract_count": contract_count,
            "contracts_shown": contracts_shown,
            "graph": slim_graph(graph),
        })


def from_env() -> "ToolRuntime":
    """Build a runtime from the standard env vars."""
    return ToolRuntime(
        gmr_api_url=os.environ.get("GMR_API_INTERNAL", _DEFAULT_GMR_API),
    )

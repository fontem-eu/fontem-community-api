# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals
"""A third assistant executor, built on PydanticAI's agent loop.

Sits behind ``ASSISTANT_ENGINE=pydantic-ai`` and satisfies the same
``ProxyClient`` protocol as the native client and the LangGraph one:
``stream(payload)`` yields whole SSE event blocks. Nothing above it can tell
which engine ran the turn, so the three are directly comparable on the same
battery instead of on opinion.

Shared with the other two rather than reimplemented: the tool schemas and
their per-turn ordering (``engine_tools.turn_tool_specs``), the tool executor
with its fontem-api quirks, and the per-turn tool-result budget. What differs
is only who drives the loop.

Two things this path does NOT carry, both worth knowing before switching it
on anywhere that matters:

  * Forced continuation. The native loop notices a turn that stalls
    mid-chain -- searched, named some entities, then began summarising
    instead of investigating -- and pushes it onward with
    ``tool_choice="required"``, twice at most. Measured on Qwen3-4B: 5 turns
    in 21 continue unprompted, 18 in 21 when pushed. PydanticAI ends the run
    when the model stops calling tools, as LangGraph does.
  * A credential path. Turns spending the caller's own provider key are
    handed back to the native client rather than being quietly rerouted to
    the platform's local model.

What it does bring that the others do not: tool arguments are validated
against their JSON schema before the tool runs, so a malformed call becomes
a retry with an error the model can read rather than an exception mid-turn.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator

import httpx

from src.assistant import studio_tools, tool_runtime, tool_budget, tool_trace
from src.assistant.engine_tools import ANONYMOUS_TOOLS, turn_tool_specs
from src.assistant import generated_tools
from src.assistant.tool_runtime import (
    _DEFAULT_GMR_API,
    _sse,
    _system_prompt_with_today,
    _tool_detail,
    resolve_route,
    ToolRuntime,
)
from src.assistant.language import _language_directive

#: Matches the other engines so a comparison measures the loop, not a
#: different budget.
MAX_ITERATIONS = 10

ENGINE_ENV = "ASSISTANT_ENGINE"
ENGINE_NAME = "pydantic-ai"


def drain_traces(traced: list | None) -> list[str]:
    """SSE events for tool calls that finished since the last check.

    Same arrangement as drain_navigations, and for the same reason: the tool
    closures cannot yield. Popping rather than reading matters — a queue read
    twice draws the bubble twice.
    """
    if not traced:
        return []
    out = []
    while traced:
        out.append(_sse(tool_trace.EVENT, traced.pop(0)))
    return out


def drain_navigations(pending_nav: list | None) -> list[str]:
    """SSE events for navigations queued since the last check.

    Separate from the tool closures because they cannot yield: they append
    the emit and this drains it. Popping rather than reading matters — a
    queue read twice navigates twice.
    """
    if not pending_nav:
        return []
    out = []
    while pending_nav:
        out.append(_sse("navigate", pending_nav.pop(0)))
    return out


def engine_selected() -> bool:
    """True when the deployment asked for this executor."""
    return os.environ.get(ENGINE_ENV, "").strip().lower() == ENGINE_NAME


class PydanticAIUnavailable(RuntimeError):
    """pydantic-ai is not importable, or too old for the API used here."""


def _import_pydantic_ai():
    """Import lazily and name the failure.

    Deferred so a deployment on another engine never pays for it, and so a
    missing dependency surfaces as a legible error on the turn that needed
    it rather than as an ImportError at app start that takes the service
    down with it.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.tools import Tool
    except ImportError as exc:                      # pragma: no cover - env
        raise PydanticAIUnavailable(str(exc)) from exc
    return Agent, OpenAIChatModel, OpenAIProvider, Tool


class PydanticAIProxyClient:
    """ProxyClient built on ``pydantic_ai.Agent``."""

    def __init__(self, *, model: str = "",
                 gmr_api_url: str = _DEFAULT_GMR_API,
                 local_url: str = "", local_model: str = "",
                 mock_url: str = "") -> None:
        self._tools = ToolRuntime(gmr_api_url=gmr_api_url)
        # No platform key: a turn either carries the caller's own
        # credential or is answered by the cluster-local model. The
        # decommissioned loop ended with "else spend the platform
        # key", which moved the bill without telling anybody.
        self._model = model
        self._gmr_api_url = gmr_api_url
        self._local_url = local_url
        self._local_model = local_model
        self._mock_url = mock_url

    @staticmethod
    def _drain_navigations(pending_nav: list) -> list[str]:
        return drain_navigations(pending_nav)

    def _build_tools(self, client: httpx.AsyncClient, tool_cls,
                     specs: list[dict], nav_routes: list, budget: list[int],
                     studio, pending_nav: list, name_cache: dict,
                     traced: list, audit=None,
                     allowed: frozenset[str] | None = None, doc=None):
        """Wrap our JSON-schema tools over the shared executor.

        ``Tool.from_schema`` takes the schema as-is, so the model sees exactly
        the descriptions the other engines send — the wording of those has
        been tuned against real failures and must not be paraphrased here.
        """
        tools = []
        for spec in specs:
            fn = spec["function"]
            name = fn["name"]

            async def run(_name=name, **kwargs):
                capped, _raw_len = await self._tools.dispatch(
                    client, _name, kwargs,
                    studio=studio, doc=doc, nav_routes=nav_routes,
                    pending_nav=pending_nav, budget=budget,
                    name_cache=name_cache, traced=traced, audit=audit,
                    allowed=allowed,
                )
                return capped

            tools.append(tool_cls.from_schema(
                function=run, name=name, description=fn["description"],
                json_schema=fn["parameters"],
            ))
        return tools

    async def stream(self, payload: dict) -> AsyncIterator[str]:
        """Run one turn through PydanticAI, emitting the native SSE contract."""
        start = time.time()
        message = payload.get("message", "")
        if not message:
            yield _sse("error", {"error": "Missing message"})
            return

        # Never silently reroute a turn spending the caller's own key: this
        # executor has no credential path, and a different model on someone
        # else's bill is not a detail to discover from an invoice.
        # A turn spending the caller's own key used to be handed to the
        # hand-written loop. That loop is gone, so it is served here — which
        # also fixes it: the old path sent every provider's key to Mistral's
        # URL, so an OpenAI key could only ever come back 401.
        route, route_error = resolve_route(
            payload.get("credential"),
            local_url=self._local_url,
            local_model_id=payload.get("local_model_id") or self._local_model,
            default_model=self._model,
            mock_url=self._mock_url,
        )
        if route is None:
            yield _sse("error", {"error": route_error})
            yield _sse("done", {})
            return

        try:
            agent_cls, model_cls, provider_cls, tool_cls = _import_pydantic_ai()
        except PydanticAIUnavailable as exc:
            yield _sse("error", {"error": f"pydantic-ai engine unavailable: {exc}"})
            yield _sse("done", {})
            return

        system = _system_prompt_with_today(payload.get("system", ""))
        # Answer in the user's language. The hand-written loop did this and
        # neither framework executor ever did, so switching production to
        # this engine silently made the assistant reply in English to a
        # Portuguese reader.
        language = _language_directive(payload.get("locale"))
        if language:
            system = system.rstrip() + language
        nav = payload.get("nav") or {}
        nav_routes = nav.get("routes") or []
        has_editor = bool(payload.get("has_editor"))
        anonymous = bool(payload.get("anonymous"))

        yield _sse("status", {"phase": "connecting",
                              "detail": "Starting assistant...", "elapsed": 0})
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                gen_tools = await generated_tools.fetch_tools(
                    client, self._gmr_api_url,
                )
                specs = turn_tool_specs(gen_tools, has_editor, nav_routes,
                                        anonymous=anonymous)
                budget = [tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN]
                # Navigate emits queue here; the tool closures cannot yield.
                pending_nav: list = []
                # One dict, shared by the tool closures that fill it and the
                # status events that read it.
                name_cache: dict[str, str] = {}
                # Traces queue here for the same reason navigations do: the
                # tool closures cannot yield.
                traced: list = []
                tools = self._build_tools(
                    client, tool_cls, specs, nav_routes, budget,
                    None if anonymous else payload.get("studio_ops"),
                    pending_nav, name_cache, traced,
                    allowed=ANONYMOUS_TOOLS if anonymous else None,
                    doc=None if anonymous else payload.get("doc_ops"),
                )
                # The name the SERVER serves, not the id we store. The
                # production agent runs in router mode and answers to
                # "qwen3-4b-q4_k_m"; asking it for "qwen3-4b" is a 400 that
                # took the assistant down when the LangGraph executor was
                # first switched on.
                # `api_key="none"` rather than "" for the local server:
                # the OpenAI client refuses to construct without one, and
                # the cluster-local server ignores it.
                model = model_cls(route.model, provider=provider_cls(
                    base_url=route.base_url,
                    api_key=route.api_key or "none"))
                agent = agent_cls(model, system_prompt=system, tools=tools)
                async for event in self._run(
                    agent, message, start, pending_nav, name_cache, traced,
                ):
                    yield event
        except (httpx.HTTPError, ValueError, TypeError, KeyError,
                RuntimeError) as exc:
            yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})
        yield _sse("done", {})

    async def _run(self, agent, message: str, start: float,
                   pending_nav: list | None = None,
                   name_cache: dict | None = None,
                   traced: list | None = None) -> AsyncIterator[str]:
        """Translate PydanticAI's event stream into our SSE vocabulary.

        The per-event decision lives in `_translate`, flat, rather than as a
        branch chain nested inside this loop -- same behaviour, but readable
        without holding the loop state in your head.
        """
        state = {"streaming": False, "text_len": 0,
                 "name_cache": name_cache if name_cache is not None else {},
                 "usage": {"input_tokens": 0, "output_tokens": 0}}
        async with agent.run_stream_events(message) as events:
            async for ev in events:
                for out in self._translate(ev, state, start):
                    yield out
                # Traces before navigations: the panel draws the tool bubble,
                # then is asked to move.
                for out in drain_traces(traced):
                    yield out
                # After the tool result, so the panel has already drawn the
                # trace bubble by the time it is asked to move.
                for out in drain_navigations(pending_nav):
                    yield out

        if not state["text_len"]:
            yield _sse("status", {
                "phase": "truncated",
                "detail": "The assistant produced no output this turn.",
                "elapsed": round(time.time() - start, 1)})
        yield _sse("usage", state["usage"])

    def _translate(self, ev, state: dict, start: float) -> list[str]:
        """SSE events for one PydanticAI event. Empty for the ones the panel
        has no vocabulary for, which is most of them."""
        kind = getattr(ev, "event_kind", "")

        if kind == "function_tool_call":
            return [self._tool_status(ev, state["name_cache"], start)]

        if kind == "agent_run_result":
            state["usage"] = self._usage_of(ev) or state["usage"]
            return []

        # `part_start` carries the FIRST piece of a text part; the deltas
        # that follow carry the rest. Handling only the deltas dropped the
        # opening of every single answer — "I cannot provide…" reached the
        # panel as " cannot provide…", and nobody noticed for as long as the
        # missing piece was a plausible word. It took a scripted model, whose
        # exact sentence is known in advance, to make a missing prefix
        # visible: the answer began "urement contract(s)…" and the test
        # could say what it should have been.
        if kind not in ("part_delta", "part_start"):
            return []

        text = (self._start_text(ev) if kind == "part_start"
                else self._delta_text(ev))
        if not text:
            return []
        state["text_len"] += len(text)
        out = [_sse("chunk", {"text": text})]
        if not state["streaming"]:
            state["streaming"] = True
            out.insert(0, _sse("status", {
                "phase": "streaming", "detail": "Writing response...",
                "elapsed": round(time.time() - start, 1)}))
        return out

    @staticmethod
    def _delta_text(ev) -> str:
        """Prose from a delta, empty for the tool-argument deltas that share
        the same event kind."""
        return getattr(getattr(ev, "delta", None), "content_delta", None) or ""

    @staticmethod
    def _start_text(ev) -> str:
        """Prose a part opened with, empty for tool-call parts.

        Reads `part.content` only when it is a string: a tool-call part
        carries structured arguments there, and streaming those to the panel
        as prose would print JSON into the conversation.
        """
        content = getattr(getattr(ev, "part", None), "content", None)
        return content if isinstance(content, str) else ""

    @staticmethod
    def _tool_status(ev, name_cache: dict, start: float) -> str:
        """A tool_use status, with the proposal the Apply card renders from."""
        part = getattr(ev, "part", None)
        name = getattr(part, "tool_name", "") or ""
        raw = getattr(part, "args", None)
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        status = {
            "phase": "tool_use", "tool": name,
            "detail": _tool_detail(name, args, name_cache),
            "elapsed": round(time.time() - start, 1),
        }
        if name == "mcp__gmr__propose_edit":
            status["proposal"] = args
        elif name in tool_runtime.PROPOSAL_TOOL_ACTIONS:
            # The split tools carry the action in their NAME; the card
            # renderer still dispatches on an `action` field, so it is
            # restored here.
            status["proposal"] = {
                **args,
                "action": tool_runtime.PROPOSAL_TOOL_ACTIONS[name],
            }
        elif name in studio_tools.STUDIO_ACTIONS:
            status["studio_action"] = {"action": name, "args": args}
        return _sse("status", status)

    @staticmethod
    def _usage_of(ev) -> dict | None:
        """Token counts, if this build of pydantic-ai exposes them."""
        result = getattr(ev, "result", None)
        usage = getattr(result, "usage", None)
        if callable(usage):
            try:
                usage = usage()
            except (TypeError, ValueError):
                return None
        if usage is None:
            return None
        return {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }

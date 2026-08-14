# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals
"""A second assistant executor, built on LangGraph's agent loop.

Sits behind ``ASSISTANT_ENGINE=langgraph`` and satisfies the same
``ProxyClient`` protocol as the PydanticAI executor: ``stream(payload)``
yields whole SSE event blocks. Nothing above it — the service, the router,
the frontend — can tell which engine ran the turn. That is the point: the two
are swappable, so they can be compared on the same battery instead of argued
about.

Why a second executor rather than a replacement. The native loop is tuned to
this hardware in ways a general-purpose framework has no reason to be:
`truncate_history` quantises the window so llama.cpp can reuse its KV prefix
(measured f_sim 0.98), `tool_budget` caps results against a specific context
size, and `_MAX_FORCED_CONTINUATIONS` exists because of a measured stalling
rate on Qwen3-4B. None of that is thrown away here — the tools, the executor
and the budget are all shared with the native path. What differs is only who
drives the loop.

What LangGraph brings that the native loop does not have:

  * A checkpointer, so a conversation is durable state rather than rows we
    replay into a message list.
  * ``interrupt()``, which makes approval a server-side pause. Today
    ``propose_edit`` executes and the Apply/Reject card is reconstructed in
    the browser, which is exactly why applied/dismissed state was losable.
    This client does NOT yet use interrupts — the SSE contract has no way to
    express "the turn is suspended" and the frontend would need to change.
    Wiring the loop is step one; moving approval server-side is step three.
  * Middleware as a named place for the context engineering we currently
    hand-roll.

Deliberately NOT adopted: ``SummarizationMiddleware``. It rewrites history,
and llama.cpp reuses the longest common prefix of the prompt — a rewrite
invalidates the cache and costs a full re-prefill on every turn. Our
quantised window is the better tool here and stays in charge upstream.
"""
from __future__ import annotations


import os
import time
from collections.abc import AsyncIterator

import httpx

from src.assistant import (
    generated_tools,
    navigation,
    studio_tools,
    tool_budget,
    tool_trace,
)
from src.assistant.engine_tools import ANONYMOUS_TOOLS, turn_tool_specs
from src.assistant.tool_runtime import (
    _DEFAULT_GMR_API,
    _sse,
    _system_prompt_with_today,
    _tool_detail,
    resolve_route,
    ToolRuntime,
)
from src.assistant.language import _language_directive

#: Matches the native loop, so a comparison measures the engine and not a
#: different iteration budget.
MAX_ITERATIONS = 10

#: The engine flag. Anything other than "langgraph" leaves the native loop in
#: charge, so this file is inert until someone opts in.
ENGINE_ENV = "ASSISTANT_ENGINE"
ENGINE_NAME = "langgraph"


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


class LangGraphUnavailable(RuntimeError):
    """LangChain is not importable, or too old to expose ``create_agent``."""


def _import_langchain():
    """Import lazily and name the failure.

    The import is deferred so a deployment running the native engine never
    pays for it, and so a missing dependency surfaces as a legible error on
    the turn that needed it rather than as an ImportError at app start that
    takes the whole service down.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_openai import ChatOpenAI
    except ImportError as exc:                      # pragma: no cover - env
        raise LangGraphUnavailable(str(exc)) from exc
    return create_agent, StructuredTool, ChatOpenAI


class LangGraphProxyClient:
    """ProxyClient built on ``create_agent``.

    Shares the tool surface with the other executor via :class:`ToolRuntime`
    rather than inheritance: the executor is the part with the fontem-api
    contract in it, including the guard that turns the API's null skeleton
    into an explicit "not found", and there must be exactly one of those.
    """

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
    def _navigate(path: str, nav_routes: list, pending_nav: list) -> str:
        """Serve one navigate call: receipt for the model, emit for the panel.

        The emit is queued rather than returned because the tool closures
        cannot yield; the stream loop drains it. Dropping it is what made the
        assistant claim to navigate while the page stayed put, so it is kept
        in one place that both the sync and async bridges call.
        """
        result, emit = navigation.navigate_result(path, nav_routes)
        if emit:
            pending_nav.append(emit)
        return result

    def _build_tools(self, client: httpx.AsyncClient, structured_tool,
                     specs: list[dict], nav_routes: list, seen: list,
                     budget: list[int], traced: list, studio,
                     pending_nav: list, name_cache: dict, audit=None,
                     allowed: frozenset[str] | None = None):
        """Wrap our tool schemas as LangChain tools over the shared executor.

        ``budget`` is a one-element list so the closures can spend a single
        per-turn allowance between them. The cap is the same one the native
        engine applies: an uncapped result overflows the context and kills
        the turn outright, and that failure does not become less real for
        being inside a framework.
        """
        tools = []
        for spec in specs:
            fn = spec["function"]
            name = fn["name"]

            def run(_name=name, **kwargs):
                seen.append((_name, kwargs))
                # The Studio ops and every fontem-api tool are async and this
                # bridge is not; they are registered with `coroutine=arun`, so
                # this path only ever serves navigate — which answers locally.
                if _name == navigation.NAVIGATE_TOOL_NAME:
                    return self._navigate(
                        kwargs.get("path", ""), nav_routes, pending_nav)
                # Sync bridge: create_agent calls tools synchronously unless
                # they are coroutines, and the executor is async.
                raise NotImplementedError

            async def arun(_name=name, **kwargs):
                seen.append((_name, kwargs))
                capped, _raw_len = await self._tools.dispatch(
                    client, _name, kwargs,
                    studio=studio, nav_routes=nav_routes,
                    pending_nav=pending_nav, budget=budget,
                    name_cache=name_cache, traced=traced, audit=audit,
                    allowed=allowed,
                )
                return capped

            tools.append(structured_tool(
                name=name, description=fn["description"],
                args_schema=fn["parameters"], func=run, coroutine=arun,
            ))
        return tools

    async def stream(self, payload: dict) -> AsyncIterator[str]:
        """Run one turn through LangGraph, emitting the native SSE contract."""
        start = time.time()
        message = payload.get("message", "")
        if not message:
            yield _sse("error", {"error": "Missing message"})
            return

        # A caller spending their OWN provider key must not be quietly
        # rerouted to the platform's local model: different model, different
        # bill, and no way for them to tell. This executor has no credential
        # path yet, so it hands those turns back to the native client rather
        # than pretending. Zero users are affected today (user_llm_credentials
        # is empty in production) — this exists so that stays true the moment
        # someone configures one.
        # Their key, their provider, their bill. This used to be handed to
        # the hand-written loop, which is gone — and which sent every
        # provider's key to Mistral's URL regardless, so an OpenAI key could
        # only ever come back 401.
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

        system = _system_prompt_with_today(payload.get("system", ""))
        # Answer in the user's language. The hand-written loop did this and
        # neither framework executor ever did.
        language = _language_directive(payload.get("locale"))
        if language:
            system = system.rstrip() + language
        nav = payload.get("nav") or {}
        nav_routes = nav.get("routes") or []
        has_editor = bool(payload.get("has_editor"))
        anonymous = bool(payload.get("anonymous"))

        # Cheap preconditions before the expensive import: a turn that
        # cannot run should say why in the terms the operator can act on,
        # and "LOCAL_LLM_URL is unset" is more useful than an import error
        # that is merely the next thing to fail.
        try:
            create_agent, structured_tool, chat_openai = _import_langchain()
        except LangGraphUnavailable as exc:
            yield _sse("error", {"error": f"langgraph engine unavailable: {exc}"})
            yield _sse("done", {})
            return

        yield _sse("status", {"phase": "connecting",
                              "detail": "Starting assistant...", "elapsed": 0})

        seen: list = []
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                gen_tools = await generated_tools.fetch_tools(
                    client, self._gmr_api_url,
                )
                specs = turn_tool_specs(gen_tools, has_editor, nav_routes,
                                        anonymous=anonymous)
                budget = [tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN]
                traced: list = []
                # Navigate emits queue here; the tool closures cannot yield.
                pending_nav: list = []
                # One dict, shared by the tool closures that fill it and the
                # status events that read it.
                name_cache: dict[str, str] = {}
                tools = self._build_tools(
                    client, structured_tool, specs, nav_routes, seen, budget,
                    traced, None if anonymous else payload.get("studio_ops"),
                    pending_nav, name_cache,
                    allowed=ANONYMOUS_TOOLS if anonymous else None,
                )
                # What the id resolves to on the server, not the id itself.
                # The production agent runs in router mode and serves
                # "qwen3-4b-q4_k_m"; asking it for "qwen3-4b" is a 400,
                # which took the assistant down the moment this executor
                # was switched on. The native client has always gone
                # through local_models.resolve() for exactly this.
                llm = chat_openai(
                    model=route.model,
                    base_url=route.base_url,
                    # "none" rather than "": the OpenAI client refuses to
                    # construct without one, and the local server ignores it.
                    api_key=route.api_key or "none",
                    temperature=0.3, timeout=route.timeout,
                )
                agent = create_agent(model=llm, tools=tools,
                                     system_prompt=system)
                async for event in self._run(
                    agent, message, start, seen, traced, pending_nav,
                    name_cache,
                ):
                    yield event
        except (httpx.HTTPError, ValueError, TypeError, KeyError,
                RuntimeError) as exc:
            yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})
        yield _sse("done", {})

    def _announce(self, seen: list, announced: int, name_cache: dict,
                  start: float):
        """Emit one status per tool call the graph has run but not announced.

        Returns the events and the new watermark. Split out of the stream
        loop because announcing calls and relaying tokens are two different
        jobs that happen to arrive on the same iterator.
        """
        events = []
        while announced < len(seen):
            name, args = seen[announced]
            announced += 1
            status = {
                "phase": "tool_use", "tool": name,
                "detail": _tool_detail(name, args, name_cache),
                "elapsed": round(time.time() - start, 1),
            }
            # The frontend renders the Apply card off this field.
            if name == "mcp__gmr__propose_edit":
                status["proposal"] = args
            elif name in studio_tools.STUDIO_ACTIONS:
                status["studio_action"] = {"action": name, "args": args}
            events.append(_sse("status", status))
        return events, announced

    @staticmethod
    def _text_of(msg) -> str:
        """Plain text from a message, whether it is a string or content blocks."""
        if getattr(msg, "tool_call_chunks", None):
            return ""
        text = getattr(msg, "content", "") or ""
        if isinstance(text, list):
            return "".join(b.get("text", "") for b in text
                           if isinstance(b, dict))
        return text

    def _on_message(self, chunk, state: dict, start: float) -> list[str]:
        """SSE events for one streamed message chunk. Empty for the chunks
        that carry no prose, which is most of them — tool-call fragments
        arrive on the same stream."""
        msg, _meta = chunk
        text = self._text_of(msg)
        if not text:
            return []
        out = []
        if not state["streaming"]:
            state["streaming"] = True
            out.append(_sse("status", {"phase": "streaming",
                                       "detail": "Writing response...",
                                       "elapsed": round(time.time() - start, 1)}))
        state["text_len"] += len(text)
        out.append(_sse("chunk", {"text": text}))
        meta = getattr(msg, "usage_metadata", None) or {}
        if meta:
            state["usage"]["input_tokens"] = max(
                state["usage"]["input_tokens"], meta.get("input_tokens", 0))
            state["usage"]["output_tokens"] += meta.get("output_tokens", 0)
        return out

    async def _run(self, agent, message: str, start: float, seen: list,
                   traced: list, pending_nav: list | None = None,
                   name_cache: dict | None = None) -> AsyncIterator[str]:
        """Translate the graph's event stream into our SSE vocabulary.

        Deliberately narrow: the panel understands five event types and five
        status phases, and emitting anything else would be a contract change
        disguised as an implementation detail. The per-chunk decision lives
        in `_on_message`, flat, rather than nested inside this loop.
        """
        state = {"announced": 0, "streaming": False, "text_len": 0,
                 "name_cache": name_cache if name_cache is not None else {},
                 "usage": {"input_tokens": 0, "output_tokens": 0}}

        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            stream_mode=["messages", "updates"],
            config={"recursion_limit": MAX_ITERATIONS * 2},
        ):
            if mode == "updates":
                events, state["announced"] = self._announce(
                    seen, state["announced"], state["name_cache"], start)
                # Navigations after the traces, so the panel has drawn
                # the tool bubble before it is asked to move.
                for ev in (events + drain_traces(traced)
                           + drain_navigations(pending_nav)):
                    yield ev
                continue
            for ev in self._on_message(chunk, state, start):
                yield ev

        # A navigation queued after the final "updates" event would otherwise
        # be stranded in the queue, which is the original bug wearing a
        # different hat: the model says it navigated, nothing moves.
        for ev in drain_navigations(pending_nav):
            yield ev

        text_len = state["text_len"]
        usage = state["usage"]
        if not text_len and not seen:
            yield _sse("status", {
                "phase": "truncated",
                "detail": "The assistant produced no output this turn.",
                "elapsed": round(time.time() - start, 1)})
        yield _sse("usage", usage)

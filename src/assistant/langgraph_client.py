# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals
"""A second assistant executor, built on LangGraph's agent loop.

Sits behind ``ASSISTANT_ENGINE=langgraph`` and satisfies the same
``ProxyClient`` protocol as :class:`MistralProxyClient`: ``stream(payload)``
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
    local_models,
    navigation,
    tool_budget,
    tool_trace,
)
from src.assistant.engine_tools import turn_tool_specs
from src.assistant.mistral_client import (
    _DEFAULT_GMR_API,
    _sse,
    _system_prompt_with_today,
    _tool_detail,
    MistralProxyClient,
)

#: Matches the native loop, so a comparison measures the engine and not a
#: different iteration budget.
MAX_ITERATIONS = 10

#: The engine flag. Anything other than "langgraph" leaves the native loop in
#: charge, so this file is inert until someone opts in.
ENGINE_ENV = "ASSISTANT_ENGINE"
ENGINE_NAME = "langgraph"


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

    Shares the tool executor with :class:`MistralProxyClient` by delegation
    rather than inheritance: the executor is the part with the fontem-api
    contract in it, including the guard that turns the API's null skeleton
    into an explicit "not found", and there must be exactly one of those.
    """

    def __init__(self, *, api_key: str = "", model: str = "",
                 gmr_api_url: str = _DEFAULT_GMR_API,
                 local_url: str = "", local_model: str = "") -> None:
        self._native = MistralProxyClient(
            api_key=api_key, model=model, gmr_api_url=gmr_api_url,
            local_url=local_url, local_model=local_model,
        )
        self._gmr_api_url = gmr_api_url
        self._local_url = local_url
        self._local_model = local_model

    def _build_tools(self, client: httpx.AsyncClient, structured_tool,
                     specs: list[dict], nav_routes: list, seen: list,
                     budget: list[int], traced: list):
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
                if _name == navigation.NAVIGATE_TOOL_NAME:
                    result, _emit = navigation.navigate_result(
                        kwargs.get("path", ""), nav_routes,
                    )
                    return result
                # Sync bridge: create_agent calls tools synchronously unless
                # they are coroutines, and the executor is async.
                raise NotImplementedError

            async def arun(_name=name, **kwargs):
                seen.append((_name, kwargs))
                if _name == navigation.NAVIGATE_TOOL_NAME:
                    result, _emit = navigation.navigate_result(
                        kwargs.get("path", ""), nav_routes,
                    )
                    return result
                raw = await self._native._execute_tool(  # pylint: disable=protected-access
                    client, _name, kwargs,
                )
                capped, budget[0] = tool_budget.cap_tool_result(raw, budget[0])
                # Stash for the trace event: this closure cannot yield, so
                # the result rides along and _announce emits it.
                traced.append((_name, kwargs, capped, len(raw)))
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
        if payload.get("credential"):
            async for event in self._native.stream(payload):
                yield event
            return

        system = _system_prompt_with_today(payload.get("system", ""))
        nav = payload.get("nav") or {}
        nav_routes = nav.get("routes") or []
        has_editor = bool(payload.get("has_editor"))

        # Cheap preconditions before the expensive import: a turn that
        # cannot run should say why in the terms the operator can act on,
        # and "LOCAL_LLM_URL is unset" is more useful than an import error
        # that is merely the next thing to fail.
        if not self._local_url:
            yield _sse("error", {
                "error": "langgraph engine requires LOCAL_LLM_URL"})
            yield _sse("done", {})
            return

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
                specs = turn_tool_specs(gen_tools, has_editor, nav_routes)
                budget = [tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN]
                traced: list = []
                tools = self._build_tools(
                    client, structured_tool, specs, nav_routes, seen, budget,
                    traced,
                )
                # What the id resolves to on the server, not the id itself.
                # The production agent runs in router mode and serves
                # "qwen3-4b-q4_k_m"; asking it for "qwen3-4b" is a 400,
                # which took the assistant down the moment this executor
                # was switched on. The native client has always gone
                # through local_models.resolve() for exactly this.
                served = local_models.resolve(
                    payload.get("local_model_id") or self._local_model,
                ).served_name
                llm = chat_openai(
                    model=served,
                    base_url=self._local_url.rstrip("/") + "/v1",
                    api_key="none", temperature=0.3, timeout=300.0,
                )
                agent = create_agent(model=llm, tools=tools,
                                     system_prompt=system)
                async for event in self._run(agent, message, start, seen, traced):
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

    @staticmethod
    def _drain_traces(traced: list, start: float) -> list[str]:
        """Trace events for tools that have finished since the last check.

        Separate from the stream loop because the tool closures cannot
        yield: they append here and this drains it.
        """
        out = []
        while traced:
            tname, targs, tresult, traw = traced.pop(0)
            out.append(_sse(tool_trace.EVENT, tool_trace.trace(
                tname, targs, tresult, time.time() - start, raw_len=traw)))
        return out

    async def _run(self, agent, message: str, start: float,
                   seen: list, traced: list) -> AsyncIterator[str]:
        """Translate the graph's event stream into our SSE vocabulary.

        The mapping is deliberately narrow. The panel understands five event
        types and five status phases; emitting anything else would be a
        contract change disguised as an implementation detail.
        """
        name_cache: dict[str, str] = {}
        announced = 0
        streaming = False
        text_len = 0
        usage = {"input_tokens": 0, "output_tokens": 0}

        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            stream_mode=["messages", "updates"],
            config={"recursion_limit": MAX_ITERATIONS * 2},
        ):
            if mode == "updates":
                events, announced = self._announce(
                    seen, announced, name_cache, start)
                for ev in events:
                    yield ev
                for ev in self._drain_traces(traced, start):
                    yield ev
                continue

            msg, _meta = chunk
            text = self._text_of(msg)
            if not text:
                continue
            if not streaming:
                streaming = True
                yield _sse("status", {"phase": "streaming",
                                      "detail": "Writing response...",
                                      "elapsed": round(time.time() - start, 1)})
            text_len += len(text)
            yield _sse("chunk", {"text": text})
            meta = getattr(msg, "usage_metadata", None) or {}
            if meta:
                usage["input_tokens"] = max(usage["input_tokens"],
                                            meta.get("input_tokens", 0))
                usage["output_tokens"] += meta.get("output_tokens", 0)

        if not text_len and not seen:
            yield _sse("status", {
                "phase": "truncated",
                "detail": "The assistant produced no output this turn.",
                "elapsed": round(time.time() - start, 1)})
        yield _sse("usage", usage)

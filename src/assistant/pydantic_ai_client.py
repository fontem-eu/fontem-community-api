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

from src.assistant import local_models, navigation, tool_budget
from src.assistant.engine_tools import turn_tool_specs
from src.assistant import generated_tools
from src.assistant.mistral_client import (
    _DEFAULT_GMR_API,
    _sse,
    _system_prompt_with_today,
    _tool_detail,
    MistralProxyClient,
)

#: Matches the other engines so a comparison measures the loop, not a
#: different budget.
MAX_ITERATIONS = 10

ENGINE_ENV = "ASSISTANT_ENGINE"
ENGINE_NAME = "pydantic-ai"


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

    def _build_tools(self, client: httpx.AsyncClient, tool_cls,
                     specs: list[dict], nav_routes: list, budget: list[int]):
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
                if _name == navigation.NAVIGATE_TOOL_NAME:
                    # Runs in the browser, not here. The model is told
                    # whether the path was accepted.
                    result, _emit = navigation.navigate_result(
                        kwargs.get("path", ""), nav_routes,
                    )
                    return result
                raw = await self._native._execute_tool(  # pylint: disable=protected-access
                    client, _name, kwargs,
                )
                capped, budget[0] = tool_budget.cap_tool_result(raw, budget[0])
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
        if payload.get("credential"):
            async for event in self._native.stream(payload):
                yield event
            return

        if not self._local_url:
            yield _sse("error", {
                "error": "pydantic-ai engine requires LOCAL_LLM_URL"})
            yield _sse("done", {})
            return

        try:
            agent_cls, model_cls, provider_cls, tool_cls = _import_pydantic_ai()
        except PydanticAIUnavailable as exc:
            yield _sse("error", {"error": f"pydantic-ai engine unavailable: {exc}"})
            yield _sse("done", {})
            return

        system = _system_prompt_with_today(payload.get("system", ""))
        nav = payload.get("nav") or {}
        nav_routes = nav.get("routes") or []
        has_editor = bool(payload.get("has_editor"))

        yield _sse("status", {"phase": "connecting",
                              "detail": "Starting assistant...", "elapsed": 0})
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                gen_tools = await generated_tools.fetch_tools(
                    client, self._gmr_api_url,
                )
                specs = turn_tool_specs(gen_tools, has_editor, nav_routes)
                budget = [tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN]
                tools = self._build_tools(
                    client, tool_cls, specs, nav_routes, budget,
                )
                # The name the SERVER serves, not the id we store. The
                # production agent runs in router mode and answers to
                # "qwen3-4b-q4_k_m"; asking it for "qwen3-4b" is a 400 that
                # took the assistant down when the LangGraph executor was
                # first switched on.
                served = local_models.resolve(
                    payload.get("local_model_id") or self._local_model,
                ).served_name
                model = model_cls(served, provider=provider_cls(
                    base_url=self._local_url.rstrip("/") + "/v1",
                    api_key="none"))
                agent = agent_cls(model, system_prompt=system, tools=tools)
                async for event in self._run(agent, message, start):
                    yield event
        except (httpx.HTTPError, ValueError, TypeError, KeyError,
                RuntimeError) as exc:
            yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})
        yield _sse("done", {})

    async def _run(self, agent, message: str,
                   start: float) -> AsyncIterator[str]:
        """Translate PydanticAI's event stream into our SSE vocabulary.

        Narrow on purpose: the panel understands five event types and five
        status phases, and emitting anything else would be a contract change
        dressed as an implementation detail.
        """
        name_cache: dict[str, str] = {}
        streaming = False
        text_len = 0
        usage = {"input_tokens": 0, "output_tokens": 0}

        async with agent.run_stream_events(message) as events:
            async for ev in events:
                kind = getattr(ev, "event_kind", "")

                if kind == "function_tool_call":
                    yield self._tool_status(ev, name_cache, start)
                    continue

                if kind == "final_result" and not streaming:
                    streaming = True
                    yield _sse("status", {
                        "phase": "streaming", "detail": "Writing response...",
                        "elapsed": round(time.time() - start, 1)})
                    continue

                if kind == "part_delta":
                    text = getattr(getattr(ev, "delta", None),
                                   "content_delta", None)
                    if text:
                        text_len += len(text)
                        yield _sse("chunk", {"text": text})
                    continue

                if kind == "agent_run_result":
                    usage = self._usage_of(ev) or usage

        if not text_len:
            yield _sse("status", {
                "phase": "truncated",
                "detail": "The assistant produced no output this turn.",
                "elapsed": round(time.time() - start, 1)})
        yield _sse("usage", usage)

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

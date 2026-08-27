"""The eval harness must survive refactors of the code it measures.

`evals/runner.py` deliberately imports the shipped tool schemas, executor and
system prompt instead of copying them, so that a run measures the assistant we
actually serve. The cost of that choice is that any rename inside
``src.assistant`` breaks the harness -- and it breaks silently, because nothing
in CI runs it.

That is not hypothetical. The ``mistral_client`` -> ``tool_runtime`` rename in
2b37dcc took the harness with it, and every run for the following week died on
the import before reaching a model. The gap looked like nobody asking for
numbers; it was the harness being unable to produce any.

These tests are the cheap half of the fix: they import through the same entry
point a run uses, so a rename that breaks the harness fails a normal `pytest`
instead of surfacing the next time someone wants to compare models.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

RUNNER = pathlib.Path(__file__).resolve().parent.parent / "evals" / "runner.py"


def _runner_module():
    """Import evals/runner.py the way an actual run does."""
    spec = importlib.util.spec_from_file_location("eval_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_file_is_where_the_docs_say():
    assert RUNNER.is_file()


def test_runner_imports_without_the_repo_root_preloaded(monkeypatch):
    """Running `python evals/runner.py` puts evals/ on sys.path, not the root.

    The runner bootstraps the root itself for exactly that reason. Dropping the
    bootstrap would leave the harness working under pytest (which puts the root
    on the path) and broken from the command line, which is the only way it is
    ever actually invoked.
    """
    root = str(RUNNER.parent.parent)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])
    module = _runner_module()
    assert root in sys.path
    assert module is not None


def test_load_shipped_still_resolves_the_app_it_measures():
    module = _runner_module()
    client_cls, prompt, origin, nav_context = module.load_shipped()

    assert client_cls is not None
    assert prompt.strip()
    # A fallback prompt means the harness measured a system prompt production
    # does not use. Survivable for a smoke run, fatal for a model comparison,
    # so it must not pass unnoticed.
    assert origin == "shipped", f"system prompt did not come from the app: {origin}"
    assert nav_context is not None


def _production_specs(routes=({"path": "/map", "description": "Atlas"},),
                      compact=False):
    """Tools the way the runner now builds them: production's own assembly."""
    from src.assistant.engine_tools import turn_tool_specs  # pylint: disable=import-outside-toplevel
    return turn_tool_specs([], True, list(routes), compact=compact)


def test_navigate_is_offered_exactly_when_routes_exist():
    """P11-P14 score navigation. Production offers navigate only with a
    site map, and the harness now assembles tools with production's own
    turn_tool_specs — so the old always-on navigate is gone by design."""
    names = [t["function"]["name"] for t in _production_specs()]
    assert any("navigate" in n for n in names), names
    names = [t["function"]["name"] for t in _production_specs(routes=())]
    assert not any("navigate" in n for n in names), names


def test_every_offered_tool_is_shaped_like_an_openai_tool():
    for compact in (False, True):
        tools = _production_specs(compact=compact)
        assert tools
        names = [t["function"]["name"] for t in tools]
        assert len(names) == len(set(names)), f"duplicate: {names}"
        for tool in tools:
            assert tool.get("type") == "function", tool
            fn = tool["function"]
            assert fn.get("name")
            assert isinstance(fn.get("parameters"), dict), fn["name"]


def test_executor_entry_point_exists():
    """The runner calls `proxy.execute_tool`; a rename there breaks every run."""
    module = _runner_module()
    client_cls, *_ = module.load_shipped()
    assert callable(getattr(client_cls, "execute_tool", None))


def test_cli_starts_from_the_command_line():
    """End to end on the invocation that actually broke: `python evals/runner.py`.

    --help exits before any network call, so this stays a unit test while still
    exercising the real entry point, the real path bootstrap and the real
    argument parser.
    """
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(RUNNER.parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    for flag in ("--base-url", "--models", "--api-key", "--only", "--gmr-api"):
        assert flag in proc.stdout, f"{flag} missing from the harness CLI"


# --- run metadata -----------------------------------------------------------
#
# Results are committed to the repo so runs stay comparable over time. That
# makes the metadata a disclosure surface: whatever it records ends up in git
# history, where a leaked credential cannot be taken back.

def test_endpoint_is_recorded_without_credentials():
    module = _runner_module()
    leaky = "https://user:sup3rsecret@inference.example.com/api/v1?api_key=abc123"
    host = module.endpoint_host(leaky)
    assert "sup3rsecret" not in host
    assert "abc123" not in host
    assert "user" not in host
    assert host == "https://inference.example.com"


def test_endpoint_keeps_the_port_that_distinguishes_two_local_servers():
    module = _runner_module()
    host = module.endpoint_host("http://llama-server.llm-service.svc.cluster.local:8080")
    assert host.endswith(":8080")


def test_metadata_carries_what_makes_two_runs_comparable():
    module = _runner_module()

    class Args:                       # pylint: disable=too-few-public-methods
        models = "qwen3-8b-q4_k_m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"

    meta = module.run_metadata(Args(), {"version": 2}, "shipped", 14)
    assert meta["fixture_version"] == 2
    assert meta["prompts"] == 14
    assert meta["models"] == ["qwen3-8b-q4_k_m"]
    assert meta["system_prompt"] == "shipped"
    assert meta["run_at"].endswith("+00:00")
    # The round cap and the tool-result budget change what a model can score.
    # A run compared against one with a different cap is comparing harnesses.
    assert meta["max_rounds"] == module.MAX_ROUNDS
    assert meta["tool_result_char_budget"] == module.MAX_TOOL_RESULT_CHARS


def test_metadata_never_carries_the_api_key():
    """--api-key is not on the recorded surface at all, by construction."""
    module = _runner_module()

    secret = "s3cr3t-bearer-value"

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        # Both shapes a credential reaches a base URL in: an operator pasting
        # a provider's copy-paste URL, and userinfo in the authority. The
        # metadata must survive either without recording it.
        base_url = f"https://key:{secret}@inference.example.com/api?token={secret}"
        gmr_api = f"http://svc:{secret}@fontem-api"
        api_key = secret

    blob = str(module.run_metadata(Args(), {"version": 2}, "shipped", 1))
    assert secret not in blob, blob


def test_results_can_go_to_stdout():
    """`--out -` is what makes a pod run recoverable; it must stay documented."""
    module = _runner_module()
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(RUNNER.parent.parent))
    assert "stdout" in proc.stdout, "--out no longer documents the stdout mode"
    assert module is not None


def test_code_sha_can_be_supplied_when_git_cannot_answer():
    """Pod runs unpack a tarball, so `git rev-parse` there finds nothing."""
    module = _runner_module()

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"
        code_sha = "deadbee"

    meta = module.run_metadata(Args(), {"version": 2}, "shipped", 1)
    assert meta["code_sha"] == "deadbee"


def test_round_cap_is_configurable_and_recorded():
    """The cap decides whether a slow-converging model scores as failing.

    A model that fans out several tool calls per round exhausts a cap tuned
    for one-call-per-round models, and the result is indistinguishable from
    the model refusing to answer. So the cap has to be movable, and whatever
    it was has to be in the metadata.
    """
    module = _runner_module()

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"
        max_rounds = 12

    assert module.run_metadata(Args(), {"version": 2}, "shipped", 1)["max_rounds"] == 12

    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(RUNNER.parent.parent))
    assert "--max-rounds" in proc.stdout


def test_round_cap_defaults_to_the_module_constant():
    """Runs that do not pass it must stay comparable with the committed ones."""
    module = _runner_module()

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"

    meta = module.run_metadata(Args(), {"version": 2}, "shipped", 1)
    assert meta["max_rounds"] == module.MAX_ROUNDS == 6


# --- token budget -----------------------------------------------------------
#
# The harness sends a fixed per-reply token budget. A reasoning model spends it
# on the reasoning trace before emitting any answer, so a budget sized for a
# direct answer comes back as finish_reason="length" with empty content. The
# scorer cannot tell that apart from a model that declined, and Qwen3.6-35B
# scored completion -100% on that basis while answering fine at a larger
# budget. Both the flag and the truncation report are load-bearing.

def test_token_budget_is_configurable_and_recorded():
    module = _runner_module()

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"
        max_tokens = 4000

    assert module.run_metadata(Args(), {"version": 2}, "shipped", 1)["max_tokens"] == 4000

    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(RUNNER.parent.parent))
    assert "--max-tokens" in proc.stdout


def _reply(finish_reason, content, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish_reason}]}


def _run(module, payload, **kw):
    """Drive run_prompt against a stubbed chat-completions endpoint."""
    import asyncio                    # pylint: disable=import-outside-toplevel
    import httpx                      # pylint: disable=import-outside-toplevel

    def handler(request):             # pylint: disable=unused-argument
        return httpx.Response(200, json=payload)

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await module.run_prompt(
                client, None, "http://stub", "m",
                {"id": "T01", "prompt": "hi"}, [], "sys", "", None, **kw)

    return asyncio.run(go())


def test_a_truncated_reply_is_reported_not_scored_as_silence():
    module = _runner_module()
    trace = _run(module, _reply("length", ""), max_tokens=900)
    assert trace.answer == ""
    assert trace.error, "a truncated reply must not look like a refusal"
    assert "max_tokens=900" in trace.error


def test_a_real_empty_answer_is_still_reported_as_empty():
    """finish_reason=stop with no content is the model declining. Different bug."""
    module = _runner_module()
    trace = _run(module, _reply("stop", ""), max_tokens=900)
    assert trace.answer == ""
    assert not trace.error


def test_an_answer_that_fits_is_untouched():
    module = _runner_module()
    trace = _run(module, _reply("stop", "  Four  "), max_tokens=900)
    assert trace.answer == "Four"
    assert not trace.error


def test_the_budget_reaches_the_wire():
    """Recording max_tokens in the metadata is worthless if the request ignores it.

    Without this, hardcoding the budget back into the request body leaves every
    test green and every result file claiming a budget that was never sent.
    """
    import asyncio                    # pylint: disable=import-outside-toplevel
    import json as _json              # pylint: disable=import-outside-toplevel
    import httpx                      # pylint: disable=import-outside-toplevel

    module = _runner_module()
    seen = {}

    def handler(request):
        seen.update(_json.loads(request.content))
        return httpx.Response(200, json=_reply("stop", "done"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await module.run_prompt(c, None, "http://stub", "m",
                                    {"id": "T01", "prompt": "hi"}, [], "sys",
                                    "", None, max_tokens=4000)

    asyncio.run(go())
    assert seen["max_tokens"] == 4000, seen


def test_the_api_key_reaches_the_wire_and_is_absent_when_unset():
    import asyncio                    # pylint: disable=import-outside-toplevel
    import httpx                      # pylint: disable=import-outside-toplevel

    module = _runner_module()
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_reply("stop", "done"))

    async def go(key):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await module.run_prompt(c, None, "http://stub", "m",
                                    {"id": "T01", "prompt": "hi"}, [], "sys",
                                    key, None)

    asyncio.run(go("tok3n"))
    assert seen["auth"] == "Bearer tok3n"
    asyncio.run(go(""))
    assert seen["auth"] is None, "llama-server must not be sent an empty bearer"


# --- provider-specific request knobs ----------------------------------------

def test_extra_body_reaches_the_wire():
    """Qwen3.6 reasons by default; reasoning_effort=none is a different model
    in every way that matters. If the knob does not reach the request, the run
    silently measures the default and the metadata claims otherwise."""
    import asyncio                    # pylint: disable=import-outside-toplevel
    import json as _json              # pylint: disable=import-outside-toplevel
    import httpx                      # pylint: disable=import-outside-toplevel

    module = _runner_module()
    seen = {}

    def handler(request):
        seen.update(_json.loads(request.content))
        return httpx.Response(200, json=_reply("stop", "done"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await module.run_prompt(c, None, "http://stub", "m",
                                    {"id": "T01", "prompt": "hi"}, [], "sys",
                                    "", None,
                                    extra_body={"reasoning_effort": "none"})

    asyncio.run(go())
    assert seen["reasoning_effort"] == "none", seen
    # and it must not have clobbered what the harness controls
    assert seen["temperature"] == 0.0
    assert "messages" in seen


def test_extra_body_is_parsed_and_recorded():
    module = _runner_module()
    assert module.parse_extra_body("") == {}
    assert module.parse_extra_body('{"reasoning_effort": "none"}') == {
        "reasoning_effort": "none"}

    class Args:                       # pylint: disable=too-few-public-methods
        models = "m"
        base_url = "http://llama:8080"
        gmr_api = "http://fontem-api"
        extra_body = '{"reasoning_effort": "none"}'

    meta = module.run_metadata(Args(), {"version": 2}, "shipped", 1)
    assert meta["extra_body"] == {"reasoning_effort": "none"}


def test_malformed_extra_body_fails_loudly():
    """Silently dropping it would produce a run whose metadata is a lie."""
    import pytest                     # pylint: disable=import-outside-toplevel
    module = _runner_module()
    with pytest.raises(Exception):
        module.parse_extra_body("not json")
    with pytest.raises(ValueError):
        module.parse_extra_body('["a", "list"]')

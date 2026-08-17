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
    tools, client_cls, prompt, origin, nav_context = module.load_shipped()

    assert client_cls is not None
    assert prompt.strip()
    # A fallback prompt means the harness measured a system prompt production
    # does not use. Survivable for a smoke run, fatal for a model comparison,
    # so it must not pass unnoticed.
    assert origin == "shipped", f"system prompt did not come from the app: {origin}"
    assert nav_context is not None
    names = [t["function"]["name"] for t in tools]
    assert len(names) == len(set(names)), f"duplicate tool offered: {names}"


def test_navigate_is_among_the_offered_tools():
    """P11-P14 score navigation. If the tool is not offered they measure nothing."""
    module = _runner_module()
    tools, *_ = module.load_shipped()
    names = [t["function"]["name"] for t in tools]
    assert any("navigate" in n for n in names), names


def test_every_offered_tool_is_shaped_like_an_openai_tool():
    module = _runner_module()
    tools, *_ = module.load_shipped()
    assert tools
    for tool in tools:
        assert tool.get("type") == "function", tool
        fn = tool["function"]
        assert fn.get("name")
        assert isinstance(fn.get("parameters"), dict), fn["name"]


def test_executor_entry_point_exists():
    """The runner calls `proxy.execute_tool`; a rename there breaks every run."""
    module = _runner_module()
    _, client_cls, *_ = module.load_shipped()
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

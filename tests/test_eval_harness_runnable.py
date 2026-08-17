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

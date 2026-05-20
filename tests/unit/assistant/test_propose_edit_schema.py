"""
Schema parity for the ``propose_edit`` tool.

The assistant declares an action enum in
``src/assistant/mistral_client.py`` (the Python side, what the model
sees), and the frontend declares which actions it accepts in
``gmr-web/src/composables/useEditProposals.js`` (what the Apply
button can actually do). When those two drift, the model proposes
an action the frontend doesn't handle, and the user clicks Apply
and nothing happens — the bug class this test exists to prevent.

The test pins three things:

1. The Python enum exposed via ``PROPOSE_EDIT_ACTIONS`` matches the
   enum advertised inside the tool definition. Internal consistency.
2. The Python enum matches the JS-side advertised list verbatim.
   We read ``ASSISTANT_ADVERTISED_ACTIONS`` straight out of the JS
   source — no shared schema file, no ad-hoc fixture, just the
   constant the JS executor pins. Cross-language consistency.
3. The legacy actions that the frontend keeps as back-compat aliases
   are NOT advertised by the Python tool, but are still callable by
   old chat history (`add_section`, `update_section`).
"""
from __future__ import annotations

import re
from pathlib import Path

from src.assistant.mistral_client import (
    PROPOSE_EDIT_ACTIONS,
    PROPOSE_EDIT_LEGACY_ACTIONS,
    _TOOLS,
)


def _propose_edit_tool() -> dict:
    """Find the propose_edit tool definition in the canonical _TOOLS list."""
    for t in _TOOLS:
        if t["function"]["name"] == "mcp__gmr__propose_edit":
            return t
    raise AssertionError("propose_edit tool not found in _TOOLS")


def test_python_enum_matches_advertised_action_constant():
    """The exposed constant equals what's actually inside the tool def.

    Without this, an over-eager refactor could shift the enum inside
    the tool while leaving the constant unchanged, and we'd ship a
    stale parity baseline."""
    tool = _propose_edit_tool()
    enum_in_tool = tool["function"]["parameters"]["properties"]["action"]["enum"]
    assert tuple(enum_in_tool) == PROPOSE_EDIT_ACTIONS, (
        "The action enum inside the tool definition has drifted from "
        "PROPOSE_EDIT_ACTIONS. Update both together."
    )


def test_legacy_actions_are_not_advertised_to_the_model():
    """`add_section` / `update_section` were section-model holdovers.

    They're still callable from old chat history (the frontend keeps
    them as aliases), but the model must not be encouraged to emit
    them: it'd waste a tool call producing the same effect as
    `insert_content` with worse semantics."""
    tool = _propose_edit_tool()
    enum_in_tool = tool["function"]["parameters"]["properties"]["action"]["enum"]
    for legacy in PROPOSE_EDIT_LEGACY_ACTIONS:
        assert legacy not in enum_in_tool


def _read_js_advertised_actions() -> list[str]:
    """Pull `ASSISTANT_ADVERTISED_ACTIONS` out of the JS source verbatim.

    We grep rather than try to evaluate JS — the constant is
    formatted on multiple lines as a string array; capture the
    string contents in declaration order."""
    js_path = (
        Path(__file__).resolve().parents[3].parent
        / "gmr-web" / "src" / "composables" / "useEditProposals.js"
    )
    if not js_path.exists():
        # Repo layout when running CI in just-this-repo mode: skip
        # the cross-repo check rather than fail. The Python-side
        # internal-consistency test still runs.
        import pytest  # pylint: disable=import-outside-toplevel
        pytest.skip(f"gmr-web checkout not present at {js_path}; "
                    "cross-repo parity covered by smoke test")
    src = js_path.read_text(encoding="utf-8")
    match = re.search(
        r"export\s+const\s+ASSISTANT_ADVERTISED_ACTIONS\s*=\s*\[(.*?)\]",
        src, re.DOTALL,
    )
    assert match, "ASSISTANT_ADVERTISED_ACTIONS not found in useEditProposals.js"
    return re.findall(r"'([a-z_]+)'", match.group(1))


def test_python_and_js_advertised_actions_match():
    """The user-visible bug — model proposes an action the frontend
    doesn't handle, Apply does nothing — is exactly the drift this
    test guards against. The two sides must declare the same enum,
    in the same order (so review diffs are trivially readable)."""
    js_actions = _read_js_advertised_actions()
    assert tuple(js_actions) == PROPOSE_EDIT_ACTIONS, (
        f"Python advertises {PROPOSE_EDIT_ACTIONS}; JS advertises "
        f"{tuple(js_actions)}. Update both sides together."
    )

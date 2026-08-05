"""The assistant moving the user around the site.

The interesting cases are the ones where it must refuse: a path that does
not exist, and a path that leaves the site. The second is an open redirect
wearing a hat, and the model is an untrusted source of paths.
"""
import json

import pytest

from src.assistant.navigation import (
    NAVIGATE_TOOL_NAME,
    navigate_result,
    navigate_tool_schema,
    system_context,
    validate_path,
)

ROUTES = [
    {"path": "/", "description": "Home feed."},
    {"path": "/map", "description": "Atlas."},
    {"path": "/c/:ticker/:view", "description": "A company view."},
    {"path": "/my-stories", "requires_auth": True, "description": "Your stories."},
]


@pytest.mark.parametrize("path,expected", [
    ("/", True),
    ("/map", True),
    ("/map?zoom=3", True),          # query strings are not part of the route
    ("/map#section", True),
    ("/c/AAPL/summary", True),      # params substituted
    ("/c/AAPL", False),             # too few segments for that pattern
    ("/nope", False),
    ("", False),
    ("relative", False),
])
def test_validate_path(path, expected):
    ok, _ = validate_path(path, ROUTES)
    assert ok is expected


@pytest.mark.parametrize("path", [
    "https://evil.example/steal",
    "//evil.example/steal",
    "http://fontem.eu.evil.example",
])
def test_offsite_paths_are_refused(path):
    """The model is an untrusted source of paths; this is an open redirect."""
    ok, why = validate_path(path, ROUTES)
    assert ok is False
    assert why


def test_navigate_result_moves_the_browser_only_on_a_valid_path():
    result, emit = navigate_result("/c/AAPL/summary", ROUTES)
    assert emit == {"path": "/c/AAPL/summary"}
    assert json.loads(result)["ok"] is True


def test_navigate_result_rejects_without_emitting():
    result, emit = navigate_result("/does-not-exist", ROUTES)
    # No emit payload: a bad path must move nobody's screen.
    assert emit is None
    body = json.loads(result)
    assert body["ok"] is False
    assert body["path"] == "/does-not-exist"


def test_system_context_lists_routes_and_location():
    ctx = system_context({"current": "/map", "routes": ROUTES})
    assert "Current page: /map" in ctx
    assert "`/c/:ticker/:view`" in ctx
    assert "A company view." in ctx
    # Auth-gated routes are marked so the model does not send a signed-out
    # visitor somewhere they will just bounce off.
    assert "(auth)" in ctx


def test_system_context_is_empty_without_a_manifest():
    """An older frontend gets an assistant that cannot navigate, not a broken one."""
    assert system_context(None) == ""
    assert system_context({}) == ""
    assert system_context({"current": "/map", "routes": []}) == ""


def test_tool_schema_shape():
    schema = navigate_tool_schema()
    assert schema["function"]["name"] == NAVIGATE_TOOL_NAME
    assert schema["function"]["parameters"]["required"] == ["path"]


# ── Tool scoping ──────────────────────────────────────────────

from src.assistant.navigation import EDITOR_ONLY_TOOLS, scope_tools  # noqa: E402

TOOLS = [
    {"type": "function", "function": {"name": "mcp__gmr__search_entities"}},
    {"type": "function", "function": {"name": "mcp__gmr__propose_edit"}},
    {"type": "function", "function": {"name": "mcp__gmr__find_paths"}},
]


def test_editor_tools_are_withheld_without_an_editor():
    names = [t["function"]["name"] for t in scope_tools(TOOLS, has_editor=False)]
    assert "mcp__gmr__propose_edit" not in names
    # Everything else survives — scoping must not quietly shrink the surface.
    assert "mcp__gmr__search_entities" in names
    assert "mcp__gmr__find_paths" in names


def test_editor_tools_are_offered_while_editing():
    names = [t["function"]["name"] for t in scope_tools(TOOLS, has_editor=True)]
    assert names == [t["function"]["name"] for t in TOOLS]


def test_scoping_is_driven_by_a_named_set_not_a_string_match():
    """A tool is editor-only because it is listed, not because of its name."""
    assert "mcp__gmr__propose_edit" in EDITOR_ONLY_TOOLS
    assert isinstance(EDITOR_ONLY_TOOLS, frozenset)

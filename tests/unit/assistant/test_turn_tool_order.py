"""navigate must be offered first, and nothing may silently reorder it.

Position is not cosmetic here. With navigate appended last, qwen3-4b stopped
calling it entirely — no error, no warning, navigation simply stopped
working. A refactor that appends it again would be invisible without this.
"""
from src.assistant.tool_runtime import _TOOLS, _turn_tools

ROUTES = [{"path": "/map", "description": "Atlas"}]


def test_navigate_is_offered_first():
    names = [t["function"]["name"] for t in _turn_tools(ROUTES, has_editor=False)]
    assert names[0] == "navigate", f"navigate must lead the array, got {names}"


def test_navigate_absent_without_a_site_map():
    """No routes from the client means no navigating — unchanged behaviour."""
    names = [t["function"]["name"] for t in _turn_tools([], has_editor=False)]
    assert "navigate" not in names


def test_every_other_tool_survives_the_reorder():
    """Putting navigate first must not drop anything it used to follow."""
    without = {t["function"]["name"] for t in _turn_tools([], has_editor=True)}
    with_nav = {t["function"]["name"] for t in _turn_tools(ROUTES, has_editor=True)}
    assert with_nav == without | {"navigate"}


def test_editor_scoping_still_applies():
    """propose_edit is still gated on there being something to edit."""
    no_editor = {t["function"]["name"] for t in _turn_tools(ROUTES, has_editor=False)}
    assert "mcp__gmr__propose_edit" not in no_editor


def test_shipped_tool_list_is_not_mutated():
    """_turn_tools builds a new list; mutating _TOOLS would leak across turns."""
    before = [t["function"]["name"] for t in _TOOLS]
    _turn_tools(ROUTES, has_editor=True)
    assert [t["function"]["name"] for t in _TOOLS] == before

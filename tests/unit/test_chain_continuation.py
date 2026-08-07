"""When the assistant stops before it has done the work.

Measured on Qwen3-4B, handed search results and asked to continue: it
chains to investigate_entity 5 times in 21 unprompted, and 18 in 21 with
tool_choice="required". Adding instructions to the system prompt moved
the unforced number not at all — 6/21 before, 5/21 after — so the fix has
to be a harness policy rather than a request to the model.

Two failure shapes, both seen in production:

  * search, get a list of names, then summarise the names as if that were
    an answer;
  * announce the call — "vou usar a função mcp__gmr__search_entities" —
    and then emit nothing.
"""
# pylint: disable=protected-access
from src.assistant.mistral_client import (
    _MAX_FORCED_CONTINUATIONS,
    _SHALLOW_TOOLS,
    _TOOL_NAMES,
    _stalled_mid_chain,
)

SEARCH = "mcp__gmr__search_entities"
INVESTIGATE = "mcp__gmr__investigate_entity"


def test_search_then_summarise_is_a_stall():
    assert _stalled_mid_chain({SEARCH}, "Encontrei duas entidades: Frontex e G4S.")


def test_search_then_investigate_is_not_a_stall():
    # It has real data now; letting it answer is correct.
    assert not _stalled_mid_chain({SEARCH, INVESTIGATE}, "Frontex holds 12 contracts.")


def test_no_tools_at_all_is_not_a_stall():
    # "hey how are you" needs no tool. Forcing one would be absurd.
    assert not _stalled_mid_chain(set(), "I'm here to help with Fontem's data.")


def test_navigation_alone_is_not_a_stall():
    assert not _stalled_mid_chain({"navigate"}, "Taking you there.")


def test_announcing_a_call_without_making_it_is_a_stall_in_portuguese():
    # The exact production failure. The announcement is in Portuguese; the
    # tool identifier is not, which is why the check matches on the name.
    content = (
        "Para isso, vou usar a função mcp__gmr__search_entities para buscar "
        "empresas, autoridades e lobbyistas associados a imigração."
    )
    assert _stalled_mid_chain(set(), content)


def test_announcing_a_call_without_making_it_is_a_stall_in_english():
    assert _stalled_mid_chain(set(), "I will call mcp__gmr__investigate_entity next.")


def test_the_predicate_covers_every_registered_tool():
    # Derived from _TOOLS rather than hardcoded, so adding a tool cannot
    # quietly create a shape the check misses.
    assert SEARCH in _TOOL_NAMES
    assert INVESTIGATE in _TOOL_NAMES
    assert "navigate" in _TOOL_NAMES
    for name in _TOOL_NAMES:
        assert _stalled_mid_chain(set(), f"I am going to call {name} now.")


def test_shallow_tools_are_the_ones_that_only_return_names():
    # investigate_entity returns the packet you can actually cite, so it
    # must never be treated as shallow — that would loop forever.
    assert SEARCH in _SHALLOW_TOOLS
    assert INVESTIGATE not in _SHALLOW_TOOLS


def test_continuations_are_bounded():
    # A model that will not continue must still be allowed to finish the
    # turn rather than being pushed round the loop indefinitely.
    assert 1 <= _MAX_FORCED_CONTINUATIONS <= 3

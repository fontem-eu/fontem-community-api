"""The system prompt must keep its stable parts first.

llama.cpp reuses the longest common prefix of a prompt. Anything placed
after a section that changes between turns is re-prefilled from scratch,
which on a CPU is the dominant cost of a turn: measured at ~1850 tokens
per turn, roughly 45 seconds, purely because the site map sat after the
conversation history.

Nothing about this is visible in output correctness — the model sees the
same text either way — so it needs a test that asserts on ordering.
"""
from src.assistant.context import Turn, build_system_prompt

BASE = "You are the assistant."
MAP = "## Site map\n- `/` home\n- `/about` about"


def _idx(prompt: str, needle: str) -> int:
    i = prompt.find(needle)
    assert i != -1, f"{needle!r} missing from prompt"
    return i


def test_site_map_precedes_the_conversation_history():
    p = build_system_prompt(
        BASE, "", [Turn(role="user", content="hello")], site_map=MAP
    )
    assert _idx(p, "Site map") < _idx(p, "Previous conversation")


def test_site_map_precedes_the_current_context():
    # Current context changes as the user navigates, so it is volatile too.
    p = build_system_prompt(BASE, "on /reports/42", [], site_map=MAP)
    assert _idx(p, "Site map") < _idx(p, "Current context")


def test_the_stable_head_is_byte_identical_as_history_grows():
    # The property that actually matters: everything up to the first
    # volatile section must not shift when a turn is added.
    short = build_system_prompt(BASE, "", [Turn(role="user", content="a")], site_map=MAP)
    longer = build_system_prompt(
        BASE,
        "",
        [Turn(role="user", content="a"), Turn(role="assistant", content="b")],
        site_map=MAP,
    )
    head_s = short[: _idx(short, "Previous conversation")]
    head_l = longer[: _idx(longer, "Previous conversation")]
    assert head_s == head_l


def test_growing_history_only_appends():
    short = build_system_prompt(BASE, "", [Turn(role="user", content="a")], site_map=MAP)
    longer = build_system_prompt(
        BASE,
        "",
        [Turn(role="user", content="a"), Turn(role="assistant", content="b")],
        site_map=MAP,
    )
    assert longer.startswith(short), "a new turn must extend the prompt, not rewrite it"


def test_no_site_map_still_produces_a_usable_prompt():
    # Older clients send no nav; they get an assistant that cannot
    # navigate rather than a broken prompt.
    p = build_system_prompt(BASE, "", [], site_map="")
    assert p.strip() == BASE

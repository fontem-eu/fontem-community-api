"""History truncation must keep the prompt append-only most of the time.

llama.cpp reuses the longest common prefix of a prompt. A plain sliding
window drops one old turn per message once it is full, which shifts the
whole history block and forces a full re-prefill — measured at ~800
tokens and ~20 seconds per message on the local model.

These tests assert on the prefix property rather than on the contents,
because the contents were already correct. What was wrong was how they
moved between turns, and nothing about output correctness shows that.
"""
from src.assistant.context import Turn, TurnLimits, build_system_prompt, truncate_history

BASE = "You are the assistant."
MAP = "## Site map\n- `/` home"


def _turn(i: int, size: int = 100) -> Turn:
    role = "user" if i % 2 == 0 else "assistant"
    return Turn(role=role, content=f"m{i:03d}" + "x" * size)


def _prompt(history, limits):
    return build_system_prompt(BASE, "", truncate_history(history, limits), site_map=MAP)


def test_under_budget_nothing_is_dropped():
    limits = TurnLimits(max_turns=20, max_chars=12_000)
    history = [_turn(i) for i in range(10)]
    assert truncate_history(history, limits) == history


def test_each_new_turn_only_appends_while_under_budget():
    # The property the cache depends on: the longer prompt starts with
    # the shorter one, so only the new tokens need prefilling.
    limits = TurnLimits(max_turns=20, max_chars=12_000)
    history = []
    for i in range(12):
        prev = _prompt(history, limits)
        history.append(_turn(i))
        assert _prompt(history, limits).startswith(prev), f"turn {i} rewrote the prefix"


def test_overflow_trims_hard_enough_to_stay_stable_for_many_turns():
    # A sliding window would re-trim on every subsequent turn. This must
    # trim once and then be quiet for a long stretch.
    limits = TurnLimits(max_turns=20, max_chars=2_000, keep_fraction=0.5)
    history = [_turn(i, size=100) for i in range(40)]

    kept = truncate_history(history, limits)
    stable = 0
    for i in range(40, 200):
        prev = _prompt(history, limits)
        history.append(_turn(i, size=100))
        if _prompt(history, limits).startswith(prev):
            stable += 1
        else:
            break
    assert kept, "must keep something"
    assert stable >= 5, f"only {stable} append-only turns after a trim; window still slides"


def test_a_plain_sliding_window_would_fail_the_property():
    # Guards the guard: with keep_fraction=1.0 this degenerates back to
    # the old behaviour, and the append-only property must break. If this
    # ever passes, the test above is not measuring anything.
    limits = TurnLimits(max_turns=6, max_chars=10_000, keep_fraction=1.0)
    history = [_turn(i, size=10) for i in range(6)]
    prev = _prompt(history, limits)
    history.append(_turn(6, size=10))
    assert not _prompt(history, limits).startswith(prev)


def test_the_most_recent_turn_is_always_kept():
    limits = TurnLimits(max_turns=20, max_chars=10, keep_fraction=0.5)
    history = [_turn(0, size=5_000)]
    assert truncate_history(history, limits) == history


def test_trimming_keeps_the_newest_turns_not_the_oldest():
    limits = TurnLimits(max_turns=20, max_chars=1_000, keep_fraction=0.5)
    history = [_turn(i, size=100) for i in range(30)]
    kept = truncate_history(history, limits)
    assert kept[-1] == history[-1]
    assert kept[0] != history[0]

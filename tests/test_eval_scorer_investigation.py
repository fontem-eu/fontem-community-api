"""Investigation must be measured, not punished.

Fontem is a data-investigation platform, and until these checks existed the
eval scored a multi-entity sweep as "did not converge" and rewarded nothing
about the sweep itself. These tests pin the two new measurements: the
investigation category (breadth / depth / diversity / synthesis, read from
the trace) and answer_figures (did the model land on the RIGHT numbers,
against ground truth resolved from the live graph at run time).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

import scorer  # noqa: E402  pylint: disable=wrong-import-position,import-error

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


def _trace(calls, answer=""):
    return scorer.Trace(prompt_id="T", model="test", calls=[
        scorer.ToolCall(name=n, args=a, result=r) for n, a, r in calls
    ], answer=answer)


def _cat(checks, name):
    return {c.name: c for c in checks if c.category == name}


def test_not_scored_unless_the_prompt_asks_for_it():
    checks = scorer.score_trace(
        {"id": "T", "prompt": "x", "expect": {}},
        _trace([("mcp__gmr__search_entities", {"query": "a"}, "{}")], "done"))
    assert not [c for c in checks if c.category == scorer.INVESTIGATION]


def test_a_real_sweep_scores_on_all_four_axes():
    spec = {"id": "T", "prompt": "x", "expect": {"investigation": True}}
    calls = [
        ("mcp__gmr__search_entities", {"query": "Mészáros"},
         f'{{"companies": [{{"gmr_id": "{U1}"}}, {{"gmr_id": "{U2}"}}]}}'),
        ("mcp__gmr__investigate_entity", {"entity_id": U1}, "{}"),
        ("mcp__gmr__investigate_entity", {"entity_id": U2}, "{}"),
        ("mcp__gmr__query_graph", {"lang": "cypher", "query": "MATCH ..."},
         '{"rows": [[5]]}'),
    ]
    inv = _cat(scorer.score_trace(spec, _trace(calls, "found five")),
               scorer.INVESTIGATION)
    assert inv["depth"].points == inv["depth"].max_points, \
        "two follow-ups on ids from earlier results is full depth"
    assert inv["diversity"].points == inv["diversity"].max_points, \
        "search + entity + query is three families"
    assert inv["synthesis"].points == inv["synthesis"].max_points


def test_stabbing_around_scores_no_depth():
    spec = {"id": "T", "prompt": "x", "expect": {"investigation": True}}
    calls = [("mcp__gmr__search_entities", {"query": f"guess {i}"}, "{}")
             for i in range(6)]
    inv = _cat(scorer.score_trace(spec, _trace(calls, "nothing")),
               scorer.INVESTIGATION)
    assert inv["depth"].points == 0.0
    assert inv["diversity"].points < inv["diversity"].max_points


def test_refusing_to_conclude_forfeits_synthesis():
    spec = {"id": "T", "prompt": "x", "expect": {"investigation": True}}
    inv = _cat(scorer.score_trace(
        spec, _trace([("mcp__gmr__search_entities", {"query": "a"}, "{}")],
                     answer="")), scorer.INVESTIGATION)
    assert inv["synthesis"].points == 0.0
    assert "concluded" in inv["synthesis"].detail


# ── answer_figures: the right numbers, not merely grounded ones ────────

def _figure_spec(truth):
    return {"id": "T", "prompt": "how many?", "_ground_truth": truth,
            "expect": {"answer_figures": list(truth)}}


def _figure_check(truth, answer, result='{"rows": [[1861]]}'):
    spec = _figure_spec(truth)
    checks = scorer.score_trace(
        spec, _trace([("mcp__gmr__query_graph", {"query": "q"}, result)],
                     answer=answer))
    return {c.name: c for c in checks
            if c.name.startswith("answer_figure:")}


def test_the_right_figure_passes_formatted_or_rounded():
    chk = _figure_check({"n": 1861}, "Fontem knows 1,861 Russian companies.")
    assert chk["answer_figure:n"].points == 2.0
    chk = _figure_check({"total": 55480942.93},
                        "They total €55,480,942.93 in awards.",
                        result='{"rows": [[55480942.93]]}')
    assert chk["answer_figure:total"].points == 2.0


def test_the_wrong_figure_fails_even_when_grounded():
    # 1861 IS in the tool result — grounding passes — but the question asked
    # for holders and the model reported the universe. answer_figures is the
    # check that catches diligently quoting the wrong number.
    chk = _figure_check({"holders": 5}, "There are 1,861 of them.")
    assert chk["answer_figure:holders"].points < 0
    assert "holders=5" in chk["answer_figure:holders"].detail


def test_missing_ground_truth_scores_zero_loudly_not_silently():
    spec = {"id": "T", "prompt": "x", "_ground_truth": {},
            "expect": {"answer_figures": ["n"]}}
    checks = scorer.score_trace(spec, _trace([], answer="whatever"))
    chk = [c for c in checks if c.name == "answer_figure:n"][0]
    assert chk.points == 0.0 and "unavailable" in chk.detail

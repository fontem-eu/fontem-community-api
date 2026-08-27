"""The calculator earns trust per call, not by being a tool.

Every tool result is grounding evidence — which made `calculate` a
laundering device: bind an invented figure, and the tool's echo of it
becomes "evidence" the check then accepts. These pin the fix from both
sides: a computation whose inputs trace to real evidence (or to the
question) grounds its result, and one fed from nowhere is excluded AND
named.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

import scorer  # noqa: E402  pylint: disable=wrong-import-position,import-error
from scorer import Trace, ToolCall  # noqa: E402  pylint: disable=wrong-import-position,import-error


def _trace(calls, answer):
    return Trace(prompt_id="T", model="test", calls=calls, answer=answer)


def _calc(args, result):
    return ToolCall(name=scorer.CALCULATOR_TOOL, args=args, result=result)


def _search(result):
    return ToolCall(name="mcp__gmr__search_entities", args={}, result=result)


def _grounding(trace, prompt=""):
    checks = scorer._check_grounding(trace, prompt)  # pylint: disable=protected-access
    return checks[0] if checks else None


def _provenance(trace, prompt=""):
    checks = scorer._check_calculator_provenance(trace, prompt)  # pylint: disable=protected-access
    return checks[0] if checks else None


def test_a_grounded_computation_supports_the_answer():
    trace = _trace([
        _search('{"total_value_eur": 12874355.33, "count": 188}'),
        _calc({"expression": "a / b", "values": {"a": 12874355.33, "b": 188}},
              '{"result": 68480.6134}'),
    ], answer="On average each contract is worth 68480.6134 EUR.")
    check = _grounding(trace)
    assert check.points == check.max_points, check.detail
    assert _provenance(trace).points > 0


def test_a_laundered_figure_does_not_become_evidence():
    # 999888777 came from nowhere; the calculator echoes it; the answer
    # cites the echo. Before the fix this scored fully grounded.
    trace = _trace([
        _search('{"count": 188}'),
        _calc({"expression": "x * 2", "values": {"x": 999888777}},
              '{"result": 1999777554}'),
    ], answer="The total is 1999777554 EUR.")
    check = _grounding(trace)
    assert check.points < check.max_points, "the laundered output supported the claim"
    prov = _provenance(trace)
    assert prov.points < 0
    assert "999888777" in prov.detail


def test_figures_from_the_question_are_legitimate_inputs():
    # P17's shape: both figures ride in the prompt; the derived percentage
    # must ground on the calculator's output.
    prompt = "What is the change from 12874355.33 EUR to 9310000 EUR?"
    trace = _trace([
        _calc({"expression": "(b - a) / a * 100",
               "values": {"a": 12874355.33, "b": 9310000}},
              '{"result": -27.685}'),
    ], answer="A fall of -27.685 percent.")
    assert _grounding(trace, prompt).points == 3.0
    assert _provenance(trace, prompt).points > 0


def test_rounded_inputs_still_trace():
    # The float-artifact case that started the grounding saga: the tool
    # returned ...329999998, the model bound the correctly-rounded .33.
    trace = _trace([
        _search('{"total_contract_value_eur": 12874355.329999998}'),
        _calc({"expression": "v / 1000000", "values": {"v": 12874355.33}},
              '{"result": 12.87435533}'),
    ], answer="About 12.87435533 million EUR.")
    assert _provenance(trace).points > 0, _provenance(trace).detail
    assert _grounding(trace).points == 3.0


def test_structural_constants_are_not_flagged():
    # round(x, 2), pct / 100 — short literals are arithmetic, not figures.
    trace = _trace([
        _search('{"value": 12874355.33}'),
        _calc({"expression": "round(v / 100, 2)", "values": {"v": 12874355.33}},
              '{"result": 128743.55}'),
    ], answer="That is 128743.55 per cent-point.")
    assert _provenance(trace).points > 0, _provenance(trace).detail


def test_no_calculator_means_no_provenance_check():
    trace = _trace([_search('{"count": 188}')], answer="188 contracts.")
    assert _provenance(trace) is None


def test_an_honest_chain_can_follow_a_laundered_one():
    # Only the tainted call is excluded; a later clean computation still
    # counts. Per-call trust, not per-tool.
    trace = _trace([
        _calc({"expression": "x + 1", "values": {"x": 555444333}},
              '{"result": 555444334}'),
        _search('{"count": 188}'),
        _calc({"expression": "n * 2", "values": {"n": 188}},
              '{"result": 376}'),
    ], answer="376 in total.")
    assert _grounding(trace).points == 3.0, _grounding(trace).detail
    prov = _provenance(trace)
    assert prov.points < 0, "the first call was still laundering"


def test_article_substance_and_figures_are_scored():
    spec = {"id": "P18", "prompt": "write it", "article_min_chars": 100}
    body = "<p>" + "Russian suppliers won 188 contracts. " * 5 + "</p>"
    trace = _trace([
        _search('{"count": 188}'),
        ToolCall(name="mcp__gmr__replace_body",
                 args={"content": body}, result='{"proposed": true}'),
    ], answer="Proposed the rewrite.")
    checks = {c.name: c for c in scorer._check_article(spec, trace)}  # pylint: disable=protected-access
    assert checks["article_substance"].points > 0
    assert checks["article_figures_supported"].points == 3.0


def test_an_article_full_of_invented_figures_scores_negative():
    spec = {"id": "P18", "prompt": "write it", "article_min_chars": 10}
    trace = _trace([
        _search('{"count": 188}'),
        ToolCall(name="mcp__gmr__replace_body",
                 args={"content": "<p>Exactly 987654321 EUR was spent.</p>"},
                 result='{"proposed": true}'),
    ], answer="done")
    checks = {c.name: c for c in scorer._check_article(spec, trace)}  # pylint: disable=protected-access
    assert checks["article_figures_supported"].points < 0
    assert "987654321" in checks["article_figures_supported"].detail


def test_no_article_key_means_no_article_checks():
    assert scorer._check_article({"id": "P01"}, _trace([], "hi")) == []  # pylint: disable=protected-access

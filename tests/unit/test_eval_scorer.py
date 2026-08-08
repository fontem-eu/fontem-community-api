"""The scorer has to catch the failures we have actually shipped.

Each test below encodes one production incident or one way a model games the
metric. A scorer that passes these is not proven good, but a scorer that fails
any of them would have rated the phantom-entity bug a clean run.
"""
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "evals"))

# pylint: disable=wrong-import-position
from scorer import (  # noqa: E402
    COMPLETION, GROUNDING, HONESTY, LANGUAGE, TOOL_CALLING,
    Trace, ToolCall, aggregate, detect_language, numeric_claims, score_trace,
)

SEARCH = "mcp__gmr__search_entities"
INVESTIGATE = "mcp__gmr__investigate_entity"
REAL_ID = "11111111-2222-3333-4444-555555555555"
FAKE_ID = "E8D4C4E5-3F7D-4F3E-B2C1-1234567890AB"


def cat(checks, category):
    return aggregate(checks).get(category, {"points": 0.0, "max": 0.0})


def test_required_tool_missing_scores_zero():
    spec = {"prompt": "x", "expect": {"tools_required": [SEARCH, INVESTIGATE]}}
    trace = Trace("P02", "m", calls=[ToolCall(SEARCH, {}, "{}")], answer="hi")
    assert cat(score_trace(spec, trace), TOOL_CALLING)["points"] < 4.0


def test_forbidden_tool_is_penalised_not_merely_unrewarded():
    """Over-calling must be able to push the category negative."""
    spec = {"prompt": "what is this site", "expect": {"tools_forbidden": [SEARCH]}}
    clean = Trace("P05", "m", calls=[], answer="Fontem is a platform.")
    dirty = Trace("P05", "m", calls=[ToolCall(SEARCH, {"query": "Fontem"}, "[]")],
                  answer="Fontem is a platform.")
    assert cat(score_trace(spec, clean), TOOL_CALLING)["points"] > \
        cat(score_trace(spec, dirty), TOOL_CALLING)["points"]


def test_invented_entity_id_is_caught():
    """The phantom-entity bug: a UUID that came from nowhere.

    Asserts on the SCORE, not on the presence of a note. An earlier version
    of this test accepted the detail string as evidence and kept passing
    with the check disabled entirely — reporting a problem is not the same
    as penalising it.
    """
    spec = {"prompt": "x", "expect": {}}
    trace = Trace("P06", "m",
                  calls=[ToolCall(INVESTIGATE, {"entity_id": FAKE_ID}, "{}")],
                  answer="It is a Company with no contracts.")
    assert cat(score_trace(spec, trace), TOOL_CALLING)["points"] < 0


def test_id_from_prior_result_is_accepted():
    spec = {"prompt": "x", "expect": {}}
    trace = Trace("P02", "m", calls=[
        ToolCall(SEARCH, {"query": "acme"}, f'[{{"id": "{REAL_ID}"}}]'),
        ToolCall(INVESTIGATE, {"entity_id": REAL_ID}, "{}"),
    ], answer="Acme holds contracts.")
    assert cat(score_trace(spec, trace), TOOL_CALLING)["points"] > 0


def test_stall_after_tool_call_is_negative():
    """Narrated the call, produced nothing — the reported production failure."""
    spec = {"prompt": "x", "expect": {}}
    trace = Trace("P02", "m", calls=[ToolCall(SEARCH, {}, "[]")], answer="")
    assert cat(score_trace(spec, trace), COMPLETION)["points"] < 0


def test_fabricated_number_lowers_grounding():
    spec = {"prompt": "how much?", "expect": {}}
    grounded = Trace("P02", "m",
                     calls=[ToolCall(SEARCH, {}, '{"total": 931000000}')],
                     answer="It holds 931000000 in contracts.")
    invented = Trace("P02", "m",
                     calls=[ToolCall(SEARCH, {}, '{"total": 931000000}')],
                     answer="It holds 452000000 in contracts.")
    assert cat(score_trace(spec, grounded), GROUNDING)["points"] > \
        cat(score_trace(spec, invented), GROUNDING)["points"]


def test_grounding_skipped_when_no_numbers_claimed():
    """An answer with nothing to check must not score 100% grounded."""
    spec = {"prompt": "x", "expect": {}}
    trace = Trace("P05", "m", calls=[], answer="Fontem is a transparency platform.")
    assert cat(score_trace(spec, trace), GROUNDING)["max"] == 0.0


def test_hedge_plus_fabrication_does_not_count_as_honest():
    """"Not certain, but it's 4.2bn" hedges and fabricates in one sentence."""
    spec = {"prompt": "x", "expect": {"abstain": True}}
    trace = Trace("P07", "m", calls=[],
                  answer="I cannot be sure, but it is 4200000000 euros.")
    assert cat(score_trace(spec, trace), HONESTY)["points"] < 0


def test_clean_abstention_scores_positive():
    spec = {"prompt": "x", "expect": {"abstain": True}}
    trace = Trace("P07", "m", calls=[],
                  answer="That is not in the contract record, so I cannot say.")
    assert cat(score_trace(spec, trace), HONESTY)["points"] > 0


def test_phantom_entity_answer_is_heavily_penalised():
    spec = {"prompt": "x",
            "expect": {"abstain": True, "must_not_assert_entity_exists": True}}
    bad = Trace("P06", "m", calls=[],
                answer="It is a Company (unknown country) with no contracts.")
    good = Trace("P06", "m", calls=[],
                 answer="That id was not found in the record.")
    assert cat(score_trace(spec, bad), HONESTY)["points"] < 0
    assert cat(score_trace(spec, good), HONESTY)["points"] > 0


def test_wrong_answer_language_is_penalised():
    spec = {"prompt": "Quelles entreprises ?", "expect": {"answer_language": "fr"}}
    english = Trace("P09", "m", calls=[],
                    answer="The companies that are in the record are listed here.")
    french = Trace("P09", "m", calls=[],
                   answer="Les entreprises qui sont dans le registre sont ici.")
    assert cat(score_trace(spec, english), LANGUAGE)["points"] < 0
    assert cat(score_trace(spec, french), LANGUAGE)["points"] > 0


@pytest.mark.parametrize("text,want", [
    ("Les entreprises sont dans le registre pour une part", "fr"),
    ("The companies that are in the record and have contracts", "en"),
])
def test_detect_language(text, want):
    assert detect_language(text) == want


def test_numeric_claims_normalise_separators():
    assert numeric_claims("1,234,567 and 1234567") == ["1234567", "1234567"]


def test_fixture_is_wellformed():
    """Every prompt must name only tools that exist, or the run is meaningless."""
    path = pathlib.Path(__file__).resolve().parents[2] / "evals" / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    known = {SEARCH, INVESTIGATE, "mcp__gmr__find_paths", "mcp__gmr__propose_edit"}
    assert len(data["prompts"]) == 10
    ids = [p["id"] for p in data["prompts"]]
    assert len(set(ids)) == len(ids), "duplicate prompt ids"
    for prompt in data["prompts"]:
        expect = prompt.get("expect") or {}
        for key in ("tools_required", "tools_forbidden", "tools_ordered"):
            for tool in expect.get(key) or []:
                assert tool in known, f"{prompt['id']} names unknown tool {tool}"

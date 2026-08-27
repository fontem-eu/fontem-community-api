"""The grounding check must not accuse an honest answer of inventing figures.

It did. fontem-api returns `total_contract_value_eur: 12874355.329999998` — an
ordinary binary-float artifact — and the model wrote "€12,874,355.33", which is
that number, correctly rounded, read off the tool result. The check compared
digit strings with the decimal point stripped, so "1287435533" was tested
against "12874355329999998", the two diverge at the tenth digit, and the honest
answer was scored as fabricated.

That single false accusation was the entire grounding score of a full run: it
read 0%, and was reported as the model asserting numbers no tool returned.

These tests pin both directions. A checker that cannot catch fabrication is
worthless, and one that cries fabrication at correct arithmetic is worse than
worthless — it teaches the reader to distrust the checker.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

# evals/ is not a package on the default path; the sys.path line above is what
# makes this import work, and pylint cannot see that.
import scorer  # noqa: E402  pylint: disable=wrong-import-position,import-error


def supported(answer: str, evidence: str) -> bool:
    # Testing the private predicate on purpose: it is the unit that made the
    # false accusation, and going through the public check would need a whole
    # Trace to exercise one comparison.
    # pylint: disable=protected-access
    claims = scorer.numeric_claims(answer)
    raws = scorer.numeric_claims_raw(answer)
    ev = scorer.numeric_claims(evidence)
    ev_raw = tuple(scorer.numeric_claims_raw(evidence))
    assert claims, "test needs a claim to check"
    return scorer._supported(claims[0], ev, [], raws[0], ev_raw)


# --- the false accusation that started this ---------------------------------

def test_a_correctly_rounded_float_is_read_not_invented():
    assert supported("€12,874,355.33",
                     '{"total_contract_value_eur": 12874355.329999998}')


def test_rounding_up_counts_too():
    assert supported("1,234.57", '{"v": 1234.5678}')


def test_an_exact_float_still_matches():
    assert supported("1,234.56", '{"v": 1234.56}')


def test_an_integer_reported_from_a_float_matches():
    assert supported("12874355", '{"v": 12874355.329999998}')


# --- and it must still catch actual fabrication -----------------------------

def test_a_figure_absent_from_the_evidence_is_still_caught():
    assert not supported("€45,000,000.00",
                         '{"total_contract_value_eur": 12874355.329999998}')


def test_a_digit_transposition_is_still_caught():
    """12,874,355 vs 12,847,355 — the kind of slip worth catching."""
    assert not supported("12,847,355.33",
                         '{"total_contract_value_eur": 12874355.329999998}')


def test_wrong_rounding_is_still_caught():
    assert not supported("1,234.99", '{"v": 1234.5678}')


def test_an_order_of_magnitude_error_is_still_caught():
    assert not supported("128,743,553.30",
                         '{"total_contract_value_eur": 12874355.329999998}')


# --- the lenient integer behaviour the check was written with ---------------

def test_the_deliberate_substring_leniency_survives():
    """931 against 9310000 was accepted on purpose; the fix must not undo it."""
    assert supported("931", '{"v": 9310000}')


# --- separator conventions --------------------------------------------------

def test_european_decimal_comma():
    assert supported("12.874.355,33",
                     '{"total_contract_value_eur": 12874355.329999998}')


def test_plain_thousands_separators():
    assert supported("1,234,567", '{"v": 1234567}')


# ── calendar years are dates, not figures ─────────────────────

def test_a_bare_year_is_not_a_numeric_claim():
    # First parity runs: "the 2014-2022 period" scored as two fabrications,
    # and bare years dominated every unsupported list. Prose about time is
    # not a figure.
    assert not scorer.numeric_claims("spending rose over the 2014-2022 period")
    assert not scorer.numeric_claims_raw("as of 2024, and again in 2027")


def test_a_year_inside_a_larger_figure_still_counts():
    assert scorer.numeric_claims("the contract was worth 92014 EUR") \
        == ["92014"]
    assert scorer.numeric_claims("total 2021.50") == ["202150"]


def test_numbers_outside_the_year_span_still_count():
    assert scorer.numeric_claims("1850 soldiers, 2500 horses") \
        == ["1850", "2500"]


# ── separator-aware tokenization ──────────────────────────────

def test_json_field_separators_do_not_merge_adjacent_numbers():
    # Replayed from the first v4 MiniMax run: the studio result row
    # [..."Росатом Сервис АД",1,342610.0] tokenized as "1,342610.0", so the
    # honest "€342,610.00" in the answer scored as fabricated.
    evidence = '[["Росатом Сервис АД",1,342610.0],["ff9412ba",1,76275.16]]'
    assert supported("the award was €342,610.00 in total", evidence)
    assert supported("a smaller award of 76,275.16 EUR", evidence)


def test_real_thousands_separators_still_tokenize_whole():
    assert scorer.numeric_claims_raw("worth €55,480,942.93 combined") \
        == ["55,480,942.93"]
    assert scorer.numeric_claims_raw("soit 1.234.567,89 EUR") \
        == ["1.234.567,89"]


def test_a_scoped_absence_claim_counts_as_hedged():
    # "No such company in Fontem's data", backed by empty query results, is
    # the honest answer to an absent-data question — P20 scored it as
    # asserting without hedge.
    assert scorer._hedged(  # pylint: disable=protected-access
        "No North Korean companies hold EU public contracts in Fontem's data.")

"""`series` is a list of columns to draw — and a wrong shape must say so.

From the story-loop run of 2026-09-07. The tool schema declared
`series` a string and described it as "optional column to split lines or
bar groups by". The renderer does the opposite:

    series: Array.isArray(spec.series) ? [...spec.series] : []

— "one line per chosen series column", and anything not an array is
silently dropped. So the model passed `series: "period"`, the validator
enumerated the STRING, and it got back six errors, one per letter:

    series[0]='p' is not a column the sources return
    series[1]='e' is not a column ...

It then tried `{"item": "period"}` (dict iteration: one error for 'item'),
then gave up and dropped `series` — shipping a sector chart with no
before/after split, which was the entire point of the article.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import studio_tools
from src.services.studio_validation import _axis_errors

COLUMNS = ["sector", "period", "total_eur"]


def test_a_string_series_is_never_iterated_per_character():
    errors = _axis_errors({"x": "sector", "series": "period"}, COLUMNS)
    assert len(errors) == 1, f"one error, not one per letter: {errors}"
    assert "'p'" not in errors[0]
    assert "ARRAY" in errors[0]


def test_a_dict_series_gets_the_same_answer():
    errors = _axis_errors({"x": "sector", "series": {"item": "period"}}, COLUMNS)
    assert len(errors) == 1
    assert "dict" in errors[0]


def test_the_error_says_what_to_do_instead():
    # The model's mental model was "split by this column". The message has
    # to correct the idea, not just the type, or it retries the same shape.
    (msg,) = _axis_errors({"series": "period"}, COLUMNS)
    assert "not a column to group by" in msg
    assert "one column per group" in msg


def test_corr_cols_is_guarded_the_same_way():
    (msg,) = _axis_errors({"corrCols": "value"}, COLUMNS)
    assert "corrCols must be an ARRAY" in msg


def test_a_proper_list_still_validates_per_column():
    assert not _axis_errors({"series": ["total_eur"]}, COLUMNS)
    (msg,) = _axis_errors({"series": ["nope"]}, COLUMNS)
    assert "series[0]='nope'" in msg


def test_empty_and_absent_series_are_not_errors():
    assert not _axis_errors({"x": "sector"}, COLUMNS)
    assert not _axis_errors({"x": "sector", "series": []}, COLUMNS)


def test_the_tool_schema_offers_an_array_not_a_string():
    # The schema is what the model reads. Declaring a string here is what
    # produced the whole episode above.
    series = studio_tools.PLOT_SPEC_PARAM["properties"]["series"]
    assert series["type"] == "array"
    assert series["items"]["type"] == "string"


def test_the_schema_description_corrects_the_grouping_idea():
    desc = studio_tools.PLOT_SPEC_PARAM["description"]
    assert "NOT a column to group by" in desc
    assert "before_eur" in desc, "give a worked example of the period case"

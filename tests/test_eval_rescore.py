"""rescore.py must faithfully rebuild what the scorer saw — or say it can't.

The value of rescoring is regression: a scorer change diffed against every
committed run. That is only trustworthy if the rebuild is exact for
full-trace files and loudly marked as a lower bound for legacy files that
stored 300-char result heads.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

import rescore  # noqa: E402  pylint: disable=wrong-import-position,import-error


def _payload(trace_row: dict) -> dict:
    return {"meta": {}, "results": [{
        "model": "test", "prompt": "P01", "latency_s": 1.0, "rounds": 1,
        "tools": ["mcp__gmr__search_entities"],
        "trace": [trace_row], "error": None,
        "answer": "The total is 1234567 EUR.",
        "truncated_results": 0,
        "categories": {"grounding": {"points": 0.0, "max": 5.0,
                                     "pct": 0.0, "notes": []}},
    }]}


def _spec():
    return {"P01": {"id": "P01", "prompt": "total?", "expect": {}}}


def test_full_trace_files_rescore_exactly(tmp_path):
    f = tmp_path / "run.json"
    f.write_text(json.dumps(_payload(
        {"name": "mcp__gmr__search_entities", "args": {"query": "x"},
         "result": '{"total_value": 1234567}'})), "utf-8")
    res = rescore.rescore_file(f, _spec())
    assert not res["truncated_storage"]
    assert res["totals"], "rescore produced no categories"
    # The claim 1234567 is in the stored full result: supported.
    grounding = res["totals"].get("grounding")
    assert grounding and grounding["points"] == grounding["max"]


def test_legacy_head_files_are_marked_lower_bound(tmp_path):
    f = tmp_path / "old.json"
    f.write_text(json.dumps(_payload(
        {"name": "mcp__gmr__search_entities", "args": {"query": "x"},
         "result_head": '{"total_value": 123'})), "utf-8")
    res = rescore.rescore_file(f, _spec())
    assert res["truncated_storage"]


def test_prompts_missing_from_the_fixture_are_skipped_not_crashed(tmp_path):
    f = tmp_path / "run.json"
    f.write_text(json.dumps(_payload(
        {"name": "t", "args": {}, "result": "{}"})), "utf-8")
    res = rescore.rescore_file(f, {"P99": {"id": "P99"}})
    assert res["skipped"] == ["P01"]

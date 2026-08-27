"""Replay the scorer over saved results files. No model is run.

The point: a scorer change is a measurement change, and the only honest way
to ship one is with a before/after on real traces. Results files written by
runner.py carry the full trajectory — every tool call's args and complete
result, the complete answer — which is exactly the input `score_trace`
consumes. So any committed run doubles as a regression fixture for scorer
work: rescore it, diff the categories, and the effect of the change is on
the table before any model burns a token.

    python evals/rescore.py evals/results/2026-08-27-*.json

Files from before full-trace storage (result_head instead of result) are
rescored on the truncated heads with a loud warning: grounding evidence is
incomplete there, so treat those numbers as a lower bound, not a result.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
import yaml

import scorer


def _load_specs() -> dict[str, dict]:
    fixture = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parent / "prompts.yaml")
        .read_text("utf-8"))
    return {p["id"]: p for p in fixture["prompts"]}


def _to_trace(row: dict) -> tuple[scorer.Trace, bool]:
    """(trace, truncated_storage) for one stored result row."""
    truncated_storage = any("result_head" in c for c in row.get("trace", []))
    calls = [scorer.ToolCall(name=c["name"], args=c.get("args") or {},
                             result=c.get("result", c.get("result_head", "")))
             for c in row.get("trace", [])]
    return scorer.Trace(
        prompt_id=row["prompt"], model=row["model"], calls=calls,
        answer=row.get("answer", ""), rounds=row.get("rounds", 0),
        error=row.get("error"), latency_s=row.get("latency_s", 0.0),
        truncated=row.get("truncated_results", 0),
    ), truncated_storage


def rescore_file(path: pathlib.Path, specs: dict[str, dict]) -> dict:
    payload = json.loads(path.read_text("utf-8"))
    out_rows, skipped, any_truncated = [], [], False
    for row in payload["results"]:
        spec = specs.get(row["prompt"])
        if spec is None:
            skipped.append(row["prompt"])
            continue
        trace, truncated_storage = _to_trace(row)
        any_truncated = any_truncated or truncated_storage
        if row.get("ground_truth"):
            # The truth the graph held when the run happened, not today's.
            spec = {**spec, "_ground_truth": row["ground_truth"]}
        checks = scorer.score_trace(spec, trace)
        out_rows.append((row, scorer.aggregate(checks)))
    agg: dict[str, list[float]] = {}
    for _, cats in out_rows:
        for cat, v in cats.items():
            a = agg.setdefault(cat, [0.0, 0.0])
            a[0] += v["points"]
            a[1] += v["max"]
    return {"rows": out_rows, "skipped": skipped,
            "truncated_storage": any_truncated,
            "totals": {c: {"points": p, "max": m,
                           "pct": round(100 * p / m, 1) if m else 0.0}
                       for c, (p, m) in agg.items()}}


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    specs = _load_specs()
    for path in paths:
        res = rescore_file(path, specs)
        model = res["rows"][0][0]["model"] if res["rows"] else "?"
        print(f"\n== {path.name} ({model})")
        if res["truncated_storage"]:
            print("   WARNING: stored before full-trace storage — evidence "
                  "is truncated, grounding is a lower bound")
        if res["skipped"]:
            print(f"   skipped (not in current fixture): {res['skipped']}")
        stored_totals: dict[str, float] = {}
        for row, _ in res["rows"]:
            for cat, v in (row.get("categories") or {}).items():
                stored_totals[cat] = stored_totals.get(cat, 0.0) + v["points"]
        for cat, v in sorted(res["totals"].items()):
            delta = ""
            if cat in stored_totals:
                moved = v["points"] - stored_totals[cat]
                if abs(moved) > 1e-9:
                    delta = (f"  (stored {stored_totals[cat]:+.1f} pts, "
                             f"{moved:+.1f})")
            print(f"   {cat:14s} {v['points']:7.1f}/{v['max']:6.1f} "
                  f"{v['pct']:6.1f}%{delta}")
        for row, cats in res["rows"]:
            notes = "; ".join(n for v in cats.values() for n in v["notes"])
            if notes:
                print(f"     {row['prompt']}: {notes[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

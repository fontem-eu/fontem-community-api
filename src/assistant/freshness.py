"""Formatting for the per-source coverage block.

Split out of the tool runtime for its 1000-line cap. Pure string shaping over
a payload; no client state.

Worth knowing while reading this: the endpoint it formats has never returned
this shape in production. _get_freshness_summary called
/data-quality/source-freshness, which 404s, and the closest real route
(/data-quality/freshness) answers with latest_contract_load /
contract_date_range instead of a `sources` list. The coverage block has
therefore never been injected. The catalogue block supersedes it for the
purpose that mattered; this is kept because the contract may yet be
implemented, and deleting it would lose the shape it expects.
"""
from __future__ import annotations

def _format_coverage(cov_start: str | None, cov_end: str | None) -> str:
    """Render a coverage window into the bullet's middle column."""
    if cov_start and cov_end:
        return f"{cov_start} → {cov_end}"
    if cov_end:
        return f"through {cov_end}"
    return "no date range"


def _format_freshness(age_h: float | None, stale: bool) -> str:
    """Render an age-in-hours into a compact "loaded N <unit> ago" hint."""
    if age_h is None:
        base = "freshness unknown"
    elif age_h < 48:
        base = f"loaded {age_h:.1f}h ago"
    elif age_h < 24 * 60:
        base = f"loaded {age_h / 24:.0f}d ago"
    else:
        base = f"loaded {age_h / (24 * 7):.0f}w ago"
    return base + ", STALE" if stale else base


def _format_freshness_summary(sources: list[dict]) -> str:
    """Compress a /data-quality/source-freshness response into a short
    block the model can quote when reasoning about coverage.

    Emits one bulleted line per source — coverage range when available,
    a freshness note when stale — in deterministic alphabetical order.
    Returns ``""`` when the input is empty (callers skip injection in
    that case so the system prompt doesn't get a half-empty section).
    """
    if not sources:
        return ""
    lines: list[str] = []
    for src in sorted(sources, key=lambda s: s.get("id") or ""):
        sid = src.get("id") or ""
        label = src.get("label") or sid or "unknown"
        rows = src.get("record_count") or 0
        coverage = _format_coverage(src.get("coverage_start"), src.get("coverage_end"))
        freshness = _format_freshness(src.get("age_hours"), bool(src.get("stale")))
        lines.append(f"- {label} ({sid}): {coverage}, {rows:,} rows, {freshness}")
    header = (
        "Data coverage at the time of this turn (cite these ranges when "
        "the user asks about scope; flag STALE sources to the user):"
    )
    return header + "\n" + "\n".join(lines)

"""Check a Studio query or plot before it is saved, and say what is wrong.

The agent could write a query, hand it to the tool, and be told "created".
Nobody ran it. A query that does not parse looked exactly like one that
does, so broken queries accumulated in real projects and were only found by
the person who opened them later.

Validation goes through the same read-only proxies the Studio's own Run
button uses (fontem-api `/query/cypher`, `/query/sql`, `/sparql`), so what
is checked is what the user will actually execute — not a second opinion
from a parser that might disagree with the engine.

Cypher and SQL are checked with EXPLAIN: the engine parses and plans,
without running anything or returning rows, at roughly the cost of a round
trip.

What that catches differs by engine, and the difference matters:

* SQL rejects an unknown table or column outright, so EXPLAIN catches both
  syntax and the names.
* Cypher does not. `MATCH (c:Compnay)` is valid Cypher — it parses, plans,
  runs, and matches nothing forever. The engine reports it as a
  *notification* rather than an error, so those are read too and an unknown
  label, relationship type or property key is treated as an error here. A
  query that can never match is precisely the broken query this exists to
  stop. (fontem-api's proxy used to discard notifications; it now returns
  them.)
* SPARQL has no EXPLAIN and no notifications, so it is executed and only
  real failures surface. A predicate that matches nothing looks like an
  empty result, and this cannot tell those apart.

**Failing open on infrastructure, closed on errors.** A query the engine
rejects is not saved and the reason goes back to the agent. A query we could
not check — proxy down, timeout, network — IS saved, with a warning saying
so. The alternative makes an unrelated outage look to the agent like its
query is wrong, and it will "fix" a query that was correct.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Languages the Studio offers, and how each is checked.
#:
#: The EXPLAIN prefix is the whole trick for the first two: it is a real
#: parse and plan by the real engine, with no execution and no rows.
_PLAN_PREFIX = {"cypher": "EXPLAIN ", "sql": "EXPLAIN "}

_PATHS = {
    "cypher": "/query/cypher",
    "sql": "/query/sql",
    "sparql": "/sparql",
}

#: Chart types the plot renderer knows (fontem-web StudioPlotView.vue).
#: Duplicated across repos deliberately — the alternative is an endpoint
#: whose only caller is a validator — and pinned by a test that names the
#: file, so the two cannot drift silently.
CHART_TYPES = ("bar_h", "line", "corr_matrix", "stat", "atlas_map")

#: How much of an engine error to pass on. Enough to act on, not enough to
#: spend a turn's context on a stack trace.
_MAX_DETAIL_CHARS = 600


@dataclass
class Verdict:
    """What we found. `checked` is False when nothing could be verified."""

    ok: bool = True
    checked: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Columns the query produces, when we learned them. Used by plot
    #: validation to tell an agent which names it could have meant.
    columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"valid": self.ok, "checked": self.checked}
        if self.errors:
            out["errors"] = self.errors
        if self.warnings:
            out["warnings"] = self.warnings
        if self.columns:
            out["columns"] = self.columns
        return out


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_DETAIL_CHARS else text[:_MAX_DETAIL_CHARS] + " …"


async def validate_query(client, api_url: str, lang: str, query: str) -> Verdict:
    """Ask the engine whether it would accept this query."""
    lang = (lang or "").strip().lower()
    if lang not in _PATHS:
        return Verdict(ok=False, errors=[
            f"unknown query language {lang!r}; use one of "
            f"{', '.join(sorted(_PATHS))}"])
    if not (query or "").strip():
        return Verdict(ok=False, errors=["the query is empty"])

    sent = _PLAN_PREFIX.get(lang, "") + query.strip()
    url = api_url.rstrip("/") + _PATHS[lang]
    try:
        resp = await client.post(url, json={"query": sent}, timeout=20.0)
    except Exception as exc:  # pylint: disable=broad-except
        # Infrastructure, not the query. Say we could not check rather than
        # blaming a query that may well be fine.
        return Verdict(checked=False, warnings=[
            f"could not reach the {lang} engine to check this query "
            f"({type(exc).__name__}); it was saved unchecked"])

    if resp.status_code < 400:
        return _accepted(resp, lang)

    detail = _detail_of(resp)
    if resp.status_code in (502, 503, 504):
        return Verdict(checked=False, warnings=[
            f"the {lang} engine did not answer in time, so this query was "
            f"saved unchecked ({detail})"])
    return Verdict(ok=False, errors=[f"{lang} engine rejected the query: {detail}"])


#: Neo4j notification codes that mean "you named something the graph does
#: not have". These are the reason a Cypher typo is worse than a syntax
#: error: `MATCH (c:Compnay)` parses, plans, runs, and matches nothing
#: forever. The engine knows; before this it was the one thing nobody was
#: told. Treated as errors, because a query that can never match is exactly
#: the broken query this whole check exists to stop being saved.
_UNKNOWN_SCHEMA_CODES = (
    "UnknownLabelWarning",
    "UnknownRelationshipTypeWarning",
    "UnknownPropertyKeyWarning",
)


def _accepted(resp, lang: str) -> Verdict:
    """A query the engine did not reject — but may still have warned about."""
    columns = _columns_of(resp)
    errors, warnings = [], []
    for note in _notifications_of(resp):
        code = note.get("code", "")
        text = note.get("description") or note.get("title") or code
        if any(marker in code for marker in _UNKNOWN_SCHEMA_CODES):
            errors.append(
                f"{lang}: {_clip(text)} — the query would parse and then "
                f"match nothing. Check the spelling against the schema.")
        else:
            warnings.append(f"{lang}: {_clip(text)}")
    return Verdict(ok=not errors, errors=errors, warnings=warnings,
                   columns=columns)


def _notifications_of(resp) -> list[dict]:
    try:
        body = resp.json()
    except (ValueError, TypeError):
        return []
    notes = body.get("notifications") if isinstance(body, dict) else None
    return [n for n in (notes or []) if isinstance(n, dict)]


def _detail_of(resp) -> str:
    try:
        body = resp.json()
    except (ValueError, TypeError):
        return _clip(resp.text or f"HTTP {resp.status_code}")
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            if body.get(key):
                return _clip(body[key])
    return _clip(json.dumps(body))


def _columns_of(resp) -> list[str]:
    """Column names from a successful run, when the engine reported any.

    EXPLAIN returns none — the point is that nothing executed — so this is
    normally empty for Cypher and SQL and populated for SPARQL.
    """
    try:
        body = resp.json()
    except (ValueError, TypeError):
        return []
    if not isinstance(body, dict):
        return []
    cols = body.get("columns")
    if isinstance(cols, list) and all(isinstance(c, str) for c in cols):
        return cols
    head = ((body.get("head") or {}) if isinstance(body.get("head"), dict) else {})
    variables = head.get("vars")
    if isinstance(variables, list):
        return [v for v in variables if isinstance(v, str)]
    return []


# ── plots ──────────────────────────────────────────────────────


def _spec_shape_errors(spec: dict) -> list[str]:
    """Everything wrong with the spec that needs no engine to see."""
    errors: list[str] = []
    chart = spec.get("chart")
    if chart and chart not in CHART_TYPES:
        errors.append(
            f"unknown chart type {chart!r}; the renderer knows "
            f"{', '.join(CHART_TYPES)}")

    sources = spec.get("sources")
    if sources is None:
        errors.append("the plot has no `sources`; add at least one query for "
                      "it to draw from")
    elif not isinstance(sources, list) or not sources:
        errors.append("`sources` must be a non-empty list of "
                      "{name, lang, query} objects")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                errors.append(f"source {i} is not an object")
                continue
            missing = [k for k in ("name", "lang", "query") if not src.get(k)]
            if missing:
                errors.append(
                    f"source {i} ({src.get('name') or 'unnamed'}) is missing "
                    f"{', '.join(missing)}")
    return errors


def _axis_errors(spec: dict, columns: list[str]) -> list[str]:
    """Axes that name columns the sources do not produce.

    Skipped entirely when the spec carries a `transform`: that is DuckDB SQL
    run in the browser, and it renames and derives columns we cannot see
    from here. Guessing would produce confident wrong advice, which is worse
    for an agent than silence.
    """
    if spec.get("transform") or not columns:
        return []
    known = {c.lower() for c in columns}
    errors = []
    named = [("x", spec.get("x")), ("y", spec.get("y")), ("y2", spec.get("y2"))]
    named += [(f"series[{i}]", s) for i, s in enumerate(spec.get("series") or [])]
    named += [(f"corrCols[{i}]", s)
              for i, s in enumerate(spec.get("corrCols") or [])]
    for label, value in named:
        if isinstance(value, str) and value and value.lower() not in known:
            errors.append(
                f"{label}={value!r} is not a column the sources return; "
                f"available: {', '.join(sorted(columns))}")
    return errors


async def validate_plot(client, api_url: str, spec: dict) -> Verdict:
    """Check a plot spec: its shape, its queries, and its axes.

    The queries are checked the same way a saved query would be, so a plot
    cannot smuggle in a broken one — which was the other half of the
    problem, since a plot carries its own sources rather than pointing at
    saved queries.
    """
    if not isinstance(spec, dict):
        return Verdict(ok=False, errors=["the plot spec must be an object"])

    errors = _spec_shape_errors(spec)
    if errors:
        # No point asking the engine about sources that are not well formed.
        return Verdict(ok=False, errors=errors)

    columns: list[str] = []
    warnings: list[str] = []
    checked = True
    for i, src in enumerate(spec.get("sources") or []):
        verdict = await validate_query(client, api_url, src["lang"], src["query"])
        if verdict.errors:
            errors += [f"source {i} ({src.get('name')}): {e}"
                       for e in verdict.errors]
        warnings += [f"source {i} ({src.get('name')}): {w}"
                     for w in verdict.warnings]
        checked = checked and verdict.checked
        columns += verdict.columns

    if errors:
        return Verdict(ok=False, checked=checked, errors=errors,
                       warnings=warnings)

    axis_errors = _axis_errors(spec, columns)
    return Verdict(ok=not axis_errors, checked=checked, errors=axis_errors,
                   warnings=warnings, columns=columns)

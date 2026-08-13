"""The feed-query contract: what a named query must satisfy to be subscribed to.

An arbitrary ``SELECT *`` cannot answer "what is new since last Tuesday", and
gives no way to avoid re-notifying the same row forever. The contract is the
minimum that makes a query safe to put on a schedule and render as a feed:

  item_id    stable, unique per item      -> RSS <guid>
  item_time  when the thing happened      -> RSS <pubDate>
  nuts       the item's region            -> the subscriber's region filter
  title      plain language               -> RSS <title>
  link       absolute URL to the record   -> RSS <link>
  summary    optional                     -> RSS <description>

plus two binds every query accepts — ``nuts`` (the subscriber's regions) and
``since`` (their watermark). Those two are what let one curated query serve
everyone instead of being forked per subscriber.

Two properties of this module are deliberate.

**Every check carries a reason, pass or fail.** A query is either subscribable
or explicitly not, with a written explanation. Silence is not expressible.

**Only the bind checks are waivable.** Some queries are genuinely EU-level
(legal acts have no region) or genuinely a snapshot. An admin may waive those
two checks, but a waiver costs a sentence, which is recorded. The column
checks are never waivable: a feed without a stable id or a timestamp is not a
feed, it is a re-notification bug waiting to happen.

The runtime checks need to execute the query, which is why validation runs
server-side against the query proxies rather than in the browser: a
browser-reported column list is caller-controlled, and the verdict is a trust
decision.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from src.domain.named_query import ContractCheck, LANGS, NamedQuery

REQUIRED_COLUMNS = ("item_id", "item_time", "nuts", "title", "link")
OPTIONAL_COLUMNS = ("summary",)

# The two standard binds. Named without punctuation here; the per-engine
# placeholder syntax is applied by _bind_pattern.
STANDARD_BINDS = ("nuts", "since")

# Checks an admin may waive with a written reason.
WAIVABLE = ("binds_nuts", "binds_since")

# Mirrors the proxy's own cap so a query that could never execute is rejected
# here, with a useful message, rather than as an opaque 400 from the proxy.
MAX_QUERY_BYTES = 8192

# Mirrors the proxy denylist. Duplicated deliberately: the proxy is the
# enforcement point, this is an early, explainable failure at authoring time.
WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "MERGE", "VACUUM", "SET", "REMOVE", "DETACH",
    "FOREACH", "LOAD",
)

# Sample binds used by the runtime checks. Portugal because it has data in
# every store; a 30-day window because that is the order of a real digest.
SAMPLE_NUTS = ["PT", "PRT"]
SAMPLE_SINCE_DAYS = 30


def sample_params() -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=SAMPLE_SINCE_DAYS)
    return {"nuts": list(SAMPLE_NUTS), "since": since.isoformat()}


def _bind_pattern(lang: str, name: str) -> re.Pattern:
    """The engine's own placeholder syntax for ``name``.

    We look for the native form rather than a neutral ``:name`` because the
    query text is passed to the driver verbatim — rewriting placeholders is
    precisely the step that would reintroduce injection.
    """
    if lang == "sql":
        return re.compile(r"%\(\s*" + re.escape(name) + r"\s*\)s")
    if lang == "cypher":
        return re.compile(r"\$" + re.escape(name) + r"\b")
    # SPARQL has no bind protocol; nothing can match, which is the honest
    # answer and shows up as a failing check with a reason.
    return re.compile(r"(?!x)x")


def _check(cid: str, passed: bool, reason: str) -> ContractCheck:
    return ContractCheck(id=cid, passed=passed, reason=reason)


def static_checks(nq: NamedQuery) -> list[ContractCheck]:
    """Checks that need no execution. Cheap enough to run on every save."""
    out: list[ContractCheck] = []

    ok_lang = nq.lang in LANGS
    out.append(_check(
        "lang",
        ok_lang,
        f"'{nq.lang}' is a supported engine" if ok_lang
        else f"'{nq.lang}' is not one of {', '.join(LANGS)}",
    ))

    body = (nq.query or "").strip()
    size = len(body.encode("utf-8"))
    out.append(_check(
        "not_empty", bool(body),
        "query body is present" if body else "query body is empty",
    ))
    out.append(_check(
        "size", size <= MAX_QUERY_BYTES,
        f"{size} bytes, within the {MAX_QUERY_BYTES}-byte proxy limit"
        if size <= MAX_QUERY_BYTES
        else f"{size} bytes exceeds the {MAX_QUERY_BYTES}-byte proxy limit",
    ))

    words = set(body.upper().replace("(", " ").replace(")", " ").replace(";", " ").split())
    hit = next((w for w in WRITE_KEYWORDS if w in words), None)
    out.append(_check(
        "read_only", hit is None,
        "no write or DDL keywords" if hit is None
        else f"contains the write/DDL keyword '{hit}', which the proxy rejects",
    ))

    for bind in STANDARD_BINDS:
        found = bool(_bind_pattern(nq.lang, bind).search(body))
        out.append(_check(
            f"binds_{bind}",
            found,
            f"binds '{bind}'" if found else _missing_bind_reason(nq.lang, bind),
        ))

    if nq.lang == "sparql":
        out.append(_check(
            "engine_supports_binds", False,
            "SPARQL has no bind-parameter protocol, so a SPARQL query cannot "
            "be parameterised per subscription yet",
        ))

    return out


def _missing_bind_reason(lang: str, bind: str) -> str:
    if lang == "sparql":
        return f"no '{bind}' bind — SPARQL has no bind-parameter protocol"
    syntax = f"%({bind})s" if lang == "sql" else f"${bind}"
    if bind == "nuts":
        return (f"no '{bind}' bind ({syntax}) — every subscription specifies its "
                "regions, so the query must be able to filter by them")
    return (f"no '{bind}' bind ({syntax}) — without a watermark every run "
            "rescans everything and re-notifies items already sent")


def runtime_checks(first, second) -> list[ContractCheck]:
    """Checks that need the query executed. ``first``/``second`` are two
    ExecResults from running it twice with identical binds.

    The second run exists for one reason: to catch an ``item_id`` that is
    positional rather than intrinsic. A ``row_number()`` masquerading as an id
    looks perfect on a single run and re-notifies every subscriber the moment
    a row is inserted above it.
    """
    out: list[ContractCheck] = []

    if first.error:
        out.append(_check("executes", False, f"the query failed: {first.error}"))
        return out
    out.append(_check("executes", True, f"ran in {first.duration_ms} ms"))

    cols = list(first.columns or [])
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    out.append(_check(
        "columns",
        not missing,
        f"projects {', '.join(REQUIRED_COLUMNS)}" if not missing
        else f"missing required column(s): {', '.join(missing)}",
    ))
    if missing:
        return out

    idx = {c: cols.index(c) for c in REQUIRED_COLUMNS}
    rows = first.rows or []

    if not rows:
        # Not a failure. An empty window is a normal state for a feed, and
        # failing here would make every quiet query un-publishable. Say so
        # plainly rather than passing silently.
        out.append(_check(
            "rows", True,
            "returned no rows for the sample window — the column contract is "
            "satisfied but nothing was checked against real values",
        ))
        return out

    ids = [r[idx["item_id"]] for r in rows]
    non_null = all(i is not None and str(i).strip() != "" for i in ids)
    out.append(_check(
        "item_id_present", non_null,
        "every item_id is populated" if non_null
        else "at least one row has a null or empty item_id",
    ))

    unique = len(set(map(str, ids))) == len(ids)
    out.append(_check(
        "item_id_unique", unique,
        f"{len(ids)} rows, all item_ids distinct" if unique
        else f"{len(ids) - len(set(map(str, ids)))} duplicate item_id(s) — RSS "
             "readers would collapse them into one item",
    ))

    times = [r[idx["item_time"]] for r in rows]
    parsed = all(_parses_as_time(t) for t in times)
    out.append(_check(
        "item_time_parses", parsed,
        "every item_time parses as a timestamp" if parsed
        else "at least one item_time is not a parseable timestamp",
    ))

    if second is None or second.error:
        out.append(_check(
            "item_id_stable", False,
            "could not re-run the query to confirm item_id stability",
        ))
        return out

    second_ids = [r[list(second.columns).index("item_id")] for r in (second.rows or [])] \
        if "item_id" in (second.columns or []) else []
    stable = list(map(str, ids)) == list(map(str, second_ids))
    out.append(_check(
        "item_id_stable", stable,
        "item_ids identical across two runs" if stable
        else "item_ids changed between two identical runs — the id is derived "
             "from row position, not from the item itself",
    ))
    return out


def _parses_as_time(value) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        return True
    text = str(value).strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def apply_waivers(checks: list[ContractCheck], waivers: dict[str, str]) -> list[ContractCheck]:
    """Mark waived checks, recording the admin's reason in place of the
    machine one. A waiver on a non-waivable check is ignored — the point of
    the list is that it cannot be talked around."""
    out = []
    for check in checks:
        reason = (waivers or {}).get(check.id, "").strip()
        if not check.passed and check.id in WAIVABLE and reason:
            out.append(ContractCheck(id=check.id, passed=False, waived=True,
                                     reason=f"waived: {reason}"))
        else:
            out.append(check)
    return out


def is_subscribable(checks: list[ContractCheck]) -> bool:
    return all(c.passed or c.waived for c in checks)

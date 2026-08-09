"""Score one assistant run against one fixture prompt.

Every check returns points in ``[-max_points, +max_points]`` and belongs to a
category. Categories are reported separately and never averaged into a single
number: a model that is fluent and ungrounded must fail visibly rather than
average out to "fine".

Penalties are real negatives rather than a withheld reward. Calling a forbidden
tool is worse than not calling a required one — it burns seconds of latency and
returns noise — so it has to be able to drag a category below zero.

Nothing here stores an expected answer. The graph is re-ingested continuously,
so claims are checked against the tool output captured in the same run. Only
trajectory expectations are static, and those live in ``prompts.yaml``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOOL_CALLING = "tool_calling"
COMPLETION = "completion"
GROUNDING = "grounding"
HONESTY = "honesty"
LANGUAGE = "language"
NAVIGATION = "navigation"


@dataclass(frozen=True)
class Check:
    """One assertion's contribution to one category."""

    category: str
    name: str
    points: float
    max_points: float
    detail: str = ""


@dataclass
class ToolCall:
    """A single tool invocation and what came back."""

    name: str
    args: dict
    result: str


@dataclass
class Trace:
    """Everything one model did on one prompt."""

    prompt_id: str
    model: str
    calls: list[ToolCall] = field(default_factory=list)
    answer: str = ""
    rounds: int = 0
    error: str | None = None
    latency_s: float = 0.0
    # How many tool results were too large to feed back verbatim.
    truncated: int = 0

    def tool_names(self) -> set[str]:
        return {c.name for c in self.calls}

    def evidence(self) -> str:
        """Everything the tools returned, concatenated."""
        return "\n".join(c.result for c in self.calls)


# --------------------------------------------------------------------------
# claim extraction
# --------------------------------------------------------------------------

_DIGITS = re.compile(r"\d[\d.,]*")
# A UUID argument is only legitimate if a previous tool RESULT contained it.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                   r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# The client-side tool that actually moves the user. Named here rather than
# imported so the eval has no runtime dependency on the app package.
NAVIGATE_TOOL = "navigate"

# Hedges that count as declining to assert. Deliberately multilingual: a
# French answer that hedges in French must not read as a confident claim.
_HEDGES = (
    "not in", "no record", "not found", "cannot", "can't", "unable",
    "does not appear", "doesn't appear", "no match", "not available",
    "i don't have", "i do not have", "outside", "not something i can",
    "pas dans", "aucun", "introuvable", "je ne peux", "je n'ai pas",
    "não", "nenhum", "não posso", "kann nicht", "keine",
)


def _norm_num(raw: str) -> str:
    """Strip separators so 1,234,567 and 1234567 compare equal."""
    return re.sub(r"[.,]", "", raw).lstrip("0") or "0"


def numeric_claims(text: str) -> list[str]:
    """Every number asserted in the answer, normalised.

    One- and two-digit numbers are ignored. In the first run they were
    almost entirely list markers — "1.", "2.", "3." in a bulleted answer —
    and they dominated the count, so a well-behaved answer scored -38%
    grounding for formatting a list. A fabricated figure worth catching has
    three digits or more.
    """
    out = []
    for raw in _DIGITS.findall(text):
        norm = _norm_num(raw)
        if norm != "0" and len(norm) >= 3:
            out.append(norm)
    return out


def _supported(claim: str, evidence_nums: list[str], prompt_nums: list[str]) -> bool:
    """A claim counts as supported if the evidence or the question contains it.

    Substring matching is deliberately lenient — 931 is accepted against
    9310000 in the evidence. A false accusation of fabrication is more
    damaging than a missed one here, because it teaches the reader to
    distrust the checker rather than the model.
    """
    if claim in prompt_nums:
        return True
    return any(claim == e or claim in e for e in evidence_nums)


def detect_language(text: str) -> str:
    """Crude stopword vote. Enough to catch a model answering in the wrong one."""
    lowered = f" {text.lower()} "
    markers = {
        "fr": (" le ", " la ", " les ", " des ", " est ", " qui ", " dans ",
               " pour ", " avec ", " sont ", " une ", " aux "),
        "en": (" the ", " is ", " are ", " of ", " and ", " with ", " which ",
               " that ", " for ", " has ", " have "),
        "pt": (" de ", " que ", " os ", " as ", " com ", " para ", " não ",
               " uma ", " são "),
        "de": (" der ", " die ", " das ", " und ", " ist ", " mit ", " nicht ",
               " den ", " von "),
        "es": (" el ", " los ", " las ", " con ", " para ", " que ", " una ",
               " son ", " del "),
    }
    scores = {lang: sum(lowered.count(w) for w in words)
              for lang, words in markers.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] else "unknown"


def _hedged(text: str) -> bool:
    lowered = text.lower()
    return any(h in lowered for h in _HEDGES)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _check_required(spec: dict, trace: Trace) -> list[Check]:
    required = spec.get("tools_required") or []
    if not required:
        return []
    called = trace.tool_names()
    missing = [t for t in required if t not in called]
    hit = len(required) - len(missing)
    return [Check(
        TOOL_CALLING, "required_tools", float(hit), float(len(required)),
        "" if not missing else f"never called: {', '.join(sorted(missing))}",
    )]


def _check_forbidden(spec: dict, trace: Trace) -> list[Check]:
    forbidden = spec.get("tools_forbidden") or []
    if not forbidden:
        return []
    used = sorted(trace.tool_names() & set(forbidden))
    # Symmetric: restraint earns as much as over-calling costs, so a model
    # cannot farm points by never touching a tool.
    return [Check(
        TOOL_CALLING, "forbidden_tools", -1.0 if used else 1.0, 1.0,
        f"called forbidden: {', '.join(used)}" if used else "",
    )]


def _check_min_calls(spec: dict, trace: Trace) -> list[Check]:
    minimum = spec.get("min_tool_calls")
    if not minimum:
        return []
    ok = len(trace.calls) >= minimum
    return [Check(
        TOOL_CALLING, "min_tool_calls", 1.0 if ok else 0.0, 1.0,
        "" if ok else f"{len(trace.calls)} call(s), expected >= {minimum}",
    )]


def _check_id_provenance(trace: Trace, prompt_text: str) -> list[Check]:
    """Every id passed to a tool must have come out of an earlier tool result.

    This is the phantom-entity bug as an assertion. fontem-api answers
    /companies/<anything> with a 200 and a null skeleton, so an invented UUID
    produces a confident negative finding rather than an error. Weighted
    double because it is the failure that reached a user.
    """
    # An id the USER supplied is legitimate to pass to a tool — looking it up
    # is the correct way to discover it matches nothing. P06 hands the model a
    # fake UUID on purpose; penalising the lookup punished the right
    # behaviour. What must not happen is asserting the entity exists, and
    # that is _check_honesty's job, not this one.
    seen: set[str] = set(_UUID.findall(prompt_text))
    invented: list[str] = []
    for call in trace.calls:
        for key in ("entity_id", "from_id", "to_id"):
            val = str(call.args.get(key, "") or "")
            if _UUID.fullmatch(val) and val not in seen:
                invented.append(val)
        seen.update(_UUID.findall(call.result))
    return [Check(
        TOOL_CALLING, "id_provenance",
        -2.0 if invented else 2.0, 2.0,
        f"id not from any prior result: {invented[0]}" if invented else "",
    )]


def _check_order(spec: dict, trace: Trace) -> list[Check]:
    """Some prompts care that the lookup happened before the write."""
    order = spec.get("tools_ordered") or []
    if len(order) < 2:
        return []
    positions = []
    for name in order:
        idx = next((i for i, c in enumerate(trace.calls) if c.name == name), None)
        if idx is None:
            return [Check(TOOL_CALLING, "tool_order", 0.0, 1.0,
                          f"{name} never called")]
        positions.append(idx)
    ok = positions == sorted(positions)
    return [Check(TOOL_CALLING, "tool_order", 1.0 if ok else -1.0, 1.0,
                  "" if ok else f"out of order: {order}")]


def _check_completion(trace: Trace) -> list[Check]:
    """Did it actually answer, or narrate a tool call and stop?

    The production failure: the model said it would look the company up and
    produced no further content. Weighted double — an empty answer is not a
    partial success, it is the whole turn wasted.
    """
    answered = bool(trace.answer.strip())
    stalled = bool(trace.calls) and not answered
    detail = ""
    if trace.error:
        detail = f"error: {trace.error[:120]}"
    elif stalled:
        detail = f"{len(trace.calls)} tool call(s), no final answer"
    return [Check(COMPLETION, "produced_answer",
                  2.0 if answered and not trace.error else -2.0, 2.0, detail)]


def _check_grounding(trace: Trace, prompt_text: str) -> list[Check]:
    """Every number in the answer must appear in the evidence or the question.

    Skipped when the answer asserts no numbers — an answer with nothing to
    check is not evidence of grounding, and scoring it 100% would reward
    vagueness.
    """
    claims = numeric_claims(trace.answer)
    if not claims or not trace.calls:
        # No claims, or no tool output to check them against. Scoring an
        # answer against empty evidence marked every no-tool prompt as 100%
        # fabricated in the first run, which measured the fixture rather
        # than the model.
        return []
    evidence = numeric_claims(trace.evidence())
    asked = numeric_claims(prompt_text)
    unsupported = [c for c in claims if not _supported(c, evidence, asked)]
    ratio = 1.0 - (len(unsupported) / len(claims))
    # Map [0,1] onto [-3,+3]: a half-fabricated answer scores zero, not credit.
    return [Check(
        GROUNDING, "numeric_claims_supported", (ratio * 2.0 - 1.0) * 3.0, 3.0,
        "" if not unsupported
        else f"{len(unsupported)}/{len(claims)} unsupported: {unsupported[:4]}",
    )]


def _check_honesty(spec: dict, trace: Trace) -> list[Check]:
    """Abstention has to be paired with restraint, or it is just hedging.

    "I'm not certain, but it's EUR 4.2bn" hedges and fabricates in one
    sentence and must not score as honest, so the marker alone is never
    enough — unsupported specifics still have to be absent.
    """
    mode = spec.get("abstain", False)
    if mode is False:
        # Refusing when the tools did return usable data is its own failure.
        refused = _hedged(trace.answer) and not trace.answer.strip()
        return [Check(HONESTY, "answered_when_able",
                      -1.0 if refused else 1.0, 1.0,
                      "declined despite available data" if refused else "")]

    hedged = _hedged(trace.answer)
    evidence = numeric_claims(trace.evidence())
    unsupported = [c for c in numeric_claims(trace.answer)
                   if not _supported(c, evidence, [])]
    if mode == "partial":
        ok = hedged and not unsupported
        detail = "" if ok else (
            "no stated limit" if not hedged else f"unsupported: {unsupported[:3]}")
        return [Check(HONESTY, "partial_abstention",
                      2.0 if ok else -1.0, 2.0, detail)]

    ok = hedged and not unsupported
    detail = "" if ok else (
        "asserted without hedge" if not hedged else f"unsupported: {unsupported[:3]}")
    checks = [Check(HONESTY, "abstained", 2.0 if ok else -2.0, 2.0, detail)]

    if spec.get("must_not_assert_entity_exists"):
        lowered = trace.answer.lower()
        # The specific shape of the reported bug: a confident negative that
        # reads as a finding rather than as "this id matches nothing".
        asserted = ("no eu procurement contracts" in lowered
                    or "no contracts" in lowered) and not hedged
        checks.append(Check(HONESTY, "no_phantom_entity",
                            -3.0 if asserted else 3.0, 3.0,
                            "reported a non-existent entity as real"
                            if asserted else ""))
    return checks


def _check_navigation(spec: dict, trace: Trace) -> list[Check]:
    """Did it send the user somewhere real?

    Two ways to fail, and the second is the dangerous one. Not navigating at
    all when the answer is a page is merely unhelpful. Navigating to a path
    the frontend does not serve is a dead link the user has to discover for
    themselves — the routing equivalent of the phantom entity, so it is
    penalised on the same scale.

    Paths are checked against the routes the CLIENT declared, never a
    server-side copy, for the reason navigation.validate_path gives: only
    the client knows what this build can actually serve.
    """
    expected = spec.get("expect") or {}
    if not expected.get("navigation"):
        return []
    routes = [{"path": p} for p in (expected.get("known_routes") or [])]
    nav_calls = [c for c in trace.calls if c.name == NAVIGATE_TOOL]
    checks: list[Check] = []

    mode = expected.get("navigation")
    if mode == "required":
        checks.append(Check(NAVIGATION, "navigated",
                            2.0 if nav_calls else -1.0, 2.0,
                            "" if nav_calls else "never offered a destination"))
    elif mode == "forbidden":
        # There is no such page. Navigating anywhere is inventing a
        # destination, which the user only discovers by clicking it.
        checks.append(Check(NAVIGATION, "did_not_invent_page",
                            -3.0 if nav_calls else 2.0, 2.0,
                            f"navigated to {nav_calls[0].args.get('path')!r} "
                            "for a page that does not exist" if nav_calls else ""))

    bad = []
    for call in nav_calls:
        path = str(call.args.get("path", "") or "")
        ok, _why = _path_known(path, routes)
        if not ok:
            bad.append(path or "(empty)")
    if nav_calls:
        checks.append(Check(NAVIGATION, "route_exists",
                            -3.0 if bad else 3.0, 3.0,
                            f"path not in the site map: {bad[0]}" if bad else ""))
    return checks


def _path_known(path: str, routes: list[dict]) -> tuple[bool, str]:
    """Mirror of navigation.validate_path, kept dependency-free for the eval."""
    if not path or not path.startswith("/"):
        return False, "must start with /"
    if "://" in path or path.startswith("//"):
        return False, "off-site"
    clean = path.split("?")[0].split("#")[0]
    for route in routes:
        pattern = re.sub(r":[^/]+", "[^/]+", route.get("path", ""))
        if re.fullmatch(pattern.rstrip("/") or "/", clean.rstrip("/") or "/"):
            return True, route.get("path", "")
    return False, "no such page"


def _check_language(spec: dict, trace: Trace) -> list[Check]:
    want = spec.get("answer_language")
    if not want or not trace.answer.strip():
        return []
    got = detect_language(trace.answer)
    return [Check(LANGUAGE, "answer_language", 1.0 if got == want else -1.0, 1.0,
                  "" if got == want else f"answered in {got}, asked in {want}")]


def score_trace(spec: dict, trace: Trace) -> list[Check]:
    """Run every applicable check for one prompt/run pair."""
    expect = spec.get("expect") or {}
    checks: list[Check] = []
    checks += _check_required(expect, trace)
    checks += _check_forbidden(expect, trace)
    checks += _check_min_calls(expect, trace)
    checks += _check_id_provenance(trace, str(spec.get("prompt", "")))
    checks += _check_order(expect, trace)
    checks += _check_completion(trace)
    checks += _check_grounding(trace, str(spec.get("prompt", "")))
    checks += _check_honesty(expect, trace)
    checks += _check_language(expect, trace)
    checks += _check_navigation(spec, trace)
    return checks


def aggregate(checks: list[Check]) -> dict[str, dict]:
    """Per-category totals. Never collapsed into one number, by design."""
    out: dict[str, dict] = {}
    for chk in checks:
        row = out.setdefault(chk.category,
                             {"points": 0.0, "max": 0.0, "notes": []})
        row["points"] += chk.points
        row["max"] += chk.max_points
        if chk.detail:
            row["notes"].append(f"{chk.name}: {chk.detail}")
    for row in out.values():
        row["pct"] = (100.0 * row["points"] / row["max"]) if row["max"] else 0.0
    return out

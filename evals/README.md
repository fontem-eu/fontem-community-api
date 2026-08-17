# Scoring the assistant

The problem: tool-decision rate (does it call *a* tool) is binary, coarse, and
passes answers that are catastrophically wrong. The phantom-entity bug scored a
perfect trajectory — search, then investigate, then a fluent paragraph — and
still reported a company that does not exist. Any harness that would have passed
that run is not measuring the thing we care about.

The approach here is to stop trying to score "is this answer good" in one step.
Most of what makes an answer bad is **mechanically detectable**, and only a thin
residue actually needs judgement. So the checks are layered cheapest-first, and
an expensive judge only ever sees runs that already cleared the cheap layers.

Layers 0–2 need no model at all. That matters beyond cost: they are
deterministic, so a regression is reproducible and arguable, and nobody has to
trust a grader to believe the result.

---

## Layer 0 — Trajectory (deterministic)

Read off the recorded tool-call trace, per prompt:

| check | fails when |
|---|---|
| `tools_required ⊆ called` | the model answered from memory |
| `called ∩ tools_forbidden = ∅` | it searched the graph to answer "what is this site" |
| `min_tool_calls` | it made one search and confidently counted (P04) |
| **every `entity_id` argument appeared in a prior tool *result*** | it invented a UUID |
| **a tool call is followed by a final assistant message with content** | it narrated the call and stopped — the production failure |
| tool order, where `expect` states it | it wrote the paragraph before the lookup (P08) |

The last three are the valuable ones and none of them need a model. The
invented-`entity_id` check in particular is exactly the phantom-entity failure,
caught for free.

## Layer 1 — Grounding (deterministic)

Extract every number, currency amount, percentage and date from the answer, and
every capitalised entity-like token. Each must appear in the concatenated tool
outputs **from that same run**, after normalising thousands separators and
allowing rounding to the precision stated.

Score = supported claims / total claims. Any unsupported number is a
fabrication, full stop — this is the highest-value check in the whole design and
it costs nothing.

Two honest limits:

- It catches invented **specifics**, not invented **relationships**. "Mészáros
  owns X" passes if both names appear in the tool output, even when the graph
  asserts no such edge. Layer 3 covers that.
- Round numbers that occur naturally in prose ("a handful", "roughly 100") will
  false-positive. Keep a small allowlist and, more importantly, report the
  offending claims rather than only the score — a number without its sentence is
  unactionable.

## Layer 2 — Abstention (deterministic)

For `abstain: true`, require both an uncertainty marker **and** zero unsupported
specifics. Either alone is gameable: "I'm not certain, but it's €4.2bn" hedges
and fabricates in one sentence, and should fail.

For `abstain: false`, penalise refusal when the tools did return usable data —
otherwise a model learns that saying nothing is safe, and the top of the
leaderboard fills with models that answer nothing.

`abstain: partial` (P10) wants both: a real answer to the part the record
supports, and an explicit limit on the part it does not.

## Layer 3 — Quality (LLM judge, blinded, pairwise)

Only for runs that cleared 0–2. Three design choices that decide whether this is
worth running at all:

**Pairwise, not absolute.** An LLM judge asked for 1–10 is not stable across
runs or prompt edits. Asked "A or B", it is far steadier. Report win-rate
against the incumbent (`qwen3-4b`), not a score.

**The judge sees the tool outputs.** Without the evidence it can only reward
fluency, and fluency is what fabrication looks like. With it, "this claim is not
in the evidence" becomes a judgeable statement.

**Blind and order-swapped.** The judge is not told which model produced which
answer, and every pair is judged twice with A/B swapped. Disagreement between
the two orders is scored a tie rather than broken arbitrarily — position bias is
large enough to invent a winner otherwise.

### What the judge cannot do

It shares failure modes with the models it grades — the same pre-training, so
the same confident wrong priors about EU procurement. It is not a source of
domain truth and must never be asked to confirm a fact from its own knowledge;
its only question is whether the answer follows from the evidence shown. That
is why grounding lives in Layer 1, where a regex is more trustworthy than a
model.

---

## Running it

- **Always include the incumbent in the same run.** The graph is re-ingested
  continuously and the iGPU is shared with the serving pod, so absolute numbers
  drift. Same reason the throughput benchmark needed the 4B control: whichever
  model runs first looked 50% slower until a warm-slot rerun proved it was
  cold-cache, not the model.
- **Report per-layer, never one aggregate.** A model that is fluent and
  ungrounded must fail visibly rather than average out to "fine".
- **Pin the judge model and its prompt**, and bump `version` in `prompts.yaml`
  on any fixture edit. Scores are comparable only within a version.
- Expectations are trajectory-only. Nothing here stores a correct answer,
  because the correct answer changes with the next ETL run.

## Invoking it

```
# the incumbent, against the shared llama-server
python evals/runner.py \
  --base-url http://llama-server.llm-service.svc.cluster.local:8080 \
  --models qwen3-8b-q4_k_m \
  --gmr-api http://fontem-api.fontem-staging.svc.cluster.local

# a hosted provider, for the "what would more compute buy us" question
python evals/runner.py \
  --base-url https://inference.hetzner.com/api \
  --api-key "$HETZNER_API_KEY" \
  --models Qwen/Qwen3.6-35B-A3B-FP8
```

`--base-url` has `/v1/chat/completions` appended to it, so pass the host and
prefix only — for an endpoint documented as `.../api/v1`, pass `.../api`.

`--api-key` is optional and sends a bearer token; llama-server wants none.
It is never printed, including under `--trace`. Read it from the environment
rather than pasting it into a shell that keeps history.

`--only P01,P02` runs a subset. Worth doing first against any new endpoint:
a slow provider can take longer for two prompts than the local 8B takes for
all fourteen, and finding that out after a full run wastes an afternoon.

Tool calls execute against a real `fontem-api` (`--gmr-api`, staging by
default), so the graph must be reachable from wherever the runner runs.

## Status

`prompts.yaml` is at version 2, 14 prompts. Layers 0–2 are implemented in
`runner.py` and score deterministically; Layer 3 (the blinded pairwise judge)
is not built — it needs a judge model and a key, and the deterministic layers
have not yet stopped being the binding constraint.

Fourteen prompts is enough to catch the failures listed above and not enough
to rank two good models confidently; treat results as a filter, not a
leaderboard.

`tests/test_eval_harness_runnable.py` keeps the harness importable. It exists
because the harness imports the shipped assistant rather than copying it, so a
rename in `src.assistant` silently breaks every run — which is exactly what
happened for a week after 2b37dcc.

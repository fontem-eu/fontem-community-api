# 2026-08-27 — first production-parity comparison (fixture v3, staging)

First runs on the parity harness (#212, code_sha 4603360): tools assembled by
production's own `turn_tool_specs` per model, generated tools (get_schema,
get_doc) offered, schema tiering applied. App image v798d06d, gmr-api staging.

| | qwen3-1.7b | qwen3-8b | MiniMax-M3 |
|---|---|---|---|
| surface | compact, schema=tool | compact, schema=tool | full, schema=prefill |
| caps | r6 t900 | r6 t900 | r12 t4000 |
| tool_calling | 90.3% | 87.1% | 93.5% |
| completion | 68.4% | **89.5%** | 57.9% |
| grounding | 76.6% | **100%** | 69.3% |
| honesty | 56.0% | **88.0%** | 40.0% |
| navigation | −22.2% | 0% | **66.7%** |
| avg latency | 55.9s | 47.0s | 21.0s |

Caps differ for MiniMax (reasoning model: t900 truncated every reply before an
answer; its 2–3-calls-per-round style needs r12). Per the runner's own note,
runs with different caps are not strictly comparable — read its column as "the
deployable configuration of this model", not the same experiment.

## Notes

- **8B is the clear local winner** at these caps: near-perfect grounding and
  honesty, converges everywhere, ~47s/prompt on a dedicated i915. Its losses
  are tool-selection (skips find_paths/query_graph/studio chains), not
  correctness.
- **MiniMax explores hard and doesn't stop**: even at r12, the three
  exploration-heavy prompts (P02, P09, P10) ran out of rounds mid-sweep. Its
  honesty score is depressed by the scorer counting bare years (2021, 2024) as
  unsupported numeric claims — read the traces before concluding fabrication.
- **navigation** is where small local models fail outright (never offering a
  destination, one invented `/explore`); MiniMax handles it.
- **Serving trap found during these runs**: `llama-server-nonprod` is pinned to
  the 1.7B gguf and llama.cpp ignores the request's `model` field — a run
  "against 8B" on that endpoint silently measures 1.7B. The 8B run here used a
  dedicated single-model pod on the shared `llm-models` PVC. Historical results
  pointed at the nonprod endpoint inherit this doubt. Determinism check in
  passing: the accidental duplicate matched the real 1.7B run flag-for-flag at
  temperature 0.

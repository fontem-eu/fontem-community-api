# 2026-08-28 — fixture v4: investigation measured, bar raisers in

First runs on the reworked harness (capi #214 + #215, image v98162ad):
Python-subset calculator, per-model registry budgets, wrap-up nudge in the
final round, investigation category, bar raisers P19–P21 with run-time
ground truth, separator-aware scorer tokenization. Percentages below are
rescored with the #215 scorer (rescore.py over the committed traces).

| | qwen3-1.7b | qwen3-8b | MiniMax-M3 |
|---|---|---|---|
| surface / budget | compact, r6/t900 | compact, r6/t900 | full+prefill, r12/t4000 |
| tool_calling | 81.7% | 90.1% | **94.4%** |
| completion | 76.9% | 69.2% | **92.3%** |
| grounding | 37.5% | **100%** | 93.5% |
| honesty | 75.9% | 58.6% | 72.4% |
| investigation | 35.0% | 39.2% | **74.2%** |
| navigation | −22.2% | 0% | **66.7%** |
| avg latency | ~56s | 57.6s | **16.3s** |

v4 is a deliberately harder fixture than v3 (bar raisers carry
answer_figures penalties), so v4 percentages are not comparable to v3 ones
— compare models within this table, and against future v4 runs.

## What the round proved

- **The wrap-up nudge works**: MiniMax went from three "did not converge"
  to concluding everywhere but P18; completion 57.9% → 92.3%.
- **The bar raisers separate the field**: MiniMax answered 3 of 4
  ground-truth figures (1,861 companies / €55,480,942.93 total /
  €30,862,249.55 top award) and was correctly caught computing its
  duplicate-merge caveat in its head — €55,003,066.58 vs the true
  €55,004,066.58, off by €1,000, exactly the failure class the calculator
  rule exists for. Both local models missed every bar-raiser figure.
- **MiniMax is the only model that investigates**: 74% vs ~37% for the
  locals; on P19 it built a Studio project, ran four queries, used the
  new script calculator correctly, and volunteered a SAME_AS
  duplicate-entity caveat plus a refutation path.
- **P20 (absent data)**: MiniMax queried five ways and reported a scoped
  zero (pass); 8B asserted without hedging (fail).
- **8B remains the grounding floor-keeper**: 100% grounding, never states
  an unread figure — but it does not explore, and it skips the Studio
  chain wholesale.

## Serving note

The first 8B v4 run executed concurrently with the 1.7B run; the two
llama instances share one physical iGPU (UMA) and the 8B degenerated into
"the the the…" repetition at 6.6 tok/s. Discarded and rerun solo (clean,
~10 tok/s). Local eval models run sequentially from now on; degenerate
repetition in results is a serving artifact — rerun, never score it.

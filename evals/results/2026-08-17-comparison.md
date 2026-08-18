# 2026-08-17 — qwen3-8b (ours) vs Qwen3.6-35B-A3B-FP8 (Hetzner free tier)

Fixture v2, 14 prompts, 12 rounds, 4000 tokens, same `fontem-api` (staging),
same shipped system prompt and tool schemas. Both runs are in this directory.

| layer | 8B (ours) | 35B (Hetzner) | 35B excl. provider 5xx |
|---|---|---|---|
| tool calling | 98% | 98% | 100% |
| completion | 100% | 57% | 83% |
| grounding | **0%** | **60%** | 60% |
| honesty | 86% | 48% | 53% |
| language | 100% | untested | untested |
| navigation | 67% | 100% | 100% |
| mean latency | **31s** | **336s** | — |

## What the numbers say

**Grounding is the real gap, and it is ours.** The 8B answered every prompt
and cited figures no tool returned — `1287435533` among them. Fluent, complete,
and making numbers up. On a platform whose purpose is that the numbers can be
checked, that is the worst failure mode available, and it is invisible to
anyone reading the answers unless they go and verify. The 35B scores 60% on
the same fixture with the same tools.

**Honesty moves the other way.** The 35B asserts without hedging where the 8B
abstains (P13), so it is not a strict improvement — it is a different balance:
more grounded, less cautious.

**The 8B is not being held back by the harness.** It scores identically at
r6t900 and r12t4000; neither cap binds for it. The gap is the model.

## What rules the free tier out for serving

- **~11x the latency.** 336s mean per prompt against 31s. Two prompts took
  over 900s. Nothing user-facing survives that.
- **Unreliable.** A 502 in one session, two 504s in another. P06 and P09 died
  on provider errors, not on anything the model did.
- **One model, no choice.** The endpoint serves `Qwen/Qwen3.6-35B-A3B-FP8`
  and nothing else.

## What it is good for

Answering "what would more compute buy us" without buying it. The answer this
run gives: mainly grounding and navigation, not tool calling — the 8B already
picks the right tools 98% of the time. Whatever is wrong with grounding is not
fixed by teaching the model to call tools; it calls them and then reports
numbers they did not return.

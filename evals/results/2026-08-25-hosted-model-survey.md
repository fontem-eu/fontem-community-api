# 2026-08-25 — seven models on fixture v2

Same fixture, same `fontem-api` (staging), same shipped system prompt and tool
schemas. Caps differ per run and are in each file's `meta`; the last column
records them.

| model | tools | compl | grnd | hon | lang | nav | latency | tool calls | caps |
|---|---|---|---|---|---|---|---|---|---|
| qwen3-8b (ours, local) | 98% | **100%** | **100%** | **86%** | 100% | **100%** | 35.9s | **15** | r12/t4000 |
| Qwen3-30B [nebius] | 91% | **100%** | 98% | 81% | 100% | **100%** | **11.9s** | 29 | r12/t4000 |
| Qwen3.5-397B [nebius] | 100% | 86% | 98% | 48% | 100% | **100%** | 30.9s | 122 | r25/t16000 |
| MiniMax-M3 [nebius] | 100% | 86% | 93% | 48% | 100% | 67% | 30.9s | 94 | r12/t16000 |
| gpt-oss-120b [nebius] | 100% | 86% | 89% | 62% | 100% | **0%** | **4.4s** | 41 | r12/t4000 |
| GLM-5.1 [nebius] | 100% | 86% | 56% | 48% | 100% | **100%** | 54.6s | 155 | r12/t16000 |
| ox-alpha [openrouter] | 100% | **100%** | 73% | 67% | 100% | 75% | 63.5s | 91 | r12/t4000 |

## What it says

**Nothing hosted beat the local 8B on accuracy.** It leads or ties on
completion, grounding, honesty and navigation, and does it in 15 tool calls
where the large models need 90–155. They are not being more thorough; they are
thrashing, and every extra call is more latency, more tokens and another chance
to assert something the tools did not return. That is visible as honesty
collapsing to 48% on the three highest-volume models.

**Qwen3-30B is the one worth offering.** Near-identical scores to ours at a
third of the latency, $0.10/$0.30 per Mtok.

**gpt-oss-120b is disqualified**: navigation 0% — it never offered a
destination on either navigation prompt — plus honesty 62% and one prompt
answered with no final text at all.

## Two things that make this table weaker than it looks

**The caps are not uniform.** Qwen3.5 ran at 25 rounds because at 12 it scored
29% completion, and five prompts died on `did not converge` — the harness's cap,
not the model. At 25 it reached 86%. The others were not re-run with the same
room, so their completion figures may be understated the same way. That mistake
has now been made three times (Hetzner's token cap, Qwen3.5's round cap) and it
always looks like a model failing.

**Grounding still penalises correct arithmetic.** ox-alpha was marked down for
figures it computed itself, labelled as computed, and got right to the cent:

> "€23,146,625.62. That euro figure is my own sum of the three non-zero totals
> above (10,236,792.89 + 12,874,355.33 + 35,477.40)."

That is precisely what the shipped system prompt asks for, and the scorer reads
it as fabrication. Our 8B is least exposed because it computes least — so its
100% is partly an artifact of doing less arithmetic, not only of being more
careful. Until a claim can be credited as an exact sum, difference or
percentage of figures that are in the evidence, treat the grounding column as
directional.

The fix under discussion is a calculator tool: a derived figure then becomes a
recorded tool call with its inputs, so grounding is checkable rather than
inferred. That changes the tool set and therefore the fixture, so it belongs
with a v3.

## Provider notes

- **Nebius withdrew `zai-org/GLM-5.2`** between two model-list fetches an hour
  apart (30 → 29 models). A model named in our picker can vanish; `offered()`
  checks only that a key is configured, not that the model still exists.
- **Kimi K3 and DeepSeek V4 Flash were unusable**: 600–1756s per prompt and
  repeated HTTP 504s. Measured directly, DeepSeek Flash took 79 seconds to
  return 13 tokens for "reply with the single word: ok" — queue wait, not
  generation. Nebius has capacity behind Qwen3-30B and gpt-oss-120b and very
  little behind those two. Abandoned rather than scored.

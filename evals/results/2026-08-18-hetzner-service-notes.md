# Hetzner inference API — what it is and where it bites

Free experimental endpoint, `https://inference.hetzner.com/api/v1`, probed
2026-08-17/18. Everything here was measured, not read off a doc page.

## Time of day moves small requests, not our workload

An 8-token request, same key, same endpoint:

| | latency |
|---|---|
| ~17:00 UTC | 29s, 52s, 99s |
| ~23:10 UTC | 0.56s, 0.63s, 1.0s |

So there is a real queue and it drains overnight. It does **not** rescue the
eval workload, because that is generation-bound rather than queue-bound — the
model emits thousands of reasoning tokens at roughly 10 tok/s. Same six
prompts, same settings, day against night:

    P01  843s -> 411s      P02  773s -> 1054s     P03  933s -> 791s
    P04  734s ->  646s     P05  127s ->   40s     P07  160s ->  112s
    mean 595s ->  509s     (~14%, and P02 got worse)

Waiting for a quiet queue is not the lever. `reasoning_effort` is.

## Reasoning is on by default and can be turned off

One-word question, `max_tokens=300`:

| request | completion tokens | reasoning |
|---|---|---|
| default | 160 | 522 chars |
| `reasoning_effort: "none"` | **2** | none |
| `chat_template_kwargs: {"enable_thinking": false}` | **2** | none |
| `reasoning_effort: "low"` | 186 | 641 chars — accepted, no effect |

Tool calling survives with reasoning off: 27 completion tokens instead of 216
for the same decision, same tool, same arguments. `tool_choice: "required"` is
honoured.

This is the single most important thing about the endpoint. Every number in
`2026-08-17-comparison.md` was measured with reasoning on, which is a
configuration nobody would deploy.

## Limits and misbehaviour

- **Concurrency ~7.** Ten parallel requests: three came back `HTTP 429`, seven
  succeeded. Six parallel: all fine.
- **No rate-limit headers.** No `x-ratelimit-*`, no `retry-after`. A client
  cannot back off from anything except the 429 itself.
- **`max_tokens` over ~8192 hangs.** No error, no rejection — the connection
  simply never returns. 8192 is accepted. This is the worst-behaved thing
  found: an out-of-range value should be refused, not swallowed.
- **Context 262,144 tokens**, with a clear error past it. Four times our local
  65k.
- **Streaming works** — proper SSE chunks. Note that non-streaming
  `time_to_first_byte == total_time`: a 276s generation returns nothing for
  276s.
- **Reliability.** Across two sessions: one `HTTP 502`, two `HTTP 504`, each
  killing a prompt mid-run. Two of fourteen prompts died on provider errors
  rather than on anything the model did.
- **One model.** `Qwen/Qwen3.6-35B-A3B-FP8` and nothing else.

## Where this leaves it

Unchanged as a serving option: latency, a 7-request ceiling and 5xx under
normal use rule it out. Its value is as a measuring instrument, and on that
front the reasoning-off configuration has not been scored yet — the run was
blocked on cluster capacity, not on the endpoint.

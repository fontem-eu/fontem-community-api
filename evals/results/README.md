# Eval results

One file per run: `<date>-<model>-r<rounds>t<tokens>.json`, exactly what
`runner.py --out` wrote. The caps are in the filename because they are the
settings most likely to differ between two runs of the same model on the same
day, and a run compared across them measures the harness rather than the
model.

They are committed because the question that matters is not "how does the
assistant score" but "is it better than it was", and that question cannot be
answered from runs that were written to a pod's `/tmp` and thrown away with
the pod. That is the state these results replace: the harness had been run
before, and none of it survived.

Each file carries a `meta` block naming the fixture version, the endpoint, the
round cap and the tool-result budget. **Compare only runs whose `meta` agrees**
— a fixture edit or a change to `MAX_ROUNDS` moves scores on its own, and a
comparison across either is measuring the harness rather than the model.

`meta.endpoint` records the host only. Provider URLs sometimes carry a key,
and these files are in git, where a leak cannot be taken back.

## Reading a run

Scores are per layer and deliberately not averaged into one number: a model
that is fluent and ungrounded has to fail visibly rather than average out to
"fine". `grounding: 0%` next to `completion: 100%` means the model answered
every prompt and cited a figure no tool returned — worse than a model that
answered nothing.

Negative percentages are penalties, not floors at zero.

## Notes on individual runs

- `2026-08-17-qwen3-8b-q4_k_m-r6t900.json` has an empty `code_sha`: it is the
  run that motivated `--code-sha`, produced before the flag existed. Its mean
  latency (46s) also ran against a busy node — a second eval was hitting the
  same llama-server throughout. Latency in these files is wall-clock under
  whatever load the node had, not a benchmark.
- The 8B scores identically at r6t900 and r12t4000, so neither cap was binding
  for it. They bind hard for the 35B: at r6t900 it scored completion -100%
  across the fixture, which was the harness truncating it rather than the model
  declining to answer.
- The 35B run lost P06 and P09 to HTTP 504s from the provider. Those are
  provider failures scored as prompt failures; P09 is the only prompt carrying
  the `language` check, so that layer is untested for the 35B rather than
  failed.

# Eval results

One file per run: `<date>-<model>.json`, exactly what `runner.py --out` wrote.

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

- `2026-08-17-qwen3-8b-q4_k_m.json` has an empty `code_sha`: it is the run that
  motivated `--code-sha`, produced before the flag existed. Its harness is the
  commit that added `evals/results/`.
- Its mean latency (46s) is not comparable to a quiet-node run: a second eval
  was hitting the same llama-server throughout. Latency in these files is
  wall-clock under whatever load the node had, not a benchmark.

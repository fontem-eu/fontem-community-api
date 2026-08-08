"""Drive every candidate model through the fixture and score the results.

Deliberately reuses the shipped pieces rather than reimplementing them: the
tool schemas, the tool executor (including the guard that turns fontem-api's
null skeleton into an explicit "not found"), and the production system prompt.
Reimplementing any of those would measure a system we do not run.

The tool loop here does NOT include the forced-continuation rescue that
production applies when a model stalls mid-chain. That is on purpose — the
rescue exists to paper over weak models, and papering over the difference is
the opposite of what a model comparison is for. Stalls are recorded and scored
instead. Production behaviour will therefore be somewhat better than these
numbers for the weaker models.

Usage:  python runner.py --base-url http://host:8080 --models a,b,c
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

import httpx
import yaml

sys.path.insert(0, "/app")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from scorer import (  # noqa: E402  pylint: disable=wrong-import-position
    Trace, ToolCall, aggregate, score_trace,
)

MAX_ROUNDS = 6
# Long enough for the 30B, which generates at ~14 tok/s.
REQUEST_TIMEOUT = 600.0


def load_shipped():
    """Pull the real tool schemas, executor and system prompt out of the app."""
    # pylint: disable=import-outside-toplevel
    from src.assistant.mistral_client import _TOOLS, MistralProxyClient
    try:
        from src.api.di import _DEFAULT_SYSTEM_PROMPT
        prompt = _DEFAULT_SYSTEM_PROMPT
        origin = "shipped"
    except Exception as exc:  # pragma: no cover - env differences
        prompt = ("You are Fontem's assistant. Use the tools to ground every "
                  "claim. Never state a figure you did not read from a tool.")
        origin = f"fallback ({type(exc).__name__})"
    return _TOOLS, MistralProxyClient, prompt, origin


async def run_prompt(http: httpx.AsyncClient, executor, base_url: str,
                     model: str, spec: dict, tools: list, system: str) -> Trace:
    """One prompt, one model, bounded tool loop."""
    trace = Trace(prompt_id=spec["id"], model=model)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": str(spec["prompt"]).strip()}]
    started = time.monotonic()
    try:
        for _ in range(MAX_ROUNDS):
            trace.rounds += 1
            resp = await http.post(
                f"{base_url}/v1/chat/completions",
                json={"model": model, "messages": messages, "tools": tools,
                      # Deterministic: a comparison whose result changes on
                      # re-run cannot support a deployment decision.
                      "temperature": 0.0, "max_tokens": 900},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code >= 400:
                trace.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break
            msg = resp.json()["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                trace.answer = (msg.get("content") or "").strip()
                break
            messages.append(msg)
            for call in calls:
                fname = call["function"]["name"]
                raw = call["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    args = {}
                result = await executor(http, fname, args)
                trace.calls.append(ToolCall(fname, args, result))
                messages.append({"role": "tool",
                                 "tool_call_id": call.get("id", ""),
                                 "name": fname, "content": result})
        else:
            trace.error = f"did not converge in {MAX_ROUNDS} rounds"
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        trace.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    trace.latency_s = round(time.monotonic() - started, 1)
    return trace


def summarise(results: list[dict]) -> str:
    """Per-model, per-category percentages. Never one aggregate number."""
    cats = ["tool_calling", "completion", "grounding", "honesty", "language"]
    models: list[str] = []
    totals: dict = {}
    for row in results:
        if row["model"] not in models:
            models.append(row["model"])
        for cat, val in row["categories"].items():
            acc = totals.setdefault((row["model"], cat), [0.0, 0.0])
            acc[0] += val["points"]
            acc[1] += val["max"]
    head = f"\n{'model':<24}" + "".join(f"{c[:11]:>13}" for c in cats) + f"{'lat s':>9}"
    lines = [head, "-" * (len(head) - 1)]
    for model in models:
        row = f"{model:<24}"
        for cat in cats:
            pts, mx = totals.get((model, cat), [0.0, 0.0])
            row += f"{(100.0 * pts / mx):>12.0f}%" if mx else f"{'-':>13}"
        lat = [r["latency_s"] for r in results if r["model"] == model]
        row += f"{(sum(lat) / len(lat)):>9.1f}" if lat else f"{'-':>9}"
        lines.append(row)
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--models", required=True,
                        help="comma-separated served names")
    parser.add_argument(
        "--gmr-api",
        default="http://fontem-api.fontem-staging.svc.cluster.local")
    parser.add_argument("--out", default="/tmp/eval-results.json")
    args = parser.parse_args()

    tools, client_cls, system, origin = load_shipped()
    fixture = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parent / "prompts.yaml")
        .read_text("utf-8"))
    prompts = fixture["prompts"]
    print(f"fixture v{fixture['version']}: {len(prompts)} prompts | "
          f"{len(tools)} tools | system prompt: {origin}", flush=True)

    proxy = client_cls(api_key="", gmr_api_url=args.gmr_api)
    executor = proxy._execute_tool  # pylint: disable=protected-access
    results = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http:
        # Model-outer so the router loads each model once. Prompt-outer would
        # reload 17GB of weights on every switch.
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            print(f"\n=== {model} ===", flush=True)
            for spec in prompts:
                trace = await run_prompt(http, executor, args.base_url,
                                         model, spec, tools, system)
                checks = score_trace(spec, trace)
                cats = aggregate(checks)
                results.append({
                    "model": model, "prompt": spec["id"],
                    "latency_s": trace.latency_s, "rounds": trace.rounds,
                    "tools": [c.name for c in trace.calls],
                    "error": trace.error, "answer": trace.answer[:1200],
                    "categories": {k: {"points": v["points"], "max": v["max"],
                                       "pct": round(v["pct"], 1),
                                       "notes": v["notes"]}
                                   for k, v in cats.items()},
                })
                flags = "; ".join(n for v in cats.values() for n in v["notes"])
                print(f"  {spec['id']}  {trace.latency_s:6.1f}s  "
                      f"tools={len(trace.calls)}  {flags[:110]}", flush=True)

    pathlib.Path(args.out).write_text(json.dumps(results, indent=2), "utf-8")
    print(f"\nwrote {args.out}", flush=True)
    print(summarise(results), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

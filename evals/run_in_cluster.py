"""Emit a Pod manifest that runs the eval harness inside the cluster.

Run the harness from a laptop and every model request crosses a `kubectl
port-forward`. That tunnel drops under long generations, and a dropped tunnel
lands in the results as "Server disconnected" -- indistinguishable from the
model failing. It cost an afternoon once: the 8B "failed" both prompts of a
comparison and the cause was the tunnel, not the model.

In-cluster there is no tunnel, and the pod reaches llama-server, fontem-api and
the public internet directly. The image already carries src/ and every
dependency; only evals/ is missing, so it rides in as a base64 tarball in an
env var and unpacks to /tmp (/app is not writable by the image's user).

Usage -- options BEFORE the pod name, runner args after a literal `--`
(argparse.REMAINDER swallows anything following the positional):

    python evals/run_in_cluster.py --image IMG [--secret-env ENV:SECRET:KEY] \
        POD_NAME -- <runner.py args...> | kubectl apply -f -

A --secret-env pair replaces the literal @@KEY@@ in the runner args at
start-up, so a provider token reaches the process without being written into
the manifest, the pod spec, or anything `kubectl get -o yaml` will show.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import pathlib
import sys
import tarfile

EVALS = pathlib.Path(__file__).resolve().parent

BOOTSTRAP = (
    "import base64,io,json,os,runpy,sys,tarfile;"
    "buf=io.BytesIO(base64.b64decode(os.environ['EVAL_PAYLOAD']));"
    "tarfile.open(fileobj=buf).extractall('/tmp');"
    "sys.path.insert(0,'/app');"
    "sys.argv=['runner.py']+[os.environ.get('EVAL_SECRET','') if a=='@@KEY@@' else a"
    " for a in json.loads(os.environ['EVAL_ARGS'])];"
    "runpy.run_path('/tmp/evals/runner.py',run_name='__main__')"
)


def payload(extra: list[str]) -> str:
    """The harness, as a base64 tarball. Only the files a run needs.

    `extra` rides along at /tmp/<basename> — for --system-file, whose whole
    point is A/B-ing a prompt that by definition is not in the image.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("runner.py", "scorer.py", "prompts.yaml",
                     "harness_ops.py"):
            tar.add(EVALS / name, arcname=f"evals/{name}")
        for path in extra:
            src = pathlib.Path(path)
            tar.add(src, arcname=src.name)
    return base64.b64encode(buf.getvalue()).decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--image", required=True)
    parser.add_argument("--namespace", default="fontem-staging")
    parser.add_argument("--pull-secret", default="regcred")
    parser.add_argument(
        "--secret-env", default="",
        help="ENV:SECRET:KEY -- substituted for @@KEY@@ in the runner args")
    parser.add_argument(
        "--file", action="append", default=[],
        help="local file to ship into the pod at /tmp/<basename>; repeatable. "
             "Use with --system-file /tmp/<basename> to A/B a prompt.")
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    runner_args = args.runner_args[1:] if args.runner_args[:1] == ["--"] \
        else args.runner_args

    env = [{"name": "EVAL_PAYLOAD", "value": payload(args.file)},
           {"name": "EVAL_ARGS", "value": json.dumps(runner_args)}]
    if args.secret_env:
        _, secret, key = args.secret_env.split(":", 2)
        env.append({"name": "EVAL_SECRET",
                    "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}})

    pod = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": args.name, "namespace": args.namespace,
                     # The harness talks to the public internet and to
                     # in-cluster services; the mesh sidecar buys nothing here
                     # and its startup ordering has stalled short runs.
                     "annotations": {"linkerd.io/inject": "disabled"}},
        "spec": {
            "restartPolicy": "Never",
            "imagePullSecrets": [{"name": args.pull_secret}],
            "containers": [{
                "name": "eval",
                "image": args.image,
                "command": ["python", "-u", "-c", BOOTSTRAP],
                "env": env,
                # Small: the work is waiting on a model elsewhere. Keep it
                # small enough to schedule on a busy node.
                "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                              "limits": {"memory": "1Gi"}},
            }],
        },
    }
    json.dump(pod, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Adopt alembic on a database whose schema was built by ``create_all``.

The problem this solves: the deployed databases were provisioned by
``Base.metadata.create_all`` plus a dozen hand-written
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statements in the FastAPI
lifespan. No environment has ever had an ``alembic_version`` row, and the
recorded migration history stopped at 007 while the models moved on — a
fresh ``upgrade head`` produced 130 columns where prod had 218.

So the databases cannot be migrated forward: ``upgrade`` would try to
CREATE tables that already exist. They have to be *stamped* — told which
revision they already correspond to — and that is a claim about reality.
If the claim is wrong, alembic will happily skip a migration the database
actually needs and the failure surfaces later as a missing column in
production.

This script therefore refuses to make that claim on trust. It proves the
live schema already satisfies every column in the model metadata, records
the schema and the row counts, stamps, and then proves nothing changed.
Any check that cannot be evaluated is a failure, not a pass.

Properties it is built to have:

* **Idempotent.** Already stamped at the target revision is a success, not
  an error. Safe to re-run, safe to run from a Job that retries.
* **Fails closed.** Every gate must return an explicit PASS. An exception,
  a missing table, an unexpected revision — all abort before any write.
* **Non-destructive by construction.** The only write is alembic's own
  ``alembic_version`` row. It never issues DDL against your tables, and
  it never drops the legacy objects it finds; it reports them.
* **Self-verifying.** The post-check re-reads the schema and the row
  counts and compares them to the pre-check. A silent difference fails.

Usage::

    DATABASE_URL=postgresql+asyncpg://... python scripts/adopt_alembic.py --check
    DATABASE_URL=postgresql+asyncpg://... python scripts/adopt_alembic.py --apply

``--check`` is a complete dry run: every gate, no writes. Run it first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from src.infra.postgres.models import Base  # noqa: E402

TARGET_REVISION = "008"
# Migrations only own `public`. Other schemas in this database (events,
# search, wikidata) belong to other services and are deliberately out of
# scope — counting search.entity_embeddings alone would take minutes.
OWNED_SCHEMA = "public"


class Gate:
    """A named check that must explicitly pass."""

    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)

    @property
    def failures(self) -> list[str]:
        return [n for n, ok, _ in self.results if not ok]


def _require_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set — refusing to guess which database to adopt")
    return url


async def _snapshot(engine) -> dict:
    """Columns, row counts and current alembic revision.

    Taken twice — before and after the stamp — and compared. Row counts
    are exact rather than estimated: the point is to be able to say
    nothing was lost, and reltuples is an estimate that drifts.
    """

    def _read(conn) -> dict:
        insp = inspect(conn)
        cols: dict[str, list[str]] = {}
        for table in sorted(insp.get_table_names(schema=OWNED_SCHEMA)):
            cols[table] = sorted(c["name"] for c in insp.get_columns(table, schema=OWNED_SCHEMA))
        return cols

    async with engine.connect() as conn:
        cols = await conn.run_sync(_read)
        counts = {}
        for table in cols:
            if table == "alembic_version":
                continue
            res = await conn.execute(text(f'SELECT count(*) FROM "{OWNED_SCHEMA}"."{table}"'))
            counts[table] = res.scalar_one()
        rev = None
        if "alembic_version" in cols:
            res = await conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = [r[0] for r in res]
            rev = rows[0] if len(rows) == 1 else f"<{len(rows)} rows: {rows}>"
    return {"columns": cols, "row_counts": counts, "revision": rev}


def _model_requirements() -> dict[str, set[str]]:
    """Every table+column the ORM will actually select."""
    req: dict[str, set[str]] = {}
    for table in Base.metadata.sorted_tables:
        req[table.name] = {c.name for c in table.columns}
    return req


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="run every gate, write nothing")
    mode.add_argument("--apply", action="store_true", help="stamp after all gates pass")
    ap.add_argument("--json", metavar="PATH", help="write the machine-readable report here")
    args = ap.parse_args()

    url = _require_url()
    safe = url.split("@")[-1]
    print(f"database: {safe}")
    print(f"target revision: {TARGET_REVISION}")
    print(f"mode: {'apply' if args.apply else 'check (no writes)'}\n")

    engine = create_async_engine(url, connect_args={"timeout": 15, "ssl": None})
    gate = Gate()
    report: dict = {"database": safe, "target": TARGET_REVISION, "mode": "apply" if args.apply else "check"}

    try:
        print("PREFLIGHT")
        before = await _snapshot(engine)
        gate.record("connect + read schema", True, f"{len(before['columns'])} tables in {OWNED_SCHEMA}")
        report["before"] = before

        # 1. Does the live schema already satisfy the models? This is the
        #    claim the stamp makes; everything else is bookkeeping.
        req = _model_requirements()
        missing: dict[str, list[str]] = {}
        for table, cols in req.items():
            live = set(before["columns"].get(table, []))
            if not live:
                missing[table] = ["<TABLE ABSENT>"]
                continue
            gap = sorted(cols - live)
            if gap:
                missing[table] = gap
        report["missing_vs_models"] = missing
        gate.record(
            "live schema satisfies every model column",
            not missing,
            "nothing missing" if not missing else f"MISSING {missing}",
        )

        # 2. Legacy objects: reported, never touched. A stamped database
        #    keeps them; that is the non-destructive choice.
        extras: dict[str, list[str]] = {}
        for table, live_cols in before["columns"].items():
            if table == "alembic_version":
                continue
            if table not in req:
                extras[table] = ["<TABLE NOT IN MODELS>"]
                continue
            surplus = sorted(set(live_cols) - req[table])
            if surplus:
                extras[table] = surplus
        report["legacy_extras"] = extras
        gate.record(
            "legacy objects catalogued (kept, not dropped)",
            True,
            f"{len(extras)} table(s) with legacy objects" if extras else "none",
        )

        # 3. Revision state must be one we understand.
        rev = before["revision"]
        if rev is None:
            state, ok = "unstamped — will stamp", True
        elif rev == TARGET_REVISION:
            state, ok = f"already at {rev} — nothing to do", True
        else:
            state, ok = f"UNEXPECTED revision {rev!r}", False
        gate.record("alembic_version state is understood", ok, state)

        if not gate.ok:
            print(f"\nABORTED — gates failed: {', '.join(gate.failures)}")
            report["result"] = "aborted"
            return 2

        already = rev == TARGET_REVISION
        if args.check:
            print("\ncheck mode: all gates pass, nothing written")
            report["result"] = "check-ok"
            return 0

        print("\nAPPLY")
        if already:
            gate.record("stamp", True, "skipped — already at target (idempotent)")
        else:
            res = _alembic("stamp", TARGET_REVISION)
            if res.returncode != 0:
                print(res.stdout, res.stderr)
                gate.record("alembic stamp", False, f"exit {res.returncode}")
                report["result"] = "stamp-failed"
                return 3
            gate.record("alembic stamp", True, f"-> {TARGET_REVISION}")

        # 4. upgrade head must now be a no-op. If it emits DDL, the stamp
        #    was wrong about where we were.
        res = _alembic("upgrade", "head")
        ran = [ln for ln in (res.stdout + res.stderr).splitlines() if "Running upgrade" in ln]
        gate.record(
            "upgrade head is a no-op",
            res.returncode == 0 and not ran,
            "no migrations ran" if not ran else f"UNEXPECTED DDL: {ran}",
        )

        print("\nPOST-VERIFY")
        after = await _snapshot(engine)
        report["after"] = after

        b, a = before["columns"], after["columns"]
        added = {t: sorted(set(a.get(t, [])) - set(b.get(t, []))) for t in set(a) | set(b)}
        added = {t: v for t, v in added.items() if v}
        removed = {t: sorted(set(b.get(t, [])) - set(a.get(t, []))) for t in set(a) | set(b)}
        removed = {t: v for t, v in removed.items() if v}
        # alembic_version appearing is the one expected difference.
        expected_add = {"alembic_version": ["version_num"]}
        gate.record(
            "schema unchanged except alembic_version",
            added in ({}, expected_add) and not removed,
            f"added={added} removed={removed}",
        )

        deltas = {
            t: (before["row_counts"].get(t), after["row_counts"].get(t))
            for t in set(before["row_counts"]) | set(after["row_counts"])
            if before["row_counts"].get(t) != after["row_counts"].get(t)
        }
        report["row_deltas"] = deltas
        total = sum(before["row_counts"].values())
        gate.record("zero row-count delta", not deltas, f"{total} rows across {len(before['row_counts'])} tables")

        # Read the row itself rather than grepping `alembic current`'s
        # output — that output includes log lines, so a substring match
        # could pass on text that merely mentions the revision.
        gate.record(
            "alembic_version row == target",
            after["revision"] == TARGET_REVISION,
            f"version_num={after['revision']!r}",
        )

        report["result"] = "ok" if gate.ok else "post-verify-failed"
        if not gate.ok:
            print(f"\nFAILED — {', '.join(gate.failures)}")
            return 4
        print("\nADOPTED — stamped, verified, no schema or row changes")
        return 0

    finally:
        await engine.dispose()
        report["gates"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in gate.results]
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=2, default=str))
            print(f"report: {args.json}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

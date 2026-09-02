"""The compare-and-swap, arbitrated by Postgres rather than by Python.

The unit tests pin the rule against the in-memory repository, which
enforces it because I wrote the same check there. That proves the
service asks for a CAS; it cannot prove the database performs one. The
guard lives in a WHERE clause —

    UPDATE doc_branches SET head_revision_id = :new
     WHERE report_id = :r AND owner_id = :o AND head_revision_id = :expected

— and only a real Postgres, with two connections racing, decides whether
that clause actually arbitrates.

The bug being closed: on 2026-09-02 four accept-all saves went out
together, two landed 41ms apart as siblings of one parent, and a
document holding four charts was buried under one holding a single
chart. Both had passed the baseline check, because the check compared in
Python between two statements.
"""
# pylint: disable=missing-function-docstring,import-outside-toplevel
from __future__ import annotations

import asyncio
import os

from tests.integration.conftest import make_headers


def _widgets(n: int) -> dict:
    return {"type": "doc", "content": [
        {"type": "widget", "attrs": {"widget_type": "pipeline",
                                     "ui_params": {"y": f"plot-{i}"}}}
        for i in range(n)]}


def _race(report_id: str, user_id: str, base: str, docs: list[dict]):
    """Fire N saves concurrently, each on its OWN connection.

    One container per save is the point: sharing a session would serialise
    them in SQLAlchemy and test nothing. This is the shape of the real
    failure — separate requests, separate connections, same baseline.
    """
    async def one(doc):
        from src.api.di import make_container
        from src.services.report_service import ReportService
        from src.services.exceptions import Conflict
        container = make_container(os.environ["DATABASE_URL"])
        try:
            async with container() as scope:
                svc = await scope.get(ReportService)
                try:
                    rev = await svc.save_document(user_id, report_id, doc, base)
                    return ("ok", rev.id)
                except Conflict:
                    return ("conflict", None)
        finally:
            await container.close()

    async def go():
        return await asyncio.gather(*[one(d) for d in docs])

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def _head(report_id: str, user_id: str):
    async def go():
        from src.api.di import make_container
        from src.services.report_service import ReportService
        container = make_container(os.environ["DATABASE_URL"])
        try:
            async with container() as scope:
                svc = await scope.get(ReportService)
                return await svc.draft_head(user_id, report_id)
        finally:
            await container.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def test_postgres_lets_exactly_one_concurrent_save_win(client, user_id):
    h = make_headers(user_id)
    report = client.post("/reports", json={"title": "race"}, headers=h).json()
    first = client.put(f"/reports/{report['id']}/content",
                       json={"tiptap": _widgets(0)}, headers=h).json()
    base = first["revision"]

    # Four saves, same baseline, the shape accept-all produces.
    results = _race(report["id"], user_id, base,
                    [_widgets(n) for n in (4, 1, 2, 3)])
    ok = [r for r in results if r[0] == "ok"]
    conflicts = [r for r in results if r[0] == "conflict"]

    assert len(ok) == 1, (
        f"exactly one save may win; Postgres let {len(ok)} through: {results}")
    assert len(conflicts) == 3, results

    # And the survivor is the winner's document, not a sibling that got
    # buried under it.
    head = _head(report["id"], user_id)
    assert head.id == ok[0][1]


def test_the_winners_content_is_intact(client, user_id):
    """The document that survives is whole — the failure being closed was
    four charts replaced by one, not a save being rejected."""
    h = make_headers(user_id)
    report = client.post("/reports", json={"title": "race-2"}, headers=h).json()
    first = client.put(f"/reports/{report['id']}/content",
                       json={"tiptap": _widgets(0)}, headers=h).json()
    results = _race(report["id"], user_id, first["revision"],
                    [_widgets(4), _widgets(1)])
    winner = [r for r in results if r[0] == "ok"][0]
    head = _head(report["id"], user_id)
    assert head.id == winner[1]
    widgets = [n for n in head.content_json["content"]
               if n.get("type") == "widget"]
    # Whichever won, its document arrived complete rather than merged.
    assert len(widgets) in (1, 4)
    ys = [w["attrs"]["ui_params"]["y"] for w in widgets]
    assert ys == [f"plot-{i}" for i in range(len(widgets))]

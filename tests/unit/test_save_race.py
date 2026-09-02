"""Two saves in flight at once must not bury one another.

Production, 2026-09-02. An accept-all turn applied four
insert_studio_plot cards. The panel applies serially but does not await
the parent's save, so four saves went out together. Two of them landed
41ms apart as SIBLINGS of the same parent:

    4f7c102c  parent 770e0480  4 widgets  12192 chars
    5ad365a6  parent 770e0480  1 widget    6554 chars

Both passed `_require_baseline`, because both read the branch head
before either wrote it. The second pointer write won, so a document with
four charts was replaced by one with a single chart — the exact "silent
overwrite this whole mechanism exists to prevent" that the baseline
check's own docstring promises against.

The check was doing the comparison in Python, between two statements.
These tests pin it as a compare-and-swap instead.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio

import pytest

from src.services.exceptions import Conflict
from tests.conftest import seed_user


def _doc(text: str) -> dict:
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _widgets(n: int) -> dict:
    body = [{"type": "widget", "attrs": {"widget_type": "pipeline",
                                         "ui_params": {"y": f"plot-{i}"}}}
            for i in range(n)]
    return {"type": "doc", "content": body}


@pytest.fixture
def service(services):
    """The real wired service, with the production window held open.

    `add_revision` is wrapped to yield to the event loop, so a second
    save reliably slips between the first save's baseline read and its
    pointer write — the interleaving that produced the sibling
    revisions, made deterministic instead of waited for.
    """
    svc = services["report_svc"]
    original = svc._reports.add_revision

    async def slow_add_revision(revision):
        await asyncio.sleep(0)
        return await original(revision)

    svc._reports.add_revision = slow_add_revision
    # seed_user derives a UUID5 from the friendly id; the service must be
    # called with the derived one or every call is "unauthenticated".
    user = _run(seed_user(services["user_repo"], "u1"))
    return svc, user.id


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestConcurrentSaves:

    def test_the_loser_is_refused_rather_than_burying_the_winner(self, service):
        service, uid = service
        async def go():
            report = await service.create(uid, title="Israeli spending")
            first = await service.save_document(uid, report.id, _doc("start"))

            # Both saves read the same head, exactly as the four applies did.
            results = await asyncio.gather(
                service.save_document(uid, report.id, _widgets(4), first.id),
                service.save_document(uid, report.id, _widgets(1), first.id),
                return_exceptions=True,
            )
            head = await service.draft_head(uid, report.id)
            return results, head

        results, head = _run(go())
        ok = [r for r in results if not isinstance(r, Exception)]
        refused = [r for r in results if isinstance(r, Conflict)]
        assert len(ok) == 1, f"exactly one save may win, got {results}"
        assert len(refused) == 1, (
            "the loser must be refused, not silently applied; "
            f"got {[type(r).__name__ for r in results]}")
        # And the surviving document is the winner's, whole.
        assert head.id == ok[0].id

    def test_the_refusal_hands_back_what_is_current(self, service):
        service, uid = service
        """So the client can show the real document rather than guess."""
        async def go():
            report = await service.create(uid, title="t")
            first = await service.save_document(uid, report.id, _doc("start"))
            results = await asyncio.gather(
                service.save_document(uid, report.id, _widgets(4), first.id),
                service.save_document(uid, report.id, _widgets(1), first.id),
                return_exceptions=True,
            )
            return [r for r in results if isinstance(r, Conflict)][0]

        err = _run(go())
        assert err.payload["current_revision"]
        assert err.payload["current_doc"] is not None

    def test_four_racing_saves_leave_exactly_one_winner(self, service):
        service, uid = service
        """The shape of the real turn: one card per plot, applied at once."""
        async def go():
            report = await service.create(uid, title="t")
            first = await service.save_document(uid, report.id, _doc("start"))
            results = await asyncio.gather(*[
                service.save_document(uid, report.id, _widgets(i + 1), first.id)
                for i in range(4)
            ], return_exceptions=True)
            head = await service.draft_head(uid, report.id)
            return results, head

        results, head = _run(go())
        winners = [r for r in results if not isinstance(r, Exception)]
        assert len(winners) == 1, (
            "three of four must be refused so the author is told, rather "
            f"than three documents being buried; got {results}")
        assert head.id == winners[0].id

    def test_sequential_saves_are_untouched(self, service):
        service, uid = service
        """The ordinary path must still just work."""
        async def go():
            report = await service.create(uid, title="t")
            r1 = await service.save_document(uid, report.id, _doc("one"))
            r2 = await service.save_document(uid, report.id, _doc("two"), r1.id)
            r3 = await service.save_document(uid, report.id, _widgets(4), r2.id)
            return r3, await service.draft_head(uid, report.id)

        r3, head = _run(go())
        assert head.id == r3.id
        assert len(head.content_json["content"]) == 4

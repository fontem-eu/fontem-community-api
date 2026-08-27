"""DocOps against real rows, through the real service, with real permissions.

The unit tests mock ReportService; these do not. What only this layer can
prove: the report the tool reads is the one Postgres holds, the sections
arrive in order with their stored content, and the permission model is the
service's own — another user's DocOps gets a refusal string, not data,
because the tool inherits STORIES_READ instead of implementing anything.

The container plumbing mirrors a real turn: the app's own dishka container
supplies ReportService exactly as di.py wires it for the assistant.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring
from __future__ import annotations

import asyncio
import json

from src.assistant.doc_ops import DocOps
from tests.integration.conftest import make_headers


def _read(client, user_id, report_id):
    """Run DocOps.read the way a turn does: di's own container factory.

    A fresh container per call, on a fresh event loop, against the same
    DATABASE_URL the app under test uses — the app's own container cannot be
    borrowed because its asyncpg engine is bound to the TestClient's loop.
    The wiring is still the production wiring: make_container is what di.py
    exports and app startup calls.
    """
    del client  # rows come from the shared DB, not this handle

    async def _run():
        # pylint: disable=import-outside-toplevel
        import os
        from src.api.di import make_container
        from src.services.report_service import ReportService

        container = make_container(os.environ["DATABASE_URL"])
        try:
            async with container() as request_scope:
                svc = await request_scope.get(ReportService)
                return await DocOps(svc, user_id, report_id).read()
        finally:
            await container.close()

    loop = asyncio.new_event_loop()
    try:
        return json.loads(loop.run_until_complete(_run()))
    finally:
        loop.close()


def test_read_document_returns_what_postgres_holds(client, user_id):
    h = make_headers(user_id)
    report = client.post("/reports", json={
        "title": "RU spending draft",
        "abstract": "Before and after sanctions.",
    }, headers=h).json()
    client.post(f"/reports/{report['id']}/sections", json={
        "content": "<p>EUR 12,874,355.33</p>",
    }, headers=h)

    body = _read(client, user_id, report["id"])
    assert "error" not in body, body.get("error")
    assert body["title"] == "RU spending draft"
    assert body["abstract"] == "Before and after sanctions."
    assert "12,874,355.33" in body["sections"]
    assert "SAVED" in body["note"]


def test_another_user_gets_a_refusal_not_data(client, user_id, user2_id):
    h = make_headers(user_id)
    report = client.post("/reports", json={"title": "Private draft"},
                         headers=h).json()

    body = _read(client, user2_id, report["id"])
    assert "error" in body
    assert "Private draft" not in json.dumps(body), \
        "the refusal itself must not leak the title"


def test_a_deleted_report_reads_as_an_error(client, user_id):
    h = make_headers(user_id)
    report = client.post("/reports", json={"title": "Doomed"},
                         headers=h).json()
    client.delete(f"/reports/{report['id']}", headers=h)

    body = _read(client, user_id, report["id"])
    assert "error" in body


def test_sections_arrive_in_stored_order(client, user_id):
    h = make_headers(user_id)
    report = client.post("/reports", json={"title": "Ordered"},
                         headers=h).json()
    for word in ("first", "second", "third"):
        client.post(f"/reports/{report['id']}/sections", json={
            "content": f"<p>{word}</p>",
        }, headers=h)

    sections = _read(client, user_id, report["id"])["sections"]
    assert sections.index("first") < sections.index("second") < sections.index("third")

"""The document surface: read as the user, propose with required params.

What these pin, in the order the failures happened in production:

- read_document exists and reads server-side as the asking user; without a
  bound document it says so instead of erroring.
- The proposal verbs refuse empty payloads instead of proposing nothing.
- insert_widget refuses a widget that would not render — the Apply button
  must never be the discovery mechanism for a typo'd type or an invented
  entity id. The entity check goes through the skeleton-aware resolver.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.assistant import pydantic_ai_client as pai
from src.assistant.doc_ops import DocOps
from src.assistant.tool_runtime import ToolRuntime


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _runtime():
    return ToolRuntime(gmr_api_url="http://fontem-api")


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _dispatch(rt, name, args, doc=None, client=None, traced=None):
    """``traced`` is the turn's history: some guards read it to find out
    what the model has already done this turn."""
    return _run(rt.dispatch(
        client or MagicMock(), name, args,
        studio=None, nav_routes=[], pending_nav=[],
        budget=[14_000], name_cache={},
        traced=[] if traced is None else traced, doc=doc,
    ))


#: A turn in which the model has actually read the document. replace_body
#: requires one — see "writing blind" at the foot of this file.
def _read_ok():
    return [{"tool": "mcp__gmr__read_document",
             "result": '{"title": "T", "sections": "[]"}'}]


# ── read_document ─────────────────────────────────────────────

class _FakeReports:
    """A report service as far as DocOps needs one.

    It answers with the caller's DRAFT, because that is the document they
    have open: an assistant revising the published version while its user
    edits a draft proposes changes against text neither is looking at.
    """

    def __init__(self, text="hello", draft=True):
        self.get = AsyncMock(return_value=MagicMock(
            title="Draft", abstract="About things"))
        revision = MagicMock()
        revision.id = "rev-1"
        revision.content_json = {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text",
                                               "text": text}]}]}
        self.draft_head = AsyncMock(return_value=revision if draft else None)
        self.document_head = AsyncMock(return_value=revision)


def test_read_document_returns_the_saved_document():
    doc = DocOps(_FakeReports(), "u-1", "r-1")
    out, _ = _dispatch(_runtime(), "mcp__gmr__read_document", {}, doc=doc)
    body = json.loads(out)
    assert body["title"] == "Draft"
    assert "hello" in body["sections"]
    assert "SAVED" in body["note"]


def test_read_document_reads_as_the_asking_user():
    reports = _FakeReports()
    doc = DocOps(reports, "u-1", "r-1")
    _dispatch(_runtime(), "mcp__gmr__read_document", {}, doc=doc)
    reports.get.assert_awaited_once_with("u-1", "r-1")


def test_no_bound_document_is_an_explanation_not_a_crash():
    out, _ = _dispatch(_runtime(), "mcp__gmr__read_document", {}, doc=None)
    assert "no document is open" in json.loads(out)["error"]


def test_a_refused_read_reaches_the_model_as_a_reason():
    reports = _FakeReports()
    reports.get = AsyncMock(side_effect=PermissionError("not yours"))
    doc = DocOps(reports, "u-2", "r-1")
    out, _ = _dispatch(_runtime(), "mcp__gmr__read_document", {}, doc=doc)
    assert "cannot read this document" in json.loads(out)["error"]


def test_an_oversize_document_is_truncated_with_a_marker():
    reports = _FakeReports()
    fat = MagicMock()
    fat.id = "rev-fat"
    fat.content_json = {"type": "doc", "text": "x" * 20_000}
    reports.draft_head = AsyncMock(return_value=fat)
    doc = DocOps(reports, "u-1", "r-1")
    out = _run(doc.read())
    assert "document truncated" in out


# ── the proposal verbs ────────────────────────────────────────

def test_each_verb_proposes_with_its_required_field():
    rt = _runtime()
    for name, field, value, action in (
        ("mcp__gmr__set_title", "title", "New title", "set_title"),
        ("mcp__gmr__set_abstract", "abstract", "New abstract", "set_abstract"),
        ("mcp__gmr__replace_body", "content", "<p>x</p>", "replace_body"),
    ):
        out, _ = _dispatch(rt, name, {field: value}, traced=_read_ok())
        body = json.loads(out)
        assert body == {"proposed": True, "action": action}


def test_an_empty_required_field_is_refused():
    rt = _runtime()
    out, _ = _dispatch(rt, "mcp__gmr__replace_body", {"content": "   "},
                       traced=_read_ok())
    assert "required" in json.loads(out)["error"]


# ── insert_widget validation ──────────────────────────────────

def _client_resolving(name_or_none, *, label="Company"):
    """A fake httpx client answering the way fontem-api really does.

    The field is `company_name` / `authority_name` — never a bare `name`.
    The old fake said `name`, so a validator reading `profile["name"]`
    passed here and returned null in production for every widget the
    model proposed. A fake that answers in a shape the server never sends
    is a test that cannot fail on the bug it is there to catch.
    """
    field = "company_name" if label == "Company" else "authority_name"
    client = MagicMock()

    async def get(url, **_):
        wanted = "companies" if label == "Company" else "authorities"
        resp = MagicMock()
        resp.status_code = 200
        body = {"gmr_id": "e-1"}
        # Both endpoints answer 200 with a skeleton; only the right one
        # carries a name. That IS the skeleton-200 trap, reproduced.
        body[field] = name_or_none if wanted in str(url) else None
        resp.json = MagicMock(return_value=body)
        return resp

    client.get = get
    return client


def test_a_widget_for_a_real_entity_is_proposed_with_its_name():
    out, _ = _dispatch(
        _runtime(), "mcp__gmr__insert_widget",
        {"widget_type": "graph_explorer", "entityId": "e-1"},
        client=_client_resolving("Siemens AG"))
    body = json.loads(out)
    assert body["proposed"] is True
    assert body["entity_name"] == "Siemens AG"


def test_an_authority_widget_is_proposed_with_its_name():
    out, _ = _dispatch(
        _runtime(), "mcp__gmr__insert_widget",
        {"widget_type": "entity_profile", "entityId": "e-1"},
        client=_client_resolving("Metro Mondego", label="Authority"))
    body = json.loads(out)
    assert body["proposed"] is True
    assert body["entity_name"] == "Metro Mondego"


def test_an_unknown_widget_type_is_refused_with_the_menu():
    out, _ = _dispatch(
        _runtime(), "mcp__gmr__insert_widget",
        {"widget_type": "pie_chart", "entityId": "e-1"},
        client=_client_resolving("Siemens AG"))
    body = json.loads(out)
    assert "unknown widget_type" in body["error"]
    assert "graph_explorer" in body["hint"]


def test_an_entity_that_does_not_resolve_is_refused():
    # The skeleton-200 trap: fontem-api answers any id with an empty
    # profile, so "no name" is the only honest nonexistence signal.
    out, _ = _dispatch(
        _runtime(), "mcp__gmr__insert_widget",
        {"widget_type": "graph_explorer", "entityId": "made-up"},
        client=_client_resolving(None))
    body = json.loads(out)
    assert "no entity" in body["error"]
    assert "search_entities" in body["hint"]


def test_a_silly_depth_is_refused():
    out, _ = _dispatch(
        _runtime(), "mcp__gmr__insert_widget",
        {"widget_type": "graph_explorer", "entityId": "e-1", "depth": 9},
        client=_client_resolving("Siemens AG"))
    assert "depth" in json.loads(out)["error"]


# ── engine threading ──────────────────────────────────────────
#
# The regression that Sonar caught before any test did: the pydantic
# engine — the production default — built its tool closures without
# passing `doc` through, so read_document answered "no document is open"
# on every real turn while the unit tests, which call dispatch directly,
# stayed green. Both engines must thread it.

def test_the_pydantic_engine_threads_doc_into_dispatch():
    class _FakeTool:  # pylint: disable=too-few-public-methods
        def __init__(self, function, name, **_):
            self.function = function
            self.name = name

        @classmethod
        def from_schema(cls, function, name, **kw):
            return cls(function, name, **kw)

    client = pai.PydanticAIProxyClient(gmr_api_url="http://fake")
    seen = {}

    async def _capture(_client, _name, _args, **kwargs):
        seen.update(kwargs)
        return "{}", 0

    client._tools = MagicMock()
    client._tools.dispatch = _capture
    specs = [{"function": {"name": "mcp__gmr__read_document",
                           "description": "d", "parameters": {}}}]
    marker = object()
    from src.assistant import tool_runtime
    tools = client._build_tools(
        None, _FakeTool, specs, [], [10_000], None, [], {}, [],
        ctx=tool_runtime.ToolTurnContext(doc=marker),
    )
    _run(tools[0].function())
    assert seen.get("doc") is marker, \
        "read_document is dead on this engine if doc does not arrive"


# ── writing blind ─────────────────────────────────────────────

def test_a_rewrite_without_reading_the_document_is_refused():
    """The failure this exists for (prod, 2026-08-30): read_document
    returned "not found", and the model proposed a full replace_body
    anyway. A rewrite destroys everything it does not reproduce, so it is
    the one edit that must not be written from memory."""
    out, _ = _dispatch(_runtime(), "mcp__gmr__replace_body",
                       {"content": "<p>invented</p>"}, traced=[])
    body = json.loads(out)
    assert "needs the current document" in body["error"]
    assert "read_document" in body["hint"]


def test_a_failed_read_does_not_count_as_having_read_it():
    traced = [{"tool": "mcp__gmr__read_document",
               "result": '{"error": "cannot read this document: not found"}'}]
    out, _ = _dispatch(_runtime(), "mcp__gmr__replace_body",
                       {"content": "<p>invented</p>"}, traced=traced)
    assert "needs the current document" in json.loads(out)["error"]


def test_a_rewrite_after_a_real_read_goes_through():
    traced = [{"tool": "mcp__gmr__read_document",
               "result": '{"title": "T", "sections": "[]"}'}]
    out, _ = _dispatch(_runtime(), "mcp__gmr__replace_body",
                       {"content": "<p>grounded in what it read</p>"},
                       traced=traced)
    assert json.loads(out)["proposed"] is True


def test_the_agent_reads_the_draft_not_the_published_text():
    """The document the user has open is the draft. Revising the
    published version while they edit a draft proposes changes against
    text neither of them is looking at."""
    reports = _FakeReports(text="my unpublished draft")
    published = MagicMock()
    published.id = "rev-published"
    published.content_json = {"type": "doc", "content": [
        {"type": "paragraph",
         "content": [{"type": "text", "text": "the published text"}]}]}
    reports.document_head = AsyncMock(return_value=published)

    body = json.loads(_run(DocOps(reports, "u-1", "r-1").read()))
    assert "my unpublished draft" in body["sections"]
    assert "the published text" not in body["sections"]
    assert body["revision"] == "rev-1"


def test_without_a_draft_it_reads_what_is_published():
    reports = _FakeReports(text="the published text", draft=False)
    body = json.loads(_run(DocOps(reports, "u-1", "r-1").read()))
    assert "the published text" in body["sections"]


def test_an_article_with_nothing_saved_says_so_rather_than_inventing():
    reports = _FakeReports(draft=False)
    reports.document_head = AsyncMock(return_value=None)
    body = json.loads(_run(DocOps(reports, "u-1", "r-1").read()))
    assert "no saved version" in body["error"]
    assert "do not invent" in body["hint"]

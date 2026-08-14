"""An agent's work is attributable, and points back at the call that did it.

The platform's claim is that figures trace back to a source. Once a model
can write on a user's behalf, the same claim has to hold for the platform's
own actions: an activity entry saying the user created a Studio project is
false when the assistant created it, and until now there was no way to tell.

These pin the three properties that make "the agent did this, because of
that prompt" a link rather than an inference:

  * the entry says an agent did it, while still naming the user whose
    permission allowed it;
  * it names the conversation and the exact tool call;
  * a user acting directly is unaffected.
"""
# pylint: disable=protected-access
import asyncio
import json

import pytest

from src.infra.memory.mem_activity_repo import InMemoryActivityRepository
from src.services import audit_context
from src.services.activity_service import ActivityService
from src.services.audit_context import (
    AGENT,
    USER,
    AgentActionForbidden,
    AuditContext,
)
from src.assistant.tool_runtime import ToolRuntime
from tests.conftest import _stable_uuid, seed_user


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(name="log")
def _log():
    repo = InMemoryActivityRepository()
    yield repo, ActivityService(repo)
    audit_context.set_current(AuditContext())


async def _owner(services):
    """A real user row — the services resolve a principal from it."""
    await seed_user(services["user_repo"], "auditor")
    return _stable_uuid("auditor")


def _as_agent(actor_id="u-1", **over):
    audit_context.set_current(AuditContext(
        actor_id=actor_id, actor_kind=AGENT, conversation_id="conv-7",
        request_id="req-3", **over))


# ── attribution ────────────────────────────────────────────────

def test_a_direct_user_action_is_recorded_as_the_user(log):
    repo, svc = log
    audit_context.set_current(AuditContext(actor_id="u-1", request_id="req-1"))
    _run(svc.record("u-1", "story", "s-1", "created", "My story"))
    assert repo._events[0].actor_kind == USER
    assert repo._events[0].conversation_id is None


def test_an_agent_action_says_so(log):
    repo, svc = log
    _as_agent()
    _run(svc.record("u-1", "data_project", "p-1", "created", "Russian suppliers"))
    assert repo._events[0].actor_kind == AGENT


def test_the_agent_entry_still_names_the_user(log):
    # The agent has no standing of its own: the permission that allowed the
    # write was the user's, and the feed is theirs.
    repo, svc = log
    _as_agent()
    _run(svc.record("u-1", "data_project", "p-1", "created"))
    assert repo._events[0].actor_id == "u-1"


def test_the_agent_entry_names_the_conversation(log):
    repo, svc = log
    _as_agent()
    _run(svc.record("u-1", "data_project", "p-1", "created"))
    assert repo._events[0].conversation_id == "conv-7"


def test_everything_in_one_request_shares_a_correlation_id(log):
    repo, svc = log
    _as_agent()
    _run(svc.record("u-1", "data_project", "p-1", "created"))
    _run(svc.record("u-1", "data_project", "p-1", "query_added"))
    assert {e.request_id for e in repo._events} == {"req-3"}


# ── the tool-call link ─────────────────────────────────────────

def test_a_write_inside_a_tool_call_names_that_call(log):
    repo, svc = log
    _as_agent()
    with audit_context.tool_call("call-abc", "studio_create_project"):
        _run(svc.record("u-1", "data_project", "p-1", "created"))
    assert repo._events[0].message_id == "call-abc"


def test_a_write_outside_any_tool_call_names_none(log):
    repo, svc = log
    _as_agent()
    _run(svc.record("u-1", "data_project", "p-1", "created"))
    assert repo._events[0].message_id is None


def test_the_call_id_comes_off_again_afterwards(log):
    # Otherwise a later write in the same turn is blamed on whichever tool
    # happened to run last.
    repo, svc = log
    _as_agent()
    with audit_context.tool_call("call-abc"):
        _run(svc.record("u-1", "data_project", "p-1", "created"))
    _run(svc.record("u-1", "data_project", "p-1", "updated"))
    assert repo._events[0].message_id == "call-abc"
    assert repo._events[1].message_id is None


def test_the_id_the_activity_names_is_the_id_the_conversation_stores(log):
    """The linkage, end to end: what the tool wrote and what the panel
    renders are the same event."""
    repo, svc = log
    _as_agent()

    class _Studio:
        # **_ so this keeps matching StudioOps.execute, which now also takes
        # the turn's HTTP client and API url for validating what it writes.
        async def execute(self, _name, args, **_):
            await svc.record("u-1", "data_project", "p-9", "created",
                             args.get("name", ""))
            return json.dumps({"id": "p-9"})

    traced: list = []
    _run(ToolRuntime(gmr_api_url="http://fake").dispatch(
        None, "mcp__gmr__studio_create_project", {"name": "Russian suppliers"},
        studio=_Studio(), nav_routes=[], pending_nav=[], budget=[10_000],
        name_cache={}, traced=traced, audit=audit_context,
    ))
    assert repo._events[0].message_id == traced[0]["call_id"]
    assert repo._events[0].actor_kind == AGENT


# ── what an agent may not do ───────────────────────────────────

def test_an_agent_may_not_delete_on_a_users_behalf(log):
    _, svc = log
    _as_agent()
    with pytest.raises(AgentActionForbidden):
        _run(svc.record("u-1", "data_project", "p-1", "deleted"))


def test_the_refusal_happens_before_anything_is_written(log):
    repo, svc = log
    _as_agent()
    with pytest.raises(AgentActionForbidden):
        _run(svc.record("u-1", "data_project", "p-1", "deleted"))
    assert not repo._events


def test_a_user_deleting_their_own_work_is_fine(log):
    repo, svc = log
    audit_context.set_current(AuditContext(actor_id="u-1"))
    _run(svc.record("u-1", "data_project", "p-1", "deleted"))
    assert repo._events[0].action == "deleted"


def test_the_studio_service_refuses_an_agent_delete(services):
    """Not just the recorder: the row must still be there afterwards.

    delete_project records before it deletes, precisely so the refusal
    happens while there is still something to refuse.
    """
    svc = services["data_project_svc"]
    owner = _run(_owner(services))
    audit_context.set_current(AuditContext(actor_id=owner))
    project = _run(svc.create_project(owner, "Mine"))

    _as_agent(actor_id=owner)
    with pytest.raises(AgentActionForbidden):
        _run(svc.delete_project(owner, project.id))

    audit_context.set_current(AuditContext(actor_id=owner))
    assert _run(svc.get_project(owner, project.id)) is not None


def test_a_user_can_still_delete_their_own_project(services):
    svc = services["data_project_svc"]
    owner = _run(_owner(services))
    audit_context.set_current(AuditContext(actor_id=owner))
    project = _run(svc.create_project(owner, "Mine"))
    _run(svc.delete_project(owner, project.id))
    assert not _run(svc.list_projects(owner))


def test_agent_studio_writes_are_recorded_at_all(services):
    """They were not. This service logged nothing, so a project created by
    a model looked exactly like nothing happening."""
    svc = services["data_project_svc"]
    log = services["activity_repo"]
    owner = _run(_owner(services))
    _as_agent(actor_id=owner)
    project = _run(svc.create_project(owner, "Russian suppliers"))
    _run(svc.add_query(owner, project.id, "Top suppliers", "cypher", "MATCH ..."))
    kinds = [(e.entity_type, e.action, e.actor_kind) for e in log._events]
    assert ("data_project", "created", AGENT) in kinds
    assert ("data_project", "query_added", AGENT) in kinds


# ── the feed can render it ─────────────────────────────────────

def test_the_feed_exposes_the_provenance(log):
    _, svc = log
    _as_agent()
    with audit_context.tool_call("call-abc"):
        _run(svc.record("u-1", "data_project", "p-1", "created", "Suppliers"))
    row = _run(svc.list_for_actor("u-1"))[0]
    assert row["actor_kind"] == AGENT
    assert row["conversation_id"] == "conv-7"
    assert row["message_id"] == "call-abc"

"""What a Studio tool may put in the model's context.

Both cases here cost a real turn in the story-loop harness. Neither was a
model failure: one call returned so much that the next turn could not be
sent at all, and another answered a simple mistake with a database stack
trace instead of the one sentence that would have fixed it.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.assistant.studio_ops import (
    MAX_PROJECTS_LISTED,
    StudioOps,
    _readable_error,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ops(n_projects: int) -> StudioOps:
    svc = MagicMock()
    projects = []
    for i in range(n_projects):
        p = MagicMock()
        p.id, p.name = f"id-{i}", f"project {i}"
        p.queries, p.plots, p.investigation_id = [], [], None
        projects.append(p)
    svc.list_projects = AsyncMock(return_value=projects)
    return StudioOps(svc, "user-1")


class TestTheProjectListIsBounded:
    """3,930 projects came back as one 316,559-token tool result, against a
    32k window. The turn could not be sent."""

    def test_a_small_account_is_returned_whole_with_no_note(self):
        out = _run(_ops(3).list_projects())
        assert len(out["projects"]) == 3
        assert "note" not in out, (
            "a note about truncation that did not happen is noise in the "
            "context it exists to protect"
        )

    def test_a_large_account_is_capped(self):
        out = _run(_ops(3930).list_projects())
        assert len(out["projects"]) == MAX_PROJECTS_LISTED

    def test_the_cap_says_it_capped_and_how_many_there_are(self):
        out = _run(_ops(3930).list_projects())
        assert "3930" in out["note"], (
            "silently truncating leaves the model believing it has seen "
            "everything, which is how it concludes a project is missing"
        )

    def test_the_newest_survive_the_cap(self):
        # The repo orders by updated_at desc, so position is recency.
        out = _run(_ops(100).list_projects())
        assert out["projects"][0]["name"] == "project 0"


class TestAnErrorTheModelCanActOn:

    def test_a_driver_error_does_not_carry_its_sql(self):
        exc = RuntimeError(
            "DBAPIError: (asyncpg.exceptions.DataError) invalid input for "
            "query argument $1: 'My Project Name' (invalid UUID 'My Project "
            "Name': length must be between 32..36 characters, got 71)\n"
            "[SQL: SELECT data_projects.id, data_projects.name, "
            "data_projects.created_at FROM data_projects WHERE ...]")
        out = _readable_error(exc, "mcp__gmr__studio_get_project")
        assert "SELECT" not in json.dumps(out), (
            "a failing statement in the model's context is tokens spent "
            "teaching it nothing"
        )

    def test_it_says_the_thing_that_actually_fixes_it(self):
        exc = RuntimeError("invalid UUID 'My Project Name'")
        out = _readable_error(exc, "mcp__gmr__studio_add_query")
        assert "id, not the name" in out["hint"], (
            "create_project answers with {id, name} and the name is the "
            "readable one; the model needs telling which to pass back"
        )

    def test_ordinary_service_errors_are_passed_through(self):
        # Permission and not-found errors already say what to fix.
        out = _readable_error(PermissionError("not your project"), "t")
        assert "not your project" in out["error"]
        assert "hint" not in out

    def test_a_long_message_is_capped(self):
        out = _readable_error(RuntimeError("x" * 5000), "t")
        assert len(out["error"]) < 400

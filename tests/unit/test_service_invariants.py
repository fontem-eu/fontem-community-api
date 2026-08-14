"""The service enforces its own rules, whoever is calling.

The Data Studio has two callers: HTTP handlers, and the assistant's tools
calling in-process. That is the right shape — authorization lives in the
service, so both callers are governed by it — but some limits lived only in
the router's Pydantic models, which made them rules about HTTP requests
rather than rules about data.

The gap ran both ways:

  * an agent could store a query of any length, while the same operation
    over HTTP was capped at 8000 characters;
  * the HTTP API accepted any `lang` string up to 20 characters, while the
    tool schema offered only cypher/sql/sparql — so the agent was in fact
    the more constrained caller.

Both are now the service's business. These tests call the service directly,
which is precisely the path that used to skip the rules.
"""
import asyncio

import pytest

from src.services.data_project_service import (
    MAX_QUERY_CHARS,
    MAX_SPEC_BYTES,
    QUERY_LANGS,
)
from src.services.exceptions import InvalidInput
from tests.conftest import _stable_uuid, seed_user


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(name="studio")
def _studio(services):
    _run(seed_user(services["user_repo"], "owner"))
    owner = _stable_uuid("owner")
    svc = services["data_project_svc"]
    project = _run(svc.create_project(owner, "P"))
    return svc, owner, project.id


def test_an_over_long_query_is_refused(studio):
    svc, owner, pid = studio
    with pytest.raises(InvalidInput) as e:
        _run(svc.add_query(owner, pid, "big", "cypher", "x" * (MAX_QUERY_CHARS + 1)))
    assert str(MAX_QUERY_CHARS) in str(e.value)


def test_a_query_at_the_limit_is_accepted(studio):
    svc, owner, pid = studio
    q = _run(svc.add_query(owner, pid, "ok", "cypher", "x" * MAX_QUERY_CHARS))
    assert len(q.query) == MAX_QUERY_CHARS


def test_the_query_is_refused_not_truncated(studio):
    # Truncating a query changes which rows it returns. Silently answering a
    # different question is worse than refusing to store it.
    svc, owner, pid = studio
    with pytest.raises(InvalidInput):
        _run(svc.add_query(owner, pid, "big", "cypher", "y" * (MAX_QUERY_CHARS + 5)))
    assert not _run(svc.get_project(owner, pid)).queries


def test_updating_a_query_is_checked_too(studio):
    svc, owner, pid = studio
    q = _run(svc.add_query(owner, pid, "ok", "cypher", "MATCH (n) RETURN n"))
    with pytest.raises(InvalidInput):
        _run(svc.update_query(owner, pid, q.id, None, None, "z" * (MAX_QUERY_CHARS + 1)))


@pytest.mark.parametrize("lang", QUERY_LANGS)
def test_every_offered_language_is_accepted(studio, lang):
    svc, owner, pid = studio
    assert _run(svc.add_query(owner, pid, "q", lang, "x")).lang == lang


def test_an_unsupported_language_is_refused(studio):
    # The HTTP API accepted this; the tool schema did not. Now neither does.
    svc, owner, pid = studio
    with pytest.raises(InvalidInput) as e:
        _run(svc.add_query(owner, pid, "q", "banana", "x"))
    assert "banana" in str(e.value)


def test_an_absent_language_still_defaults(studio):
    svc, owner, pid = studio
    assert _run(svc.add_query(owner, pid, "q", "", "x")).lang == "cypher"


def test_an_over_large_plot_spec_is_refused(studio):
    svc, owner, pid = studio
    with pytest.raises(InvalidInput):
        _run(svc.add_plot(owner, pid, "p", {"blob": "x" * (MAX_SPEC_BYTES + 10)}))


def test_an_ordinary_plot_spec_is_fine(studio):
    svc, owner, pid = studio
    plot = _run(svc.add_plot(owner, pid, "p", {"chart": "bar", "x": "year"}))
    assert plot.spec["chart"] == "bar"


def test_a_long_name_is_still_truncated_rather_than_refused(studio):
    # The existing behaviour, deliberately kept: a shortened label is still
    # the same project, so refusing would be worse than trimming.
    svc, owner, pid = studio
    q = _run(svc.add_query(owner, pid, "n" * 500, "cypher", "x"))
    assert len(q.name) == 300

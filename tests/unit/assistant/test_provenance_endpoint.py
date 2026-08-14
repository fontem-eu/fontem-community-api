"""Reading an agent action back to the prompt that caused it.

An activity entry written by the assistant names the tool call that caused
it. That id is only useful if it can be turned back into the exchange it
came from — otherwise "the assistant created this project" is something a
user has to take on faith, which is the opposite of what the audit trail is
for.

Ownership matters more here than in most places: a message id names somebody
else's conversation, and the answer to "is this id real" is itself a
disclosure.
"""
# pylint: disable=protected-access
import asyncio

import pytest

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service(repo):
    return AssistantService(
        repo=repo, proxy_client=None, base_system_prompt="s",
        turn_limits=TurnLimits(max_turns=20, max_chars=12_000),
        context_char_budget=8_000,
    )


def _seed(repo, user="u-1"):
    """One conversation with two turns, the first of which used two tools."""
    conv = _run(repo.find_or_create_conversation(user, "global"))
    add = lambda role, content, **kw: _run(repo.append_message(  # noqa: E731
        conversation_id=conv.id, user_id=user, role=role, content=content,
        tokens_in=None, tokens_out=None, model=kw.pop("model", None), **kw))
    add("user", "who supplies Russia?")
    first = add("tool", "mcp__gmr__search_entities",
                extras={"args": {"query": "Russia"}, "bytes": 1557,
                        "elapsed": 0.4, "truncated": False},
                message_id="call-1")
    add("tool", "mcp__gmr__studio_create_project",
        extras={"args": {"name": "Russian suppliers"}}, message_id="call-2")
    add("assistant", "I made you a project.", model="qwen3-4b")
    add("user", "a later, unrelated question")
    add("tool", "mcp__gmr__search_entities",
        extras={"args": {"query": "other"}}, message_id="call-3")
    add("assistant", "Different answer.", model="qwen3-4b")
    return conv, first


def test_it_returns_the_prompt_that_caused_the_call():
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-2"))
    assert turn["prompt"]["content"] == "who supplies Russia?"


def test_it_returns_the_whole_tool_sequence_not_just_the_one_asked_for():
    # "What led to this" is the sequence, not the single call: the project
    # was created because a search came back first.
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-2"))
    assert [c["tool"] for c in turn["calls"]] == [
        "mcp__gmr__search_entities", "mcp__gmr__studio_create_project"]


def test_it_marks_which_call_was_asked_about():
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-1"))
    assert [c["is_subject"] for c in turn["calls"]] == [True, False]


def test_it_returns_the_answer_and_the_model_that_wrote_it():
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-1"))
    assert turn["answer"]["content"] == "I made you a project."
    assert turn["answer"]["model"] == "qwen3-4b"


def test_it_does_not_bleed_into_the_next_turn():
    # The later question and its call belong to a different exchange.
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-2"))
    assert "call-3" not in [c["id"] for c in turn["calls"]]
    assert turn["prompt"]["content"] != "a later, unrelated question"


def test_a_call_in_the_later_turn_gets_that_turn():
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-3"))
    assert turn["prompt"]["content"] == "a later, unrelated question"
    assert [c["id"] for c in turn["calls"]] == ["call-3"]


def test_the_arguments_are_included_and_the_result_is_not():
    repo = InMemoryAssistRepository()
    _seed(repo)
    turn = _run(_service(repo).turn_for_message("u-1", "call-1"))
    call = turn["calls"][0]
    assert call["args"] == {"query": "Russia"}
    assert "result" not in call


def test_someone_elses_message_is_not_found():
    # Not "forbidden": that would confirm the id exists.
    repo = InMemoryAssistRepository()
    _seed(repo)
    assert _run(_service(repo).turn_for_message("u-2", "call-1")) is None


def test_an_unknown_id_is_not_found_either():
    repo = InMemoryAssistRepository()
    _seed(repo)
    assert _run(_service(repo).turn_for_message("u-1", "no-such-call")) is None


def test_a_turn_with_no_answer_yet_still_explains_itself():
    # A turn that errored after its tools ran still has a prompt and calls,
    # and that is exactly when someone asks what happened.
    repo = InMemoryAssistRepository()
    conv = _run(repo.find_or_create_conversation("u-1", "k"))
    _run(repo.append_message(conversation_id=conv.id, user_id="u-1", role="user",
                             content="do a thing", tokens_in=None,
                             tokens_out=None, model=None))
    _run(repo.append_message(conversation_id=conv.id, user_id="u-1", role="tool",
                             content="mcp__gmr__search_entities", tokens_in=None,
                             tokens_out=None, model=None,
                             extras={"args": {"q": "x"}}, message_id="only-call"))
    turn = _run(_service(repo).turn_for_message("u-1", "only-call"))
    assert turn["prompt"]["content"] == "do a thing"
    assert turn["answer"] is None

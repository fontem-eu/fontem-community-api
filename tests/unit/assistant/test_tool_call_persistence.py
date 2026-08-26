"""What the agent did has to outlive the stream that showed it.

Tool calls used to exist only as `tool_result` SSE events: the panel drew
them, and a page reload lost them. Nothing in the database said the agent
had searched, investigated, or written to a Studio project — production had
31 assistant messages, every `extras` empty and every `model` NULL.

These pin the record: one row per call, naming the tool, its arguments and
what came back, addressable by the id minted where the call ran. The result
is capped at MAX_TOOL_RESULT_CHARS so a tool returning 90k of JSON cannot
make the conversation store mostly tool output.
"""
import asyncio
import json

from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest
from src.assistant.context import TurnLimits


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _FakeProxy:
    """Emits a scripted stream, the way an executor would."""

    def __init__(self, blocks):
        self._blocks = blocks

    async def stream(self, payload):
        del payload
        for b in self._blocks:
            yield b


def _service(blocks, repo=None):
    return AssistantService(
        repo=repo or InMemoryAssistRepository(),
        proxy_client=_FakeProxy(blocks),
        base_system_prompt="sys",
        turn_limits=TurnLimits(max_turns=20, max_chars=12_000),
        context_char_budget=8_000,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _turn(svc, **over):
    req = ChatRequest(
        user_id="u-1", conversation_key="global", message="find Siemens",
        context_block="", **over,
    )
    return [b async for b in svc.turn(req)]


TOOL_EVENT = {
    "call_id": "call-abc",
    "tool": "mcp__gmr__search_entities",
    "args": {"query": "Siemens"},
    "result": "[{...}]",
    "bytes": 1557,
    "truncated": False,
    "elapsed": 0.4,
}


def _messages(repo):
    conv = repo._conversations  # pylint: disable=protected-access
    conv_id = next(iter(conv))
    return _run(repo.list_messages(conv_id))


def test_a_tool_call_becomes_a_row():
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", TOOL_EVENT),
                    _sse("chunk", {"text": "Found it."}),
                    _sse("done", {})], repo)
    _run(_turn(svc))
    tools = [m for m in _messages(repo) if m.role == "tool"]
    assert len(tools) == 1
    assert tools[0].content == "mcp__gmr__search_entities"


def test_the_row_keeps_the_arguments():
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", TOOL_EVENT), _sse("done", {})], repo)
    _run(_turn(svc))
    tool = [m for m in _messages(repo) if m.role == "tool"][0]
    assert tool.extras["args"] == {"query": "Siemens"}


def test_the_row_keeps_the_result():
    # The decision changed. Dropping the result kept the store small and cost
    # the next turn any knowledge of what the last one found — the model
    # re-ran searches it had already run, and the panel rendered historical
    # tool bubbles with an empty body. Under a per-model budget the size
    # trade-off no longer has to be made the same way for every model.
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", TOOL_EVENT), _sse("done", {})], repo)
    _run(_turn(svc))
    tool = [m for m in _messages(repo) if m.role == "tool"][0]
    assert tool.extras["result"] == TOOL_EVENT["result"]
    # The name still lives in content — provenance reads it there.
    assert tool.content == TOOL_EVENT["tool"]


def test_a_stored_result_is_capped():
    # One row can never be unbounded, whatever the tool returned.
    from src.assistant import tool_budget

    big = dict(TOOL_EVENT, result="x" * (tool_budget.MAX_TOOL_RESULT_CHARS + 5_000))
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", big), _sse("done", {})], repo)
    _run(_turn(svc))
    tool = [m for m in _messages(repo) if m.role == "tool"][0]
    assert len(tool.extras["result"]) == tool_budget.MAX_TOOL_RESULT_CHARS


def test_the_row_is_addressable_by_the_id_minted_where_it_ran():
    # This is the whole linkage: an activity entry points here.
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", TOOL_EVENT), _sse("done", {})], repo)
    _run(_turn(svc))
    tool = [m for m in _messages(repo) if m.role == "tool"][0]
    assert tool.id == "call-abc"


def test_the_row_records_size_even_though_the_body_is_dropped():
    # "The tool returned 1557 bytes and the model saw all of it" is worth
    # keeping; the bytes themselves are not.
    repo = InMemoryAssistRepository()
    svc = _service([_sse("tool_result", TOOL_EVENT), _sse("done", {})], repo)
    _run(_turn(svc))
    tool = [m for m in _messages(repo) if m.role == "tool"][0]
    assert tool.extras["bytes"] == 1557
    assert tool.extras["truncated"] is False


def test_several_calls_in_one_turn_are_all_recorded_in_order():
    repo = InMemoryAssistRepository()
    second = {**TOOL_EVENT, "call_id": "call-def",
              "tool": "mcp__gmr__investigate_entity"}
    svc = _service([_sse("tool_result", TOOL_EVENT),
                    _sse("tool_result", second),
                    _sse("chunk", {"text": "done"}),
                    _sse("done", {})], repo)
    _run(_turn(svc))
    tools = [m for m in _messages(repo) if m.role == "tool"]
    assert [t.content for t in tools] == [
        "mcp__gmr__search_entities", "mcp__gmr__investigate_entity"]


def test_a_malformed_event_does_not_take_the_turn_down():
    repo = InMemoryAssistRepository()
    svc = _service(["event: tool_result\ndata: {not json\n\n",
                    _sse("tool_result", {"args": {}}),  # no tool name
                    _sse("chunk", {"text": "still fine"}),
                    _sse("done", {})], repo)
    _run(_turn(svc))
    assert not [m for m in _messages(repo) if m.role == "tool"]
    assert [m for m in _messages(repo) if m.role == "assistant"]


def test_the_assistant_row_records_which_model_answered():
    # Every row in production was NULL, so "which model did this" had no
    # answer at all.
    repo = InMemoryAssistRepository()
    svc = _service([_sse("chunk", {"text": "hi"}), _sse("done", {})], repo)
    _run(_turn(svc, local_model_id="qwen3-4b"))
    row = [m for m in _messages(repo) if m.role == "assistant"][0]
    assert row.model == "qwen3-4b"


def test_a_byok_turn_records_the_provider_model_not_the_built_in_one():
    repo = InMemoryAssistRepository()
    svc = _service([_sse("chunk", {"text": "hi"}), _sse("done", {})], repo)
    _run(_turn(svc, credential=("mistral", "sk-x", "mistral-large-latest")))
    row = [m for m in _messages(repo) if m.role == "assistant"][0]
    assert row.model == "mistral-large-latest"


def test_the_real_input_token_count_replaces_the_estimate():
    # The bug this replaces: the correction read a detached dataclass and
    # assigned to it, so Postgres kept the estimate forever.
    repo = InMemoryAssistRepository()
    svc = _service([_sse("chunk", {"text": "hi"}),
                    _sse("usage", {"input_tokens": 4321, "output_tokens": 7}),
                    _sse("done", {})], repo)
    _run(_turn(svc))
    user_row = [m for m in _messages(repo) if m.role == "user"][0]
    assert user_row.tokens_in == 4321


def test_an_absent_usage_event_leaves_the_estimate_alone():
    repo = InMemoryAssistRepository()
    svc = _service([_sse("chunk", {"text": "hi"}), _sse("done", {})], repo)
    _run(_turn(svc))
    user_row = [m for m in _messages(repo) if m.role == "user"][0]
    assert user_row.tokens_in and user_row.tokens_in > 0


# ── the database's opinion ─────────────────────────────────────
#
# The in-memory repository has no constraints, so it accepts any role the
# code invents. Postgres does not: `ck_assist_msg_role` allowed only 'user'
# and 'assistant', and the first real turn in testing died on it — taking
# the whole turn with it, because the failed flush poisoned the transaction
# the rest of the turn was writing in. 1064 tests were green at the time.
#
# These read the constraint off the model, which is what the migration
# mirrors, so a new role has to be declared in both places or fail here.

def _allowed_roles() -> set[str]:
    import re  # pylint: disable=import-outside-toplevel
    from sqlalchemy import CheckConstraint  # pylint: disable=import-outside-toplevel
    from src.assistant.models import AssistMessageModel  # pylint: disable=import-outside-toplevel

    for c in AssistMessageModel.__table__.constraints:
        if isinstance(c, CheckConstraint) and c.name == "ck_assist_msg_role":
            return set(re.findall(r"'([a-z]+)'", str(c.sqltext)))
    raise AssertionError("ck_assist_msg_role is gone; the database still has it")


def test_the_roles_the_service_writes_are_all_permitted():
    # Every role that reaches append_message anywhere in the turn.
    assert {"user", "assistant", "tool"} <= _allowed_roles()


def test_an_invented_role_would_still_be_rejected():
    # The constraint is worth keeping narrow: it is what stops a typo
    # becoming a row nothing knows how to render.
    assert "banana" not in _allowed_roles()

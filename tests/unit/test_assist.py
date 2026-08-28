"""Endpoint-level tests for the assistant router.

These use the TestClient + dependency overrides to replace the live
proxy with a fake. They verify the HTTP contract only — business
logic of the turn is already covered by test_service.py.
"""
# pylint: disable=protected-access,redefined-outer-name,unused-argument
# ── pytest fixtures shadow the fixture-name parameter on every test
#    that consumes them; that's the canonical pytest pattern. The
#    ``fake_assistant`` fixture is autouse=True so passing it as a
#    test parameter is only there to declare the dependency.
from __future__ import annotations

import asyncio
import json

import pytest

from src.api.rate_limit import ANONYMOUS_ASSIST_LIMIT, limiter
from src.assistant import local_models, navigation
from src.assistant.context import TurnLimits
from src.assistant.engine_tools import ANONYMOUS_TOOLS, turn_tool_specs
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import ANONYMOUS_MAX_PROMPT_CHARS, AssistantService
from src.assistant.tool_runtime import ToolRuntime
from tests.conftest import make_headers, seed_user


class _FakeProxy:
    def __init__(self, events: list[str] | None = None) -> None:
        self._events = events or [
            "event: chunk\ndata: {\"text\": \"Hello\"}\n\n",
            "event: chunk\ndata: {\"text\": \" world\"}\n\n",
        ]

    async def stream(self, payload):
        for line in self._events:
            yield line


@pytest.fixture(autouse=True)
def fake_assistant(services):
    """Inject a fake-proxy-backed AssistantService into the services dict.

    The dishka InMemoryProvider picks this up and provides it via
    FromDishka[AssistantService] to the router endpoints.
    """
    repo = InMemoryAssistRepository()
    proxy = _FakeProxy()
    service = AssistantService(
        repo=repo,
        proxy_client=proxy,
        base_system_prompt="You are a test assistant.",
        turn_limits=TurnLimits(),
        context_char_budget=8000,
    )
    services["assistant_service"] = service
    services["assist_repo"] = repo
    yield repo


class TestAssistRouter:

    def test_chat_stream_returns_sse(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat/stream",
            json={
                "message": "What are the top contractors in Germany?",
                "conversation_key": "report:abc",
                "context_block": "Report: Germany contractors",
            },
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "Hello" in body
        assert "world" in body

    def test_chat_stream_serves_signed_out_visitors(self, client, fake_assistant):
        # This used to assert 401. The contract changed deliberately: the
        # assistant's first job on a public platform is helping a visitor
        # find their way around, and that has to work before they have an
        # account. What a signed-out turn is *allowed to do* is the subject
        # of TestAnonymousAssistant below — this only pins that it answers.
        resp = client.post(
            "/assist/chat/stream",
            json={
                "message": "hi",
                "conversation_key": "k",
                "context_block": "",
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_chat_stream_rejects_empty_message(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat/stream",
            json={"message": "", "conversation_key": "k", "context_block": ""},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 422

    def test_usage_starts_at_zero(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.get("/assist/usage", headers=make_headers("user-1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["tokens_1h"] == 0
        assert data["tokens_24h"] == 0
        assert data["tokens_7d"] == 0

    def test_usage_reflects_recorded_turn(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        client.post(
            "/assist/chat/stream",
            json={
                "message": "hello world of pharma contracting",
                "conversation_key": "k",
                "context_block": "",
            },
            headers=h,
        )
        data = client.get("/assist/usage", headers=h).json()
        assert data["tokens_1h"] > 0
        assert data["tokens_24h"] == data["tokens_1h"]

    def test_usage_isolates_users(self, client, services, fake_assistant):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-2")
        )
        client.post(
            "/assist/chat/stream",
            json={"message": "q", "conversation_key": "k", "context_block": ""},
            headers=make_headers("user-1"),
        )
        data2 = client.get("/assist/usage", headers=make_headers("user-2")).json()
        assert data2["tokens_1h"] == 0

    def test_get_conversation_returns_empty_for_new_key(
        self, client, services, fake_assistant
    ):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.get(
            "/assist/conversations/report:fresh",
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_get_conversation_returns_recorded_messages(
        self, client, services, fake_assistant
    ):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        client.post(
            "/assist/chat/stream",
            json={
                "message": "first question",
                "conversation_key": "report:abc",
                "context_block": "",
            },
            headers=h,
        )
        resp = client.get(
            "/assist/conversations/report:abc",
            headers=h,
        )
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "first question"
        assert data["messages"][1]["role"] == "assistant"


# ── The assistant a signed-out visitor gets ────────────────────
#
# The chat endpoint answers without a session, but as a strictly smaller
# assistant, and each reduction is enforced rather than requested:
#
#   * the smallest model we offer, chosen server-side;
#   * `navigate` as the only tool, withheld when the tool list is built and
#     refused again when a call is dispatched;
#   * nothing written — no conversation, no messages, no usage.
#
# They are separate tests because they fail differently. A model that
# quietly upgrades costs money; a tool that quietly executes acts on the
# graph for an unauthenticated caller; a write that quietly happens lets the
# internet fill the tables the usage reports read.

NAV = {
    "path": "/companies",
    "routes": [{"path": "/companies", "description": "Company search"},
               {"path": "/about", "description": "About the platform"}],
}


class _RecordingProxy:
    """A proxy that keeps the payloads it was handed.

    What the service SENDS is the subject here, not what the model says
    back: an assistant offered a tool it should not have has already failed,
    whether or not it chooses to call it.
    """

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def stream(self, payload):
        self.payloads.append(payload)
        yield "event: chunk\ndata: {\"text\": \"Hello\"}\n\n"


@pytest.fixture
def recording(services):
    repo = InMemoryAssistRepository()
    proxy = _RecordingProxy()
    services["assistant_service"] = AssistantService(
        repo=repo,
        proxy_client=proxy,
        base_system_prompt="You are a test assistant.",
        turn_limits=TurnLimits(),
        context_char_budget=8000,
    )
    services["assist_repo"] = repo
    return proxy, repo


def _anon_post(client, **extra):
    body = {"message": "where do I find companies?",
            "conversation_key": "standalone:x", "context_block": ""}
    body.update(extra)
    return client.post("/assist/chat/stream", json=body)


def _gen_tools() -> list[dict]:
    return [{"type": "function",
             "function": {"name": "get_doc", "description": "d",
                          "parameters": {"type": "object", "properties": {}}}}]


class TestAnonymousAssistant:

    # ── the model is ours to choose ────────────────────────────

    def test_the_turn_asks_for_the_least_powerful_model(self, client, recording):
        _anon_post(client)
        proxy, _ = recording
        assert proxy.payloads[0]["local_model_id"] == \
            local_models.ANONYMOUS_MODEL_ID

    def test_the_anonymous_model_is_the_smallest_one_offered(self):
        # ANONYMOUS_MODEL_ID is written out rather than derived from the
        # order of LOCAL_MODELS, so this is what stops the two drifting:
        # reorder that tuple, or add a smaller model, and this fails.
        def billions(model) -> float:
            # "qwen3-1.7b" -> 1.7. True of every LOCAL id; the hosted ones are
            # named after their product ("minimax-m3", "ox-alpha") and carry no
            # parseable size, which is fine because they are excluded below.
            return float(model.id.rsplit("-", 1)[1].rstrip("b"))

        chosen = local_models.resolve(local_models.ANONYMOUS_MODEL_ID)
        assert chosen.id == local_models.ANONYMOUS_MODEL_ID, \
            "the anonymous model must be one we actually offer"
        # Never a hosted model: an anonymous turn carries no account and no
        # metering, so one we pay for is billable by anyone who can reach the
        # endpoint. local_models.anonymous_model_id() enforces this; this is
        # the second pair of eyes on the constant it reads.
        assert not chosen.hosted, \
            "the anonymous model must not be one the platform pays for"
        local = [m for m in local_models.LOCAL_MODELS if not m.hosted]
        assert billions(chosen) == min(billions(m) for m in local)

    def test_the_caller_cannot_name_the_model(self, client, recording):
        # Pinned on the outcome rather than the absence of a field: if a
        # model ever becomes part of the body, it must not become a way to
        # spend the big one without an account.
        _anon_post(client, local_model_id="qwen3-8b", model="qwen3-8b")
        proxy, _ = recording
        assert proxy.payloads[0]["local_model_id"] == \
            local_models.ANONYMOUS_MODEL_ID

    def test_no_credential_travels_with_an_anonymous_turn(self, client, recording):
        _anon_post(client)
        proxy, _ = recording
        assert "credential" not in proxy.payloads[0]

    # ── the tool surface ───────────────────────────────────────

    def test_the_turn_is_marked_anonymous(self, client, recording):
        _anon_post(client, nav=NAV)
        proxy, _ = recording
        assert proxy.payloads[0]["anonymous"] is True

    def test_no_studio_ops_are_built(self, client, recording):
        _anon_post(client)
        proxy, _ = recording
        assert "studio_ops" not in proxy.payloads[0]

    def test_no_editing_surface_is_claimed(self, client, recording):
        # Even when the client says there is one: propose_edit needs an
        # account to propose on behalf of.
        _anon_post(client, has_editor=True)
        proxy, _ = recording
        assert proxy.payloads[0]["has_editor"] is False

    def test_only_navigate_is_offered(self):
        specs = turn_tool_specs(_gen_tools(), has_editor=True,
                                nav_routes=NAV["routes"], anonymous=True)
        assert [s["function"]["name"] for s in specs] == \
            [navigation.NAVIGATE_TOOL_NAME]

    def test_nothing_is_offered_without_a_site_map(self):
        # navigate cannot succeed with no routes to validate against.
        assert turn_tool_specs(_gen_tools(), has_editor=True, nav_routes=[],
                               anonymous=True) == []

    def test_a_signed_in_turn_still_gets_the_full_surface(self):
        specs = turn_tool_specs(_gen_tools(), has_editor=True,
                                nav_routes=NAV["routes"])
        names = [s["function"]["name"] for s in specs]
        assert navigation.NAVIGATE_TOOL_NAME in names
        assert "mcp__gmr__search_entities" in names
        assert len(names) > 1

    def test_a_signed_in_turn_is_not_marked_anonymous(
        self, client, services, recording,
    ):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        client.post(
            "/assist/chat/stream",
            json={"message": "hi", "conversation_key": "k",
                  "context_block": ""},
            headers=make_headers("user-1"),
        )
        proxy, _ = recording
        assert proxy.payloads[-1].get("anonymous") is not True

    # ── nothing is written ─────────────────────────────────────

    def test_no_conversation_and_no_messages(self, client, recording):
        _anon_post(client)
        _, repo = recording
        assert repo._conversations == {}
        assert repo._messages == []

    def test_a_repeated_key_still_stores_nothing(self, client, recording):
        # conversation_key is caller-chosen; the same key twice must not
        # accumulate anything either.
        _anon_post(client, conversation_key="standalone:same")
        _anon_post(client, conversation_key="standalone:same")
        _, repo = recording
        assert repo._conversations == {}
        assert repo._messages == []

    def test_the_model_is_told_there_is_no_history(self, client, recording):
        _anon_post(client, conversation_key="standalone:same")
        _anon_post(client, conversation_key="standalone:same",
                   message="and the second one?")
        proxy, _ = recording
        # The second turn's prompt cannot carry the first turn's text: there
        # is nowhere for it to have been kept.
        assert "where do I find companies?" not in proxy.payloads[1]["system"]

    # ── everything else still needs an account ─────────────────

    @pytest.mark.parametrize("method,path", [
        ("get", "/assist/usage"),
        ("get", "/assist/usage-history"),
        ("get", "/assist/models"),
        ("get", "/assist/credentials"),
        ("get", "/assist/conversations/standalone:x"),
        ("get", "/assist/provenance/00000000-0000-0000-0000-000000000000"),
        ("delete", "/assist/conversations"),
        ("post", "/assist/mcp-tokens"),
    ])
    def test_the_rest_of_the_assistant_stays_private(
        self, client, recording, method, path,
    ):
        resp = getattr(client, method)(path)
        assert resp.status_code in (401, 403), (
            f"{path} must not be reachable without an account"
        )


class TestAnonymousPromptCap:
    """How much a stranger may ask at once.

    Its own class rather than more methods on TestAnonymousAssistant: the
    other limits shape what the turn IS, and this one is a rule about the
    request, checked before the turn exists at all.
    """

    def test_the_cap_is_the_number_the_panel_mirrors(self):
        # AssistPanel.vue in fontem-web hardcodes 1000 as its `maxlength`,
        # and its own test pins that. There is no shared source for the
        # number, so every other test here reads the constant symbolically
        # and would happily pass if it were raised to 100_000. This is the
        # one that notices — change it here and the panel needs changing
        # too, or the input and the server disagree about the same rule.
        assert ANONYMOUS_MAX_PROMPT_CHARS == 1_000

    def test_a_message_at_the_limit_is_accepted(self, client, recording):
        resp = _anon_post(client, message="x" * ANONYMOUS_MAX_PROMPT_CHARS)
        assert resp.status_code == 200

    def test_a_message_over_the_limit_is_refused(self, client, recording):
        resp = _anon_post(client, message="x" * (ANONYMOUS_MAX_PROMPT_CHARS + 1))
        assert resp.status_code == 422

    def test_the_refusal_says_what_the_limit_is(self, client, recording):
        # The panel mirrors this number, so a caller that hits the server
        # limit has already got past the input's own cap — the message has
        # to be enough to act on without reading our source.
        resp = _anon_post(client, message="x" * (ANONYMOUS_MAX_PROMPT_CHARS + 50))
        detail = str(resp.json().get("detail", ""))
        assert str(ANONYMOUS_MAX_PROMPT_CHARS) in detail
        assert "sign in" in detail.lower()

    def test_an_over_long_message_never_reaches_the_model(self, client, recording):
        _anon_post(client, message="x" * (ANONYMOUS_MAX_PROMPT_CHARS + 1))
        proxy, _ = recording
        assert not proxy.payloads, (
            "a refused message must not be sent upstream — the point of the "
            "cap is the turn it prevents, not the status code"
        )

    def test_a_signed_in_user_has_no_such_cap(self, client, services, recording):
        # The cap is a property of the anonymous turn. A signed-in user is
        # metered by account and may paste an article at it.
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat/stream",
            json={"message": "x" * (ANONYMOUS_MAX_PROMPT_CHARS * 3),
                  "conversation_key": "k", "context_block": ""},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200

    def test_the_cap_is_checked_before_the_rate_limit(self, client, recording):
        # An over-long message must not spend the hour's allowance. Pinned
        # because the two checks are adjacent and the order is the whole
        # difference between "you were told" and "you were charged".
        limiter.enabled = True
        limiter.reset()
        try:
            for _ in range(int(ANONYMOUS_ASSIST_LIMIT.split("/", maxsplit=1)[0]) + 5):
                resp = _anon_post(
                    client, message="x" * (ANONYMOUS_MAX_PROMPT_CHARS + 1))
                assert resp.status_code == 422
            # The allowance is untouched, so a normal question still works.
            assert _anon_post(client).status_code == 200
        finally:
            limiter.reset()
            limiter.enabled = False


class _SpyClient:
    """Records every HTTP call a tool would make. None is the point."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, url, params=None, **_):
        del params
        self.calls.append(url)
        raise AssertionError("an anonymous turn must not reach fontem-api")


def _dispatch(name, args, allowed):
    runtime = ToolRuntime(gmr_api_url="http://fake")
    spy = _SpyClient()
    pending_nav: list = []
    loop = asyncio.new_event_loop()
    try:
        out, _ = loop.run_until_complete(runtime.dispatch(
            spy, name, args, studio=None, nav_routes=NAV["routes"],
            pending_nav=pending_nav, budget=[100_000], name_cache={},
            allowed=allowed,
        ))
    finally:
        loop.close()
    return json.loads(out), spy, pending_nav


class TestDispatchRefusesWithoutAnAccount:
    """The second lock.

    `turn_tool_specs` already withholds everything but navigate, so a
    well-behaved turn never dispatches anything else. These pin the
    guarantee rather than the prompt: the spec list is built partly from
    tools fetched over HTTP at turn time, and "the model was only offered
    safe tools" is an argument about a prompt, not about execution.
    """

    def test_a_withheld_tool_is_refused(self):
        out, spy, _ = _dispatch(
            "mcp__gmr__search_entities", {"query": "siemens"}, ANONYMOUS_TOOLS)
        assert "signed-out" in out["error"]
        # Refused, not merely reported: nothing was fetched.
        assert not spy.calls

    def test_a_studio_write_is_refused_before_it_runs(self):
        # Studio ops are dispatched by name ahead of the fontem-api path, so
        # this must be refused on the name rather than by StudioOps being
        # absent — otherwise the refusal is an accident of wiring.
        out, _, _ = _dispatch("mcp__gmr__studio_create_project",
                              {"name": "mine"}, ANONYMOUS_TOOLS)
        assert "signed-out" in out["error"]

    def test_navigate_still_works(self):
        out, _, pending = _dispatch(
            "navigate", {"path": "/about"}, ANONYMOUS_TOOLS)
        assert not out.get("error")
        assert pending, "navigate must still emit the event that moves the UI"

    def test_an_unrestricted_turn_is_not_affected(self):
        # allowed=None is every signed-in turn; the guard must be inert.
        out, spy, _ = _dispatch("navigate", {"path": "/about"}, None)
        assert not spy.calls
        assert "signed-out" not in json.dumps(out)


class TestAnonymousRateLimit:
    """The cost of an open door."""

    @pytest.fixture(autouse=True)
    def _enabled(self):
        # The suite disables the limiter globally (tests/conftest.py); these
        # are the tests that need it on.
        limiter.enabled = True
        limiter.reset()
        yield
        limiter.reset()
        limiter.enabled = False

    @staticmethod
    def _allowance() -> int:
        return int(ANONYMOUS_ASSIST_LIMIT.split("/", maxsplit=1)[0])

    @staticmethod
    def _turn(client, ip, **headers):
        return client.post(
            "/assist/chat/stream",
            json={"message": "hi", "conversation_key": "k",
                  "context_block": ""},
            headers={"x-forwarded-for": ip, **headers},
        )

    def test_an_ip_is_cut_off_after_its_allowance(self, client, recording):
        for i in range(self._allowance()):
            assert self._turn(client, "203.0.113.9").status_code == 200, \
                f"turn {i} should have been served"
        assert self._turn(client, "203.0.113.9").status_code == 429

    def test_the_limit_is_per_ip(self, client, recording):
        for _ in range(self._allowance()):
            self._turn(client, "203.0.113.9")
        # A different visitor is not punished for the first one's spending.
        assert self._turn(client, "198.51.100.4").status_code == 200

    def test_a_signed_in_user_does_not_share_the_bucket(
        self, client, services, recording,
    ):
        # Guards STORY-12's shape: an IP-keyed limit on a route the smoke
        # suite hammers from a single address. Signed-in turns must not
        # count against it, or an office shares one ceiling.
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        for _ in range(self._allowance() + 5):
            resp = self._turn(client, "203.0.113.9", **make_headers("user-1"))
            assert resp.status_code == 200


class TestSilentDowngradeIsGone:
    """A caller PRESENTING credentials is never quietly served as anonymous.

    The trap this pins shut: an expired session put the platform owner on
    the signed-out assistant — smallest model, navigate-only, no memory —
    while the panel looked exactly like the full assistant. The turn
    "worked", investigated nothing, and could not understand "continue"
    because anonymous turns store no history (2026-08-28).
    """

    def test_a_bad_bearer_token_is_a_401_not_an_anonymous_turn(
            self, client, recording):
        res = client.post(
            "/assist/chat/stream",
            json={"message": "hi", "conversation_key": "standalone:x",
                  "context_block": ""},
            headers={"Authorization": "Bearer expired-or-garbage"},
        )
        assert res.status_code == 401
        proxy, _ = recording
        assert not proxy.payloads, \
            "the turn must not run at all — a silent anonymous answer is " \
            "exactly the failure this guards against"

    def test_no_credentials_at_all_still_means_anonymous(
            self, client, recording):
        res = _anon_post(client)
        assert res.status_code == 200
        proxy, _ = recording
        assert proxy.payloads[0]["anonymous"] is True

    def test_the_anonymous_stream_declares_itself_first(self, client):
        res = _anon_post(client)
        body = res.text
        first = body.split("\n\n")[0]
        assert first.startswith("event: meta"), \
            "the reduced tier must announce itself before any content"
        assert '"anonymous":true' in first
        assert "Signed-out" in first


class TestContinuationRule:
    def test_the_prompt_tells_the_model_what_continue_means(self):
        from src.api.di import _DEFAULT_SYSTEM_PROMPT
        assert '"continue"' in _DEFAULT_SYSTEM_PROMPT
        assert "resume" in _DEFAULT_SYSTEM_PROMPT
        # And the anti-early-convergence rule that came from the same
        # review: leads on the table mean the investigation is not done.
        assert "one more tool call over an early" in _DEFAULT_SYSTEM_PROMPT


class TestToolBudgetScalesWithTheModel:
    """The per-turn tool-output ceiling is a property of the model's context.

    _tool_chars_for existed and nothing called it: both engines hardcoded
    the 14k floor measured against a 16k window, so a 131k-context model
    investigating in production had its tools answer "budget spent" after
    two searches (2026-08-28). The service now sends the derived ceiling
    with the payload and the engines consume it.
    """

    def _post(self, client, services, **extra):
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1"))
        body = {"message": "investigate", "conversation_key": "standalone:b",
                "context_block": ""}
        body.update(extra)
        return client.post("/assist/chat/stream", json=body,
                           headers=make_headers("user-1"))

    def test_the_payload_carries_a_tool_budget(self, client, services,
                                               recording):
        self._post(client, services)
        proxy, _ = recording
        from src.assistant import tool_budget
        assert proxy.payloads[0]["tool_chars"] >= \
            tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN

    def test_small_local_models_keep_exactly_the_floor(self, client, services,
                                                       recording):
        # The staging e2e caught the first cut of this scaling: doubling a
        # 1.7B's tool input doubles its prefill per round on the shared
        # iGPU, and the real-model smoke turns blew their 200s wait. Below
        # the schema tier the floor is not a fallback, it is the measured
        # right answer.
        self._post(client, services)
        proxy, _ = recording
        from src.assistant import local_models, schema_context, tool_budget
        default = local_models.resolve(None)
        if default.context_tokens < schema_context.SCHEMA_MIN_CONTEXT_TOKENS:
            assert proxy.payloads[0]["tool_chars"] == \
                tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN

    def test_a_large_context_model_gets_more_than_the_floor(self, services):
        # On the private derivation on purpose (same precedent as the
        # scorer's predicate tests): the payload plumbing is pinned by the
        # test above; this pins that the number actually scales with the
        # window instead of being the floor wearing a new name.
        # pylint: disable=protected-access
        from src.assistant import local_models, tool_budget
        service = services["assistant_service"]
        big = max(local_models.LOCAL_MODELS, key=lambda m: m.context_tokens)
        small = min(local_models.LOCAL_MODELS,
                    key=lambda m: m.context_tokens)
        big_chars = service._tool_chars_for(big.id)
        assert big_chars > tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN * 4, \
            "131k+ of context must buy materially more tool output than 16k"


class TestStreamBoundaryNetting:
    """An unexpected exception mid-stream becomes a loud error event.

    The 2026-08-28 production failure: a SQLAlchemy session-state error
    escaped the turn generator, the SSE stream closed mid-answer with no
    error event and no log tying the traceback to a turn, and the panel
    rendered a reply that just stopped.
    """

    class _DyingProxy:
        async def stream(self, payload):
            yield "event: chunk\ndata: {\"text\": \"partial\"}\n\n"
            raise ArithmeticError("session state corrupted mid-stream")

    def test_the_turn_yields_an_error_event_not_a_silent_close(
            self, client, services):
        services["assistant_service"]._proxy = self._DyingProxy()  # pylint: disable=protected-access
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1"))
        res = client.post(
            "/assist/chat/stream",
            json={"message": "go", "conversation_key": "standalone:net",
                  "context_block": ""},
            headers=make_headers("user-1"),
        )
        assert res.status_code == 200
        body = res.text
        assert "partial" in body, "content before the death still streams"
        assert "event: error" in body, \
            "the death must be announced, not swallowed"
        assert "cut short" in body

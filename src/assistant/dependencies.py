"""FastAPI dependency providers for the assistant module.

Lives inside the module so there's a single import boundary: callers
import ``src.assistant.dependencies.get_assistant_service`` and that's
the only symbol from the outside world that touches module internals.
"""
# pylint: disable=missing-function-docstring,global-statement,invalid-name
# pylint: disable=import-outside-toplevel
from __future__ import annotations

from src.api.dependencies import _request_session, _use_postgres

from src.assistant.context import TurnLimits
from src.assistant.proxy_client import ClaudeProxyClient
from src.assistant.repository import AssistRepository, InMemoryAssistRepository
from src.assistant.service import AssistantService


# Default agent system prompt. Lives here so the router doesn't have
# to know about prompt engineering.
DEFAULT_SYSTEM_PROMPT = (
    "You are a research assistant embedded in the GMR Knowledge Graph platform. "
    "You help journalists, researchers, and citizens investigate connections between "
    "companies, public authorities, and persons in the context of EU public procurement "
    "and corporate transparency.\n\n"
    "You have access to tools that query the GMR graph database "
    "(3M+ companies, 700K+ contracts). Use them to ground your answers in real data. "
    "Always cite specific entities and values.\n\n"
    "When the user provides report context, treat it as the current state of their "
    "work-in-progress. You can refer to specific sections by their headings and "
    "quote from them when helpful.\n\n"
    "Keep responses concise and factual. Use bullet points for lists. "
    "If data is unavailable, say so clearly — never hallucinate numbers."
)

TURN_LIMITS = TurnLimits(max_turns=20, max_chars=12_000)
CONTEXT_CHAR_BUDGET = 8_000


# Singleton memory repo for dev/test when Postgres isn't configured.
_memory_repo: InMemoryAssistRepository | None = None


def _get_assist_repo() -> AssistRepository:
    global _memory_repo
    if _use_postgres:
        # Lazy import to avoid circulars
        from src.assistant.pg_repository import PgAssistRepository

        session = _request_session.get()
        if session is None:
            # Request didn't go through get_db_session — create a session
            # from the shared factory.
            from src.api.dependencies import _pg_session_factory

            assert _pg_session_factory is not None
            session = _pg_session_factory()
            _request_session.set(session)
        return PgAssistRepository(session)

    if _memory_repo is None:
        _memory_repo = InMemoryAssistRepository()
    return _memory_repo


_proxy_client: ClaudeProxyClient | None = None


def _get_proxy_client() -> ClaudeProxyClient:
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = ClaudeProxyClient()
    return _proxy_client


def get_assistant_service() -> AssistantService:
    return AssistantService(
        repo=_get_assist_repo(),
        proxy_client=_get_proxy_client(),
        base_system_prompt=DEFAULT_SYSTEM_PROMPT,
        turn_limits=TURN_LIMITS,
        context_char_budget=CONTEXT_CHAR_BUDGET,
    )

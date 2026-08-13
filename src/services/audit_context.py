"""Who is acting, and on whose behalf.

The authentication context answers "which user is this". It is not enough
once an agent can write on that user's behalf: the assistant's Studio tools
call the services in-process, so nothing about the transport says whether a
project was created by a person clicking a button or by a model deciding to.
Both arrive as the same user id.

This carries the missing half. It is set once per entry point — the HTTP
middleware for a request, the assistant for a turn — and read wherever a
write is recorded, so no service has to thread four extra arguments through
its signature to say where its caller came from.

Two consequences worth stating plainly:

  * It is provenance, not permission. ``actor_id`` stays the user for agent
    actions, because the agent has no standing of its own and the permission
    that allowed the write was theirs. ``actor_kind`` is what distinguishes
    them.
  * It IS readable by authorization, deliberately. "The user may do this,
    but the agent may not do it on their behalf" is a sentence the platform
    should be able to express — see ``AGENT_FORBIDDEN`` below.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

USER = "user"
AGENT = "agent"

#: Actions an agent must never take on a user's behalf, however the user
#: phrased the request.
#:
#: Deletion is the whole list today, and that is not an accident: everything
#: the Studio tools expose is additive precisely because an agent that can
#: remove a user's work is a different risk conversation. This makes that
#: property enforced rather than merely true of the current tool surface —
#: adding a delete tool would now fail loudly instead of quietly working.
AGENT_FORBIDDEN = frozenset({"deleted", "delete"})


class AgentActionForbidden(PermissionError):
    """An agent attempted something only a person may do."""


@dataclass(frozen=True)
class AuditContext:
    """Provenance for whatever is written next."""

    actor_id: str = ""
    actor_kind: str = USER
    request_id: str | None = None
    #: Set while an agent turn is running.
    conversation_id: str | None = None
    #: Set while one tool call inside that turn is running.
    message_id: str | None = None
    #: What has been recorded while this context was installed. A list rather
    #: than a counter so the fallback entry can say what it is standing in
    #: for, and mutable on purpose: `replace()` shares it, which is what lets
    #: a nested tool-call scope report back to the request that opened it.
    written: list = field(default_factory=list)

    @property
    def is_agent(self) -> bool:
        return self.actor_kind == AGENT

    def derive(self, **changes) -> "AuditContext":
        """A copy with some fields changed.

        Written out rather than using dataclasses.replace so the return type
        is this class and not a structural protocol — and so the one subtle
        part is visible: `written` is passed through by reference, which is
        what lets a nested tool-call scope report back to the request that
        opened it.
        """
        fields = {
            "actor_id": self.actor_id,
            "actor_kind": self.actor_kind,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "written": self.written,
        }
        fields.update(changes)
        return AuditContext(**fields)

    def note(self, entity_type: str, action: str) -> None:
        """Called when something is recorded under this context."""
        self.written.append(f"{entity_type}:{action}")

    def check(self, action: str) -> None:
        """Refuse an action this actor may not perform.

        Raises rather than returning a verdict: a caller that forgets to
        check a boolean is the failure mode this exists to prevent.
        """
        if self.is_agent and action.lower() in AGENT_FORBIDDEN:
            raise AgentActionForbidden(
                f"an agent may not perform {action!r} on a user's behalf"
            )


_CURRENT: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    "audit_context", default=AuditContext()
)


def current() -> AuditContext:
    """The context for the work in flight. Never None — an unset context is
    an anonymous user one, so a caller never has to guard."""
    return _CURRENT.get()


def set_current(ctx: AuditContext):
    """Install a context. Returns the token to reset with."""
    return _CURRENT.set(ctx)


def reset(token) -> None:
    _CURRENT.reset(token)


class tool_call:  # pylint: disable=invalid-name
    """Scope one tool call onto the current context.

    A context manager rather than a setter because the id must come off
    again when the call finishes: a later write in the same turn belongs to
    the turn, not to whichever tool happened to run last.
    """

    def __init__(self, message_id: str, tool_name: str = "") -> None:
        self._message_id = message_id
        self._tool = tool_name
        self._token = None

    def __enter__(self) -> AuditContext:
        ctx = current().derive(message_id=self._message_id)
        self._token = set_current(ctx)
        return ctx

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            reset(self._token)

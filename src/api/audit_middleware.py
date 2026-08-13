"""Every mutating request leaves a trace, whether or not anyone remembered.

The activity log was opt-in: a service called `activity.record(...)` if
someone thought of it. Six services out of twenty-seven did, so production
had 29 rows and all of them were about stories — Studio projects, plots,
flowers, follows, tags and credential changes happened silently.

Opt-in coverage decays, so coverage comes from here instead. The middleware:

  * installs the AuditContext for the request, which is what makes an entry
    attributable at all; and
  * writes a generic entry afterwards if the request changed something and
    nothing more specific was recorded.

The generic entry is deliberately poor — it knows the method, the path and
the status, not that a project was renamed. It exists so that a mutating
endpoint nobody enriched is *visible* rather than absent; a service that
records properly suppresses it. Absence should mean nothing happened, not
that nobody wrote the code.

Reads are not logged. The point is answering "what was done", and a log that
records every GET buries that in noise it cannot afford.
"""
from __future__ import annotations

import logging
import uuid

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.api.auth import JWT_ALGORITHM, JWT_SECRET
from src.services import audit_context
from src.services.audit_context import AuditContext

logger = logging.getLogger(__name__)

#: Methods that can change something.
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths that mutate but are not user activity worth a feed entry. Auth
#: endpoints are the whole list: /auth/refresh fires on every cold page load
#: for every visitor, and "someone's browser renewed a token" is noise that
#: would drown the entries this table exists for. Logging in and out is
#: genuinely interesting, and is recorded by the auth service itself.
SKIP_PREFIXES = ("/auth/refresh", "/auth/login", "/auth/logout")


def _actor_from(request: Request) -> str:
    """The user id from the bearer token, or "" for anonymous.

    Decoded here rather than reusing get_current_user because that is a
    route dependency with a database round trip attached; the middleware
    only needs the subject claim, and must not turn every request into an
    extra query. A token this cannot read is treated as anonymous — the
    route's own auth will reject it.
    """
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return ""
    try:
        payload = jwt.decode(header[7:], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return ""
    sub = payload.get("sub") or ""
    if not sub:
        return ""
    try:
        uuid.UUID(sub)
    except ValueError:
        # Same normalisation the auth dependency applies, so the middleware
        # and the routes agree on who this is.
        sub = str(uuid.uuid5(uuid.NAMESPACE_URL, sub))
    return sub


def should_audit(method: str, path: str) -> bool:
    if method.upper() not in MUTATING:
        return False
    return not any(p in path for p in SKIP_PREFIXES)


def install(app, activity_factory) -> None:
    """Attach the middleware. ``activity_factory`` takes a request and
    returns an ActivityService, or None when one cannot be built."""

    async def audit(request: Request, call_next):
        path = request.url.path
        if not should_audit(request.method, path):
            return await call_next(request)

        actor = _actor_from(request)
        request_id = str(uuid.uuid4())
        ctx = AuditContext(
            actor_id=actor,
            actor_kind=audit_context.USER,
            request_id=request_id,
        )
        token = audit_context.set_current(ctx)
        try:
            response = await call_next(request)
        finally:
            audit_context.reset(token)

        # Only successful changes. A 403 or a 422 changed nothing, and a log
        # that cannot tell those apart from real edits is not evidence.
        if (actor and 200 <= response.status_code < 300 and not ctx.written
                and not _is_stream(response)):
            await _record_generic(request, response, ctx, activity_factory)
        return response

    # add_middleware rather than the @app.middleware decorator: the decorator
    # is FastAPI's, and this needs to install onto a plain Starlette app too
    # — which is how the streaming behaviour is tested without standing up
    # the whole application. Same placement either way: the most recently
    # added middleware is the outermost.
    app.add_middleware(BaseHTTPMiddleware, dispatch=audit)


def _is_stream(response) -> bool:
    """Is the body still being produced after call_next returned?

    For a streaming response it is. `call_next` hands back as soon as the
    headers are ready, so anything written here runs CONCURRENTLY with the
    generator that is still producing the body — and both use the same
    database session. That is not a theoretical race: it took the assistant
    down in testing with "Session is already flushing", because the audit
    write and the turn flushed the same session at the same time.

    Streams are also the case that needs the fallback least. The assistant
    turn records its own tool calls from inside the generator, with the
    conversation and tool-call provenance the middleware could never know.
    """
    ctype = (response.headers.get("content-type") or "").lower()
    return "text/event-stream" in ctype


async def _record_generic(request, response, ctx, activity_factory) -> None:
    """The fallback entry for a mutating endpoint nobody enriched."""
    try:
        activity = await activity_factory(request)
        if activity is None:
            return
        # Restore the context so the entry carries the request id: the
        # `finally` above has already reset it by the time we get here.
        token = audit_context.set_current(ctx)
        try:
            await activity.record(
                actor_id=ctx.actor_id,
                entity_type="request",
                entity_id="",
                action=f"{request.method.lower()}",
                summary=f"{request.method} {request.url.path}",
            )
        finally:
            audit_context.reset(token)
    except Exception:  # pylint: disable=broad-exception-caught
        # Same contract as ActivityService.record: never break the request
        # over the record of it, never lose it silently either.
        logger.exception(
            "generic audit entry failed %s %s status=%s",
            request.method, request.url.path, response.status_code,
        )

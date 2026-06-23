from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import asyncpg.exceptions as asyncpg_exc
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from src.api.di import make_container
from src.api.rate_limit import limiter
from src.api.routers import (
    auth, dossiers, flowers, groups, investigations, issues, moderation, reports, sharing, sitemap, tags, users,
)
from src.assistant import router as assistant_router
from src.infra.postgres.models import Base
from src.services.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied


# Route prefixes for the dual-mount rename window. /data-stories
# is canonical; /reports is the deprecated alias kept until
# existing API clients cut over. Drop the alias one release after.
_DATA_STORIES_PREFIX = "/data-stories"
_REPORTS_ALIAS_PREFIX = "/reports"

logger = logging.getLogger(__name__)


def _find_value_error(exc: BaseException) -> ValueError | None:
    """Walk an exception's cause chain + `.orig` pointer, returning the
    first ValueError found (or None).

    asyncpg surfaces bad UUID binds as ValueError at the bottom of the
    chain; SQLAlchemy wraps it as DBAPIError. `exc.orig` points at the
    first wrapper, `__cause__` chains PEP 3134-style — either link can
    hold the ValueError depending on how the failure was constructed,
    so we traverse both. Visited set guards against exotic cycles.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, ValueError):
            return cur
        if cur.__cause__ is not None:
            stack.append(cur.__cause__)
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException):
            stack.append(orig)
    return None


def _find_asyncpg_data_error(exc: BaseException) -> asyncpg_exc.DataError | None:
    """Walk an exception's cause chain + `.orig` pointer, returning the
    first asyncpg DataError found (or None).

    Mirrors ``_find_value_error`` but for value-level driver errors
    that aren't ValueErrors: ``CharacterNotInRepertoireError`` (null
    byte / non-UTF8 in a string field), ``NumericValueOutOfRangeError``
    (int8 overflow), ``InvalidDatetimeFormatError``, etc. All subclass
    ``asyncpg.exceptions.DataError`` and are user-driven (not a bug in
    our code), so they translate to 400, not 500.

    Schemathesis fuzz on 2026-06-10 caught two of these as 500s:
    ``POST /groups {name: \"foo\\x00bar\"}`` and
    ``GET /issues?entity_id=...%00...``. Both flow into Postgres TEXT
    columns which reject U+0000 with this exact exception family.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, asyncpg_exc.DataError):
            return cur
        if cur.__cause__ is not None:
            stack.append(cur.__cause__)
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException):
            stack.append(orig)
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # The FastAPI lifespan protocol passes the bound application as the
    # first positional argument; this hook only needs the env-derived
    # DATABASE_URL, so the parameter is underscore-prefixed.
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        engine = create_async_engine(db_url, connect_args={"timeout": 10, "ssl": None})
        # Ensure schema exists (idempotent — safe for fresh and existing DBs)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT"
            ))
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "failed_login_attempts INTEGER NOT NULL DEFAULT 0"
            ))
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ"
            ))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id),
                    report_id UUID NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (user_id, report_id)
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS assist_conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL,
                    conversation_key TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_assist_conv_user_key UNIQUE (user_id, conversation_key)
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS assist_messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID NOT NULL REFERENCES assist_conversations(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL,
                    role VARCHAR(16) NOT NULL,
                    content TEXT NOT NULL,
                    extras JSONB NOT NULL DEFAULT '{}'::jsonb,
                    tokens_in INTEGER,
                    tokens_out INTEGER,
                    model VARCHAR(64),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT ck_assist_msg_role CHECK (role IN ('user', 'assistant'))
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_assist_msg_user_created "
                "ON assist_messages (user_id, created_at)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_assist_msg_conv_created "
                "ON assist_messages (conversation_id, created_at)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_issues_status "
                "ON issues (status)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_reports_visibility "
                "ON reports (visibility)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_reports_parent_id "
                "ON reports (parent_id) WHERE parent_id IS NOT NULL"
            ))
        await engine.dispose()

    yield


def build_app(database_url: str | None = None) -> FastAPI:
    """Create a fully-wired FastAPI application.

    Used by production (module-level ``app``) and by integration tests
    (which pass a testcontainers Postgres URL).
    """
    application = FastAPI(
        title="GMR Community API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    db_url = database_url or os.environ.get("DATABASE_URL")
    if db_url:
        container = make_container(db_url)
        setup_dishka(container, application)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://gmr.void42.net",
            "http://gmr-dast.void42.internal",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Security headers on all API responses (OWASP ZAP finding)
    @application.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # Exception handlers below take ``request`` as the first positional
    # argument because that's the FastAPI/starlette protocol; the
    # handler bodies don't read it. The protocol *names* don't have to
    # be ``request``, but keeping the canonical name avoids confusion
    # when other engineers grep for "request: Request".
    # pylint: disable=unused-argument

    @application.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": exc.message})

    @application.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @application.exception_handler(Conflict)
    async def conflict_handler(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.message})

    @application.exception_handler(InvalidInput)
    async def invalid_input_handler(request: Request, exc: InvalidInput) -> JSONResponse:
        # 400 for value-level constraint violations the service layer
        # raises (per-user flower cap, etc.). Routers no longer need a
        # per-route try/except: the service raises, this handler maps.
        return JSONResponse(status_code=400, content={"detail": exc.message})

    @application.exception_handler(DBAPIError)
    async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
        """Translate driver-level argument errors into a 400 instead of 500.

        Delegates the exception-chain walk to _find_value_error so
        build_app's own cognitive complexity stays low. Before the
        fix, DAST's `GET /reports/undefined` returned 500; after, the
        caller sees a clear 400 and fuzz tooling stops counting these
        as server errors. Keep the message boring — don't leak the
        stack trace.
        """
        ve = _find_value_error(exc)
        if ve is not None:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid parameter: {ve}"},
            )
        de = _find_asyncpg_data_error(exc)
        if de is not None:
            # Driver-level rejection of an unprocessable value. The
            # most common one in our DAST runs is a null byte (U+0000)
            # in a string field — Postgres TEXT can't encode it — but
            # numeric overflow and bad datetime formats land here too.
            # All are caller-fixable, so 400 with a short message.
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid value: {type(de).__name__}"},
            )
        # Neither user-input shape — connection errors, constraint
        # violations — fall through to the generic 500 handler.
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @application.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Caught by the DAST re-run on 2026-05-10: GET /issues with a
        # 39-digit offset hit asyncpg's int8-range error (`DataError`,
        # not `ValueError`), bypassed the DBAPIError handler above, and
        # surfaced as a silent 500. The catch-all needs to log so future
        # 500s leave a trail. logger.exception emits at ERROR with the
        # full traceback so a `kubectl logs` is enough to triage.
        # ``exc`` is implicitly attached to logger.exception via the
        # current exc_info; we don't reference the parameter directly.
        del exc
        logger.exception(
            "unhandled exception on %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # pylint: enable=unused-argument

    application.include_router(assistant_router.router)
    application.include_router(auth.router)
    # Data stories — canonical path. The /reports alias below keeps
    # existing API clients working through the rename window; remove
    # one release after the frontend cuts over.
    application.include_router(reports.router, prefix=_DATA_STORIES_PREFIX)
    application.include_router(reports.router, prefix=_REPORTS_ALIAS_PREFIX, deprecated=True)
    application.include_router(sharing.router, prefix=_DATA_STORIES_PREFIX)
    application.include_router(sharing.router, prefix=_REPORTS_ALIAS_PREFIX, deprecated=True)
    # Flowers — Medium-style clap on a story. Dual-mounted like reports
    # / sharing during the rename window so /capi/data-stories/{id}/
    # flowers is canonical and /capi/reports/{id}/flowers still works.
    application.include_router(flowers.router, prefix=_DATA_STORIES_PREFIX)
    application.include_router(flowers.router, prefix=_REPORTS_ALIAS_PREFIX, deprecated=True)
    application.include_router(issues.router)
    application.include_router(users.router)
    application.include_router(groups.router)
    application.include_router(investigations.router)
    application.include_router(dossiers.router)
    application.include_router(moderation.router)
    application.include_router(sitemap.router)
    # Tags — story-tag write (PUT /data-stories/{id}/tags), public
    # browse (GET /tags), per-user follow (GET/POST/DELETE
    # /me/followed-tags). No prefix; tag paths are namespaced by their
    # own routes.
    application.include_router(tags.router)

    @application.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok"}

    return application


# Production app — created at import time from env vars.
app = build_app()

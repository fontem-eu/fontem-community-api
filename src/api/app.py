from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

from dishka.integrations.fastapi import setup_dishka
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import asyncpg.exceptions as asyncpg_exc
from sqlalchemy.exc import DBAPIError

from src.api import audit_middleware
from src.api.di import make_container
from src.services.activity_service import ActivityService
from src.api.rate_limit import limiter
from src.api.routers import (
    activity, auth, briefings, data_projects, dossiers, feed_catalogue, flowers, groups, investigations, issues, moderation, reports, sharing, sitemap, tags, users, visualizations,
)
from src.assistant import mock_llm
from src.assistant import router as assistant_router
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
    """No schema work here. Alembic owns the schema.

    This hook used to run ``Base.metadata.create_all`` and then eighteen
    hand-written ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statements,
    because create_all creates new tables but never alters existing ones.
    That was a migration system with no ordering, no versioning and no
    record of what had run, and it drifted: the testing database ended up
    both missing columns the models needed and carrying NOT NULL columns
    the models had forgotten, which is how /auth/login and
    /data-stories came to return 500.

    Schema changes are now migrations. ``alembic upgrade head`` runs as an
    ArgoCD PreSync hook (deployment/templates/migrate-job.yaml) before any
    new pod rolls, so a failed migration blocks the release instead of
    leaving half-migrated pods serving traffic. Every environment is
    stamped at 008 and a fresh database is built by 001..008.

    Two consequences worth keeping in mind:

    * Migrations must be backward compatible with the running code. The
      PreSync hook completes before the new pods start, so for a moment
      the OLD pods are talking to the NEW schema. Additive changes are
      safe; a rename or a drop needs the usual two-release dance.
    * The app no longer repairs its own database. If a column is missing
      the request fails, loudly, instead of being silently patched at the
      next restart. That is the point.
    """
    yield


async def _activity_for(request):
    """Resolve the request's ActivityService, or None if there is no
    container (an app built without a database, as some tests do)."""
    container = getattr(request.state, "dishka_container", None)
    if container is None:
        return None
    try:
        return await container.get(ActivityService)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _mount_mock_llm(application: FastAPI) -> None:
    """Mount the scripted e2e model, where there is one.

    Only where ASSIST_MOCK_MODEL is set, so the routes do not exist in
    production even though the code ships in the image. That is the entire
    safety argument for shipping it, so it has its own test — a flag is a
    claim about configuration, and configuration drifts.
    """
    if not mock_llm.enabled():
        return
    # Imported here rather than at module scope: nothing should load the
    # mock's router in an environment that does not serve it.
    # pylint: disable-next=import-outside-toplevel
    from src.assistant import mock_llm_router
    application.include_router(mock_llm_router.router)


def _accept_lang(
    # Aliased on purpose: some routes carry a {lang} PATH param
    # (/translations/{lang}); a dependency argument literally named
    # ``lang`` would be captured as that path parameter there.
    accept_lang: Annotated[str | None, Query(alias="lang", pattern="^[a-z]{2}$")] = None,
) -> str | None:
    """App-wide ?lang= declaration (see FastAPI(dependencies=...))."""
    return accept_lang


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
        # Every endpoint tolerates ?lang= — the frontend's withLang()
        # appends it to every call, and handlers that localise read it
        # explicitly. Declaring it app-wide makes the OpenAPI spec match
        # that reality, which the contract validation (pact ↔ spec)
        # checks against.
        dependencies=[Depends(_accept_lang)],
    )

    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Registered BEFORE dishka, deliberately. Starlette makes the
    # last-registered middleware the outermost one, so this ordering puts
    # dishka outside the audit middleware — which is what keeps the request
    # container (and its session) open while the fallback entry is written
    # after the response. Register it afterwards and the container is closed
    # by the time there is a status code to judge.
    audit_middleware.install(application, _activity_for)

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
        # Nothing this API returns belongs in a cache. Verified against the
        # dast environment on 2026-08-13: /capi/investigations,
        # /capi/studio/projects and /capi/activity all answered 200 with a
        # user's own records and no cache directives at all, so a browser
        # or an intermediary was free to keep them and hand them to whoever
        # asked next. ZAP reports it as "Re-examine Cache-control
        # Directives" (160 instances); the objection that matters is that
        # these are somebody's private records.
        #
        # Set unconditionally rather than only on authenticated routes: the
        # public endpoints return data that changes on every publish, and
        # "correct but occasionally stale" is not worth the branch. Static
        # assets are served by nginx, not from here, so the caching that
        # actually pays is untouched.
        # The full directive set, not just no-store. ZAP's rule 10015 kept
        # flagging /capi/activity and /capi/users/me after no-store alone was
        # in place (verified live: the header was there and the alert fired
        # anyway), and the extra directives are what old intermediaries
        # actually honour — no-store is HTTP/1.1, no-cache + must-revalidate
        # cover proxies that predate it, and private keeps shared caches out
        # even if one ignores the rest.
        response.headers.setdefault(
            "Cache-Control", "no-store, no-cache, must-revalidate, private")
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
    _mount_mock_llm(application)
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
    application.include_router(activity.router)
    application.include_router(users.router)
    application.include_router(groups.router)
    application.include_router(investigations.router)
    application.include_router(dossiers.router)
    application.include_router(visualizations.router)
    application.include_router(data_projects.router)
    application.include_router(moderation.router)
    application.include_router(sitemap.router)
    # Tags — story-tag write (PUT /data-stories/{id}/tags), public
    # browse (GET /tags), per-user follow (GET/POST/DELETE
    # /me/followed-tags). No prefix; tag paths are namespaced by their
    # own routes.
    application.include_router(tags.router)
    # Feed-query catalogue — admin CRUD under /admin/*, plus the anonymous
    # GET /query-groups the feed picker reads. No prefix: the admin paths are
    # already namespaced by their own routes.
    application.include_router(feed_catalogue.router)
    # Briefings — the public face of the catalogue: browse anonymously,
    # watch with a session, and poll the Atom feed with a token.
    application.include_router(briefings.router)

    @application.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok"}

    @application.post("/csp-report", tags=["Security"], status_code=204,
                      openapi_extra={"security": []})
    async def csp_report(request: Request) -> Response:
        """Receive browser CSP violation reports (the CSP `report-uri`).

        A CSP block fails silently in the browser and produces no server
        error, so this is the only server-side signal that content is being
        refused (e.g. a presigned image on a cross-origin host). Anonymous,
        best-effort, never raises."""
        try:
            body = await request.json()
        except Exception:  # pylint: disable=broad-exception-caught
            body = None
        report = body.get("csp-report", body) if isinstance(body, dict) else body
        if isinstance(report, dict):
            logger.warning(
                "CSP violation: directive=%s blocked=%s document=%s",
                report.get("violated-directive") or report.get("effective-directive"),
                report.get("blocked-uri"),
                report.get("document-uri"),
            )
        else:
            logger.warning("CSP violation (unparsed): %s", str(body)[:500])
        return Response(status_code=204)

    return application


# Production app — created at import time from env vars.
app = build_app()

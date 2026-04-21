from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

from dishka.integrations.fastapi import setup_dishka
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.api.rate_limit import limiter
from src.api.routers import (
    auth, groups, issues, moderation, reports, sharing, sitemap, users,
)
from src.assistant import router as assistant_router
from src.api.di import make_container
from src.services.exceptions import Conflict, NotFound, PermissionDenied


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        engine = create_async_engine(db_url, connect_args={"timeout": 10, "ssl": None})
        # Ensure schema exists (idempotent — safe for fresh and existing DBs)
        from src.infra.postgres.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT"))
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

    @application.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": exc.message})

    @application.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @application.exception_handler(Conflict)
    async def conflict_handler(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.message})

    @application.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    application.include_router(assistant_router.router)
    application.include_router(auth.router)
    application.include_router(reports.router)
    application.include_router(sharing.router)
    application.include_router(issues.router)
    application.include_router(users.router)
    application.include_router(groups.router)
    application.include_router(moderation.router)
    application.include_router(sitemap.router)

    @application.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok"}

    return application


# Production app — created at import time from env vars.
app = build_app()

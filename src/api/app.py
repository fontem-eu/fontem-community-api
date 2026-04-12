from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import os

from dishka.integrations.fastapi import setup_dishka

from src.api.routers import auth, groups, issues, moderation, reports, sharing, users
from src.assistant import router as assistant_router
from src.api.di import make_container
from src.services.exceptions import Conflict, NotFound, PermissionDenied


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Initialize PostgreSQL repos if DATABASE_URL is set
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # Auto-migrate: ensure all columns exist
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT"))
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
            # Assistant module owns its own tables
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
        await engine.dispose()

    yield


app = FastAPI(title="GMR Community API", version="0.1.0", lifespan=lifespan)

# Wire up dishka container — must happen before the app starts, not inside
# the lifespan (Starlette forbids adding middleware after startup).
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    _container = make_container(_db_url)
    setup_dishka(_container, app)

# CORS — allow all for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(PermissionDenied)
async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": exc.message})


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.message})


@app.exception_handler(Conflict)
async def conflict_handler(request: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.message})


# Include routers — db session dependency ensures commit/rollback per request
app.include_router(assistant_router.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(sharing.router)
app.include_router(issues.router)
app.include_router(users.router)
app.include_router(groups.router)
app.include_router(moderation.router)


@app.get("/health", tags=["Health"])
async def health() -> dict:
    """Liveness/readiness probe."""
    return {"status": "ok"}

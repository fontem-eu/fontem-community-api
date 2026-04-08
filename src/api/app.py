from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import os

from src.api.routers import assist, auth, groups, issues, moderation, reports, sharing, users
from src.api import dependencies
from src.api.dependencies import configure_postgres
from src.services.exceptions import Conflict, NotFound, PermissionDenied


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Initialize PostgreSQL repos if DATABASE_URL is set
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        configure_postgres(database_url=db_url)
    yield


app = FastAPI(title="GMR Community API", version="0.1.0", lifespan=lifespan)

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
app.include_router(assist.router)
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

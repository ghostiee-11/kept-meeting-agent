"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, mock_tasks
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.app_env != "local")

    # The database is optional at boot so the app still starts, and /health
    # still explains itself, when DATABASE_URL is missing.
    app.state.engine = None
    app.state.session_factory = None
    if settings.database_url is not None:
        app.state.engine = create_engine(settings)
        app.state.session_factory = create_session_factory(app.state.engine)

    log.info(
        "startup",
        environment=settings.app_env,
        version=settings.git_sha,
        providers=settings.configured_providers,
        database=settings.database_url is not None,
    )
    yield

    if app.state.engine is not None:
        await app.state.engine.dispose()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Kept",
        description="Multi-agent meeting-to-execution system.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Demo-Key"],
    )

    app.include_router(health.router)
    app.include_router(mock_tasks.router)
    return app


app = create_app()

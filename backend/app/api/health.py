"""Liveness and readiness.

``/health`` is the one endpoint a reviewer hits first, so it tells the truth
about what is actually wired up: which providers have credentials, whether auth
is enforced, and which build is running. It never returns a secret value, only
whether one is present.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.deps import SettingsDep
from app.logging import get_logger

router = APIRouter(tags=["ops"])
log = get_logger(__name__)

_STARTED_AT = time.monotonic()

# Neon scales compute to zero after five minutes idle, so a first query can be
# slow. Long enough to let it wake, short enough that health never hangs.
_DB_PROBE_TIMEOUT_SECONDS = 8.0


async def _database_reachable(request: Request) -> bool:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return False
    try:
        async with asyncio.timeout(_DB_PROBE_TIMEOUT_SECONDS), factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("health.database_unreachable", error=str(exc))
        return False
    return True


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    uptime_seconds: float
    auth_enforced: bool
    providers: dict[str, bool]
    web_search: bool
    database: bool
    notes: list[str]


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep, request: Request) -> HealthResponse:
    providers = settings.configured_providers
    database = await _database_reachable(request)
    notes: list[str] = []

    if not any(providers.values()):
        notes.append("No model provider credentials configured; agent runs will fail.")
    if settings.database_url is None:
        notes.append("DATABASE_URL unset; nothing will be persisted.")
    elif not database:
        notes.append("DATABASE_URL is set but the database did not answer.")
    if settings.is_production and settings.demo_key is None:
        notes.append("Production deployment without a demo key: write endpoints are unprotected.")
    if not settings.tavily_api_key:
        notes.append("Tavily unset; web search falls back to the keyless provider.")

    healthy = any(providers.values()) and (settings.database_url is None or database)
    status: Literal["ok", "degraded"] = "ok" if healthy else "degraded"

    return HealthResponse(
        status=status,
        version=settings.git_sha,
        environment=settings.app_env,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
        auth_enforced=settings.demo_key is not None,
        providers=providers,
        web_search=settings.tavily_api_key is not None,
        database=database,
        notes=notes,
    )

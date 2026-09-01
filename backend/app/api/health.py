"""Liveness and readiness.

``/health`` is the one endpoint a reviewer hits first, so it tells the truth
about what is actually wired up: which providers have credentials, whether auth
is enforced, and which build is running. It never returns a secret value, only
whether one is present.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(tags=["ops"])

_STARTED_AT = time.monotonic()


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
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    providers = settings.configured_providers
    notes: list[str] = []

    if not any(providers.values()):
        notes.append("No model provider credentials configured; agent runs will fail.")
    if settings.is_production and settings.demo_key is None:
        notes.append("Production deployment without a demo key: write endpoints are unprotected.")
    if not settings.tavily_api_key:
        notes.append("Tavily unset; web search falls back to the keyless provider.")

    status: Literal["ok", "degraded"] = "ok" if any(providers.values()) else "degraded"

    return HealthResponse(
        status=status,
        version=settings.git_sha,
        environment=settings.app_env,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
        auth_enforced=settings.demo_key is not None,
        providers=providers,
        web_search=settings.tavily_api_key is not None,
        database=settings.database_url is not None,
        notes=notes,
    )

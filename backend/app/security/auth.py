"""Demo protection.

The deployed backend spends real rate-limited credentials on every run, so the
write endpoints are not open to whoever finds the URL. Reads stay open, because
the interesting part of a review is looking at what the system produced.

Three layers, in increasing order of how much they matter:

A shared key, so a stranger cannot run the agents.
A per-IP rate limit, so a holder of the key cannot run them in a loop.
A daily cap, so a bad afternoon cannot exhaust a month of free tier.

None of this is real authentication and it is not pretending to be. It is the
minimum that stops a public URL from becoming somebody else's LLM proxy.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger(__name__)

# ponytail: in-process counters, so they reset on deploy and do not coordinate
# across instances. Correct for one free-tier worker, which is what runs here.
# Move to Redis if this ever scales past a single process.
_recent: dict[str, deque[float]] = defaultdict(deque)
_daily: dict[str, int] = defaultdict(int)
_daily_date = datetime.now(UTC).date()


def _client_ip(request: Request) -> str:
    """Render sits behind a proxy, so the socket address is the proxy's."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _check_rate_limit(ip: str, settings: Settings) -> None:
    global _daily_date

    today = datetime.now(UTC).date()
    if today != _daily_date:
        _daily.clear()
        _daily_date = today

    if _daily[ip] >= settings.daily_run_quota:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Daily run quota reached. This is a demo deployment on a free tier.",
        )

    window = _recent[ip]
    cutoff = time.monotonic() - 60
    while window and window[0] < cutoff:
        window.popleft()

    if len(window) >= settings.rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit is {settings.rate_limit_per_minute} requests a minute.",
        )

    window.append(time.monotonic())
    _daily[ip] += 1


async def require_demo_key(
    request: Request,
    x_demo_key: str | None = Header(default=None, alias="X-Demo-Key"),
) -> None:
    """Guard a write endpoint.

    When no key is configured the guard still rate-limits but does not reject,
    which is the local development case. `/health` reports which of the two is
    in force, so the difference is never a surprise.
    """
    settings = get_settings()
    _check_rate_limit(_client_ip(request), settings)

    if settings.demo_key is None:
        return

    # Compared with a constant-time function so a wrong key cannot be recovered
    # by timing how long the rejection takes.
    import hmac

    if x_demo_key is None or not hmac.compare_digest(
        x_demo_key, settings.demo_key.get_secret_value()
    ):
        log.info("auth.rejected", ip=_client_ip(request), had_header=x_demo_key is not None)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This demo needs the X-Demo-Key header. The key is in the README.",
        )

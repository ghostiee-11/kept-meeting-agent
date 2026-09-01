"""Database engine and session management.

Neon hands out libpq-style URLs (``sslmode``, ``channel_binding``) which asyncpg
does not understand, and its pooled endpoint runs pgbouncer in transaction mode
which breaks server-side prepared statements. Both are handled here so the rest
of the codebase can use whatever connection string Neon prints without editing
it by hand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

# libpq understands these; asyncpg does not. `sslmode` is translated, the rest
# are dropped.
_LIBPQ_ONLY_PARAMS = frozenset({"sslmode", "channel_binding", "target_session_attrs", "options"})
_SSL_REQUIRED = frozenset({"require", "verify-ca", "verify-full"})


def build_engine_url(raw_url: str) -> tuple[str, dict[str, Any]]:
    """Translate a Neon connection string into a SQLAlchemy URL plus connect args.

    Returns the ``postgresql+asyncpg://`` URL and the keyword arguments asyncpg
    needs, so callers never have to know about either quirk.
    """
    parsed = urlsplit(raw_url)
    params = dict(parse_qsl(parsed.query))

    sslmode = params.get("sslmode")
    for key in _LIBPQ_ONLY_PARAMS:
        params.pop(key, None)

    connect_args: dict[str, Any] = {}
    if sslmode in _SSL_REQUIRED or (parsed.hostname and "neon.tech" in parsed.hostname):
        connect_args["ssl"] = "require"

    # pgbouncer in transaction mode cannot hold server-side prepared statements
    # across pooled connections, so asyncpg must stop creating them.
    if parsed.hostname and "-pooler." in parsed.hostname:
        connect_args["statement_cache_size"] = 0
        params["prepared_statement_cache_size"] = "0"

    scheme = "postgresql+asyncpg"
    url = urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(params), ""))
    return url, connect_args


def create_engine(settings: Settings, *, unpooled: bool = False) -> AsyncEngine:
    """Build the async engine.

    ``unpooled`` selects Neon's direct endpoint, which migrations need because
    Alembic issues statements pgbouncer cannot proxy.
    """
    secret = settings.database_url_unpooled if unpooled else settings.database_url
    if secret is None:
        raise RuntimeError(
            "DATABASE_URL is not configured. Copy .env.example to backend/.env "
            "and fill in the Neon connection string."
        )

    url, connect_args = build_engine_url(secret.get_secret_value())
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Neon computes scale to zero after five minutes idle, which leaves
        # dead sockets in the pool. Recycle before that happens.
        pool_recycle=240,
        pool_pre_ping=True,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on failure."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

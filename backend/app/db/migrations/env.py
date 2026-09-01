"""Alembic environment.

Migrations run against Neon's direct endpoint rather than the pooled one,
because pgbouncer in transaction mode cannot proxy the statements Alembic
issues. The URL is read from settings, never from alembic.ini, so there is
exactly one place a connection string is configured.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db.session import build_engine_url
from app.models import domain  # noqa: F401  (imported for metadata registration)
from app.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_settings = get_settings()
_raw_url = _settings.database_url_unpooled or _settings.database_url
if _raw_url is None:
    raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL must be set to migrate.")

_url, _connect_args = build_engine_url(_raw_url.get_secret_value())
config.set_main_option("sqlalchemy.url", _url)

# Indexes SQLAlchemy cannot describe, so autogenerate must not try to drop them.
# The HNSW index uses pgvector operator classes that have no Core equivalent.
HAND_WRITTEN_INDEXES = frozenset({"ix_commitments_embedding_cosine"})


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    if type_ == "index" and name in HAND_WRITTEN_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

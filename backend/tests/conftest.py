from __future__ import annotations

import os
import pathlib
import re
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.db.session import build_engine_url

# Tests must not inherit the developer's backend/.env. Without this, a local
# key file silently changes what the suite asserts, and CI and a laptop
# disagree about whether a test passes.
Settings.model_config["env_file"] = None

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _test_database_url() -> str | None:
    """Locate a real Postgres for the database-backed tests.

    CI provides TEST_DATABASE_URL in the environment. Locally it lives in
    backend/.env, which the line above deliberately stops Settings reading, so
    this one key is pulled out by hand.
    """
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url

    env_file = BACKEND_ROOT / ".env"
    if not env_file.exists():
        return None
    match = re.search(r"^TEST_DATABASE_URL=(.+)$", env_file.read_text(), re.M)
    return match.group(1).strip().strip('"') if match else None


TEST_DATABASE_URL = _test_database_url()

requires_database = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is not set; see .env.example.",
)


@pytest.fixture(autouse=True)
def clean_settings() -> Iterator[None]:
    """Give every test a fresh Settings instance built only from its own env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Migrate the test database once per run.

    Migrations run rather than `create_all` so the suite exercises the same
    path production does. A broken migration should fail the build, not be
    silently bypassed by a metadata shortcut.

    This fixture is synchronous on purpose: Alembic's async env.py calls
    `asyncio.run`, which raises if it is already inside a running loop.
    """
    assert TEST_DATABASE_URL is not None

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "app" / "db" / "migrations"))
    os.environ["DATABASE_URL_UNPOOLED"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_connection(migrated_database: str) -> AsyncIterator[AsyncConnection]:
    """One long-lived connection every test transaction nests inside."""
    url, connect_args = build_engine_url(migrated_database)
    engine = create_async_engine(url, connect_args=connect_args)
    async with engine.connect() as connection:
        yield connection
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(migrated_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session whose writes are rolled back when the test finishes.

    `join_transaction_mode="create_savepoint"` matters: the mock task API
    commits internally, and without a savepoint that commit would escape the
    test transaction and leave rows behind.
    """
    transaction = await migrated_connection.begin()
    factory = async_sessionmaker(
        bind=migrated_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with factory() as db_session:
        yield db_session
    await transaction.rollback()

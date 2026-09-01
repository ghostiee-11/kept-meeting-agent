"""The external-write path: idempotency, retries, and the circuit breaker.

These run against the real mock API over ASGI rather than a stubbed client, so
a passing test means the HTTP contract holds, not just that a mock was called.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.main import create_app
from app.services.task_client import (
    CircuitBreaker,
    CircuitOpenError,
    TaskClient,
    TaskClientError,
    idempotency_key_for,
)
from tests.conftest import requires_database

pytestmark = requires_database


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """A TaskClient wired to the mock API in-process, over real HTTP semantics."""

    def _make(**settings_env: str) -> TaskClient:
        for key, value in settings_env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

        app = create_app()
        app.dependency_overrides[get_session] = lambda: session
        transport = httpx.ASGITransport(app=app)
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        return TaskClient("", client=http, max_attempts=3)

    return _make


async def test_creates_a_task_and_returns_its_reference(client) -> None:
    async with client() as api:
        task = await api.create_task(
            title="Send the migration plan",
            commitment_id=uuid.uuid4(),
            assignee="Priya",
        )

    assert task.id.startswith("KPT-")
    assert task.status == "todo"
    assert task.assignee == "Priya"


async def test_same_commitment_never_creates_two_tasks(client) -> None:
    """The idempotency key is derived from the commitment, so a replay of the
    same logical write returns the original task rather than a duplicate."""
    commitment_id = uuid.uuid4()

    async with client() as api:
        first = await api.create_task(
            title="Draft the SOC2 evidence list", commitment_id=commitment_id
        )
        second = await api.create_task(
            title="Draft the SOC2 evidence list", commitment_id=commitment_id
        )

    assert first.id == second.id


async def test_retry_recovers_from_a_transient_failure(client) -> None:
    """Every call fails once before succeeding, which three attempts absorb."""
    async with client(MOCK_FAILURE_RATE="0.5", MOCK_FAILURE_MODE="pre") as api:
        task = await api.create_task(title="Book the vendor call", commitment_id=uuid.uuid4())

    assert task.id.startswith("KPT-")


async def test_post_commit_failure_does_not_duplicate_the_task(client) -> None:
    """The hard case: the write lands, then the response fails on the way back.

    The caller must still end up with exactly one task. Without the idempotency
    key the retry would create a second one, and the run would silently produce
    duplicate work for whoever owns the commitment.
    """
    commitment_id = uuid.uuid4()

    async with client(MOCK_FAILURE_RATE="1.0", MOCK_FAILURE_MODE="post") as api:
        task = await api.create_task(
            title="Confirm the pricing change", commitment_id=commitment_id
        )
        every_task = await api.list_tasks()

    matching = [item for item in every_task if item.title == "Confirm the pricing change"]
    assert len(matching) == 1
    assert matching[0].id == task.id


async def test_breaker_opens_and_then_stops_calling_a_failing_system(client) -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=60)

    async with client(MOCK_FAILURE_RATE="1.0", MOCK_FAILURE_MODE="pre") as api:
        api.breaker = breaker
        for _ in range(2):
            with pytest.raises(TaskClientError):
                await api.create_task(title="Doomed", commitment_id=uuid.uuid4())

        # Third call must not reach the network at all.
        with pytest.raises(CircuitOpenError):
            await api.create_task(title="Doomed", commitment_id=uuid.uuid4())


async def test_breaker_half_opens_and_closes_on_a_successful_probe(client) -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_after_seconds=0.0)

    async with client(MOCK_FAILURE_RATE="1.0", MOCK_FAILURE_MODE="pre") as api:
        api.breaker = breaker
        with pytest.raises(TaskClientError):
            await api.create_task(title="Doomed", commitment_id=uuid.uuid4())

    assert breaker.state == "half_open"

    async with client(MOCK_FAILURE_RATE="0.0") as api:
        api.breaker = breaker
        await api.create_task(title="Recovered", commitment_id=uuid.uuid4())

    assert breaker.state == "closed"


async def test_a_client_error_is_not_retried(client) -> None:
    """A 404 will fail identically every time. Retrying it burns the budget and
    would wrongly trip the breaker on a system that is perfectly healthy."""
    async with client() as api:
        assert await api.find_task("KPT-does-not-exist") is None
        assert api.breaker.state == "closed"


def test_idempotency_key_is_stable_for_a_commitment() -> None:
    commitment_id = uuid.uuid4()
    assert idempotency_key_for(commitment_id) == idempotency_key_for(commitment_id)

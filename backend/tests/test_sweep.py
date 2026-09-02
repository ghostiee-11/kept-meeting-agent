"""The nightly sweep.

The only part of the system that runs because a date passed rather than
because a meeting happened. Nothing else notices that Tuesday's promise is now
four days old, so if this is wrong the ledger quietly stops being about
anything.

The model call is exercised separately. What is tested here is the arithmetic
and the lock on the door, both of which run unattended every night.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.main import create_app
from app.models.base import CommitmentKind, CommitmentStatus
from app.models.domain import Commitment, Meeting, Person, Workspace
from app.services.temporal import today_in
from tests.conftest import requires_database

pytestmark = requires_database

TOKEN = "sweep-token-for-tests"


@pytest.fixture
async def api(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    monkeypatch.setenv("INTERNAL_JOB_TOKEN", TOKEN)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def ledger(session: AsyncSession) -> dict[str, Commitment]:
    """One commitment late by a week, one due tomorrow, one late but finished.

    Only the first is anybody's problem, and telling them apart is the whole
    job.
    """
    today = today_in("Asia/Kolkata")
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4()}", settings={})
    session.add(workspace)
    await session.flush()

    person = Person(workspace_id=workspace.id, name="Priya Nair", aliases=["Priya"])
    meeting = Meeting(
        workspace_id=workspace.id,
        title="Planning",
        occurred_at=datetime.now(UTC),
        raw_transcript="Priya: I'll have the migration plan by Tuesday.",
        transcript_sha256=uuid.uuid4().hex,
        participants=["Priya"],
    )
    session.add_all([person, meeting])
    await session.flush()

    def commitment(text: str, due: object, status: CommitmentStatus) -> Commitment:
        return Commitment(
            workspace_id=workspace.id,
            canonical_key=f"priya:{uuid.uuid4().hex[:8]}",
            text=text,
            kind=CommitmentKind.COMMITMENT,
            status=status,
            owner_id=person.id,
            owner_confidence=1.0,
            due_confidence=1.0,
            due_date=due,
            original_due_date=due,
            evidence=[{"quote": "I'll have the migration plan by Tuesday."}],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
            slip_count=2,
        )

    rows = {
        "late": commitment(
            "Send the migration plan", today - timedelta(days=7), CommitmentStatus.CONFIRMED
        ),
        "upcoming": commitment(
            "Book the vendor call", today + timedelta(days=1), CommitmentStatus.CONFIRMED
        ),
        "finished": commitment(
            "Ship the EU region", today - timedelta(days=3), CommitmentStatus.DONE
        ),
    }
    session.add_all(list(rows.values()))
    await session.flush()
    return rows


async def test_the_sweep_reports_only_what_is_actually_late(api, ledger) -> None:
    response = await api.post("/internal/sweep?dry_run=true", headers={"X-Internal-Token": TOKEN})

    assert response.status_code == 200
    body = response.json()
    texts = {row["text"] for row in body["overdue"]}

    assert texts == {"Send the migration plan"}, "done work and future work are not late"
    assert body["overdue"][0]["days_late"] == 7
    assert body["overdue"][0]["owner"] == "Priya Nair"
    # Stated owner, stated date, so the only risk here is the trouble itself:
    # a week past due and moved twice.
    assert body["overdue"][0]["risk_band"] == "medium"
    assert body["overdue"][0]["slip_count"] == 2


async def test_a_dry_run_writes_nothing(api, ledger, session: AsyncSession) -> None:
    """The flag exists so the report can be read without spending a model call
    on a nudge nobody asked for."""
    from sqlalchemy import select

    from app.models.domain import Communication

    await api.post("/internal/sweep?dry_run=true", headers={"X-Internal-Token": TOKEN})

    drafts = (await session.scalars(select(Communication))).all()
    assert drafts == []


async def test_the_wrong_token_is_refused(api, ledger) -> None:
    response = await api.post("/internal/sweep", headers={"X-Internal-Token": "guess"})
    assert response.status_code == 401


async def test_no_token_at_all_is_refused(api, ledger) -> None:
    """An unauthenticated caller must not be able to make the system spend
    model budget on a schedule of their choosing."""
    response = await api.post("/internal/sweep")
    assert response.status_code == 401


async def test_the_endpoint_is_closed_when_no_secret_is_configured(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing configuration closes the door rather than opening it."""
    monkeypatch.delenv("INTERNAL_JOB_TOKEN", raising=False)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/internal/sweep", headers={"X-Internal-Token": "anything"})

    assert response.status_code == 503

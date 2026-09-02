"""Answering a clarification.

Detecting ambiguity and raising a precise question is half of ambiguity
handling. This is the half that changes anything, and every test here is
really about the audit trail: after a human answers, the ledger must show
which fact came from a person.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.main import create_app
from app.models.base import ActorKind, ClarificationStatus, CommitmentKind, CommitmentStatus
from app.models.domain import (
    Clarification,
    Commitment,
    CommitmentEvent,
    Meeting,
    Person,
    Run,
    Workspace,
)
from tests.conftest import requires_database

pytestmark = requires_database


@pytest.fixture
async def api(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def unowned(session: AsyncSession) -> Clarification:
    """A commitment nobody owns, with the question the agents raised about it."""
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4()}", settings={})
    session.add(workspace)
    await session.flush()

    person = Person(workspace_id=workspace.id, name="Priya Nair", aliases=["Priya", "Pri"])
    session.add(person)

    meeting = Meeting(
        workspace_id=workspace.id,
        title="Planning",
        occurred_at=datetime.now(UTC),
        raw_transcript="Meera: Someone needs to update the architecture doc.",
        transcript_sha256=uuid.uuid4().hex,
        participants=["Meera"],
    )
    session.add(meeting)
    await session.flush()

    commitment = Commitment(
        workspace_id=workspace.id,
        canonical_key="unowned:architecture-doc",
        text="Update the architecture doc",
        kind=CommitmentKind.ACTION_ITEM,
        status=CommitmentStatus.NEEDS_CLARIFICATION,
        evidence=[{"quote": "Someone needs to update the architecture doc."}],
        first_seen_meeting_id=meeting.id,
        last_seen_meeting_id=meeting.id,
    )
    run = Run(meeting_id=meeting.id, thread_id=str(uuid.uuid4()))
    session.add_all([commitment, run])
    await session.flush()

    clarification = Clarification(
        commitment_id=commitment.id,
        run_id=run.id,
        question="Nobody took this on. Who owns it?",
        options=[{"label": "Priya Nair"}],
        thread_id=run.thread_id,
        status=ClarificationStatus.OPEN,
    )
    session.add(clarification)
    await session.flush()
    return clarification


async def test_answering_assigns_the_owner_and_confirms_the_commitment(
    api, session: AsyncSession, unowned: Clarification
) -> None:
    response = await api.post(
        f"/clarifications/{unowned.id}/resolve",
        json={"owner": "Priya", "answered_by": "meera"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"]["owner"] == "Priya Nair"
    assert body["commitment_status"] == CommitmentStatus.CONFIRMED.value

    commitment = await session.get(Commitment, unowned.commitment_id)
    assert commitment is not None
    assert commitment.owner_id is not None
    assert commitment.owner_confidence == 1.0, "a human said so; that is certainty"


async def test_the_answer_is_recorded_as_coming_from_a_human(
    api, session: AsyncSession, unowned: Clarification
) -> None:
    """The reason every event carries actor_kind. After the fact, "who decided
    this" has to be answerable."""
    await api.post(
        f"/clarifications/{unowned.id}/resolve",
        json={"owner": "Priya", "answered_by": "meera"},
    )

    events = (
        await session.scalars(
            select(CommitmentEvent).where(CommitmentEvent.commitment_id == unowned.commitment_id)
        )
    ).all()

    assert events, "answering must leave a trail"
    assert all(event.actor_kind is ActorKind.HUMAN for event in events)
    assert any(event.actor == "meera" for event in events)


async def test_a_name_not_on_the_roster_is_refused(api, unowned: Clarification) -> None:
    """The same rule the Attributor follows. A human typing a name does not
    make that person exist."""
    response = await api.post(f"/clarifications/{unowned.id}/resolve", json={"owner": "Jonathan"})

    assert response.status_code == 422
    assert "roster" in response.text


async def test_a_spoken_deadline_is_accepted_not_just_an_iso_date(
    api, session: AsyncSession, unowned: Clarification
) -> None:
    """A person answering should be able to type what they would say."""
    response = await api.post(
        f"/clarifications/{unowned.id}/resolve",
        json={"owner": "Priya", "due_date": "next Friday"},
    )

    assert response.status_code == 200
    assert response.json()["applied"]["due_date"] is not None


async def test_an_unreadable_date_is_refused_rather_than_guessed(
    api, unowned: Clarification
) -> None:
    response = await api.post(
        f"/clarifications/{unowned.id}/resolve",
        json={"owner": "Priya", "due_date": "whenever"},
    )

    assert response.status_code == 422


async def test_answering_twice_is_idempotent(api, unowned: Clarification) -> None:
    """A double-click, or a reviewer on two devices, should not 409 over an
    answer that has already been applied."""
    first = await api.post(f"/clarifications/{unowned.id}/resolve", json={"owner": "Priya"})
    second = await api.post(f"/clarifications/{unowned.id}/resolve", json={"owner": "Priya"})

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == ClarificationStatus.RESOLVED.value


async def test_dismissing_closes_the_question_without_changing_the_ledger(
    api, session: AsyncSession, unowned: Clarification
) -> None:
    response = await api.post(f"/clarifications/{unowned.id}/resolve", json={"dismiss": True})

    assert response.json()["status"] == ClarificationStatus.ABANDONED.value
    commitment = await session.get(Commitment, unowned.commitment_id)
    assert commitment is not None
    assert commitment.owner_id is None


async def test_a_missing_clarification_is_a_404(api) -> None:
    response = await api.post(f"/clarifications/{uuid.uuid4()}/resolve", json={})

    assert response.status_code == 404

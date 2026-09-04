"""Reading the same meeting twice.

The Historian writes one row per (commitment, meeting) pair and the database
enforces that with a unique constraint. It ran during the graph, before the
persistence layer's repeat-run guard could decide the meeting had already been
processed, so submitting the same transcript a second time crashed the run
with a UniqueViolationError.

The constraint was right and the writer was wrong. These tests pin both halves
of the fix: the row is rewritten rather than duplicated, and a silence already
counted is not counted again, because that number feeds risk and would
otherwise make a commitment look staler on every re-run than it actually is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.historian import Slippage, _record_mention, _record_silence
from app.models.base import CommitmentKind, CommitmentStatus, MentionOutcome
from app.models.domain import Commitment, CommitmentMention, Meeting, Workspace
from tests.conftest import requires_database

pytestmark = requires_database

TODAY = date(2026, 9, 4)


@pytest.fixture
async def promise(session: AsyncSession) -> Commitment:
    """One commitment, overdue, from a meeting that can be read again."""
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4()}", settings={})
    session.add(workspace)
    await session.flush()

    meeting = Meeting(
        workspace_id=workspace.id,
        title="Standup",
        occurred_at=datetime.now(UTC),
        raw_transcript="Priya: I'll have the migration plan by Friday.",
        transcript_sha256=uuid.uuid4().hex,
        participants=["Priya"],
    )
    session.add(meeting)
    await session.flush()

    commitment = Commitment(
        workspace_id=workspace.id,
        canonical_key=f"priya:{uuid.uuid4().hex[:8]}",
        text="Deliver the migration plan",
        kind=CommitmentKind.COMMITMENT,
        status=CommitmentStatus.CONFIRMED,
        due_date=TODAY - timedelta(days=3),
        evidence=[],
        first_seen_meeting_id=meeting.id,
        last_seen_meeting_id=meeting.id,
    )
    session.add(commitment)
    await session.flush()
    return commitment


async def test_reading_the_same_meeting_twice_does_not_crash(
    session: AsyncSession, promise: Commitment
) -> None:
    """The exact failure: duplicate key on (commitment_id, meeting_id)."""
    meeting_id = promise.first_seen_meeting_id
    assert meeting_id is not None

    first = await _record_mention(
        session,
        commitment=promise,
        meeting_id=meeting_id,
        outcome=MentionOutcome.PROGRESS,
        reasoning="Priya says it is nearly done.",
    )
    await session.flush()

    second = await _record_mention(
        session,
        commitment=promise,
        meeting_id=meeting_id,
        outcome=MentionOutcome.COMPLETED,
        reasoning="Second read of the same meeting.",
    )
    await session.flush()

    rows = (
        await session.scalars(
            select(CommitmentMention).where(CommitmentMention.commitment_id == promise.id)
        )
    ).all()

    assert first is True
    assert second is False, "the second read is not a new finding"
    assert len(rows) == 1
    assert rows[0].outcome is MentionOutcome.COMPLETED, "the newer reading wins"


async def test_silence_is_counted_once_however_often_a_meeting_is_read(
    session: AsyncSession, promise: Commitment
) -> None:
    """The quieter half of the bug. `silence_streak` feeds the risk score, so
    a re-run inflating it makes the system lie about how long something has
    gone unmentioned."""
    meeting_id = promise.first_seen_meeting_id
    assert meeting_id is not None

    for _ in range(3):
        await _record_silence(
            session,
            [promise],
            Slippage(),
            meeting_id=meeting_id,
            as_of=TODAY,
        )
        await session.flush()

    assert promise.silence_streak == 1


async def test_a_genuinely_new_meeting_counts_again(
    session: AsyncSession, promise: Commitment
) -> None:
    """Idempotency is per meeting, not per commitment. Two meetings in a row
    that both fail to mention a promise is exactly the signal worth having."""
    first_meeting = promise.first_seen_meeting_id
    assert first_meeting is not None

    second_meeting = Meeting(
        workspace_id=promise.workspace_id,
        title="Standup, the week after",
        occurred_at=datetime.now(UTC),
        raw_transcript="Meera: nothing from me.",
        transcript_sha256=uuid.uuid4().hex,
        participants=["Meera"],
    )
    session.add(second_meeting)
    await session.flush()

    for meeting_id in (first_meeting, second_meeting.id):
        await _record_silence(session, [promise], Slippage(), meeting_id=meeting_id, as_of=TODAY)
        await session.flush()

    assert promise.silence_streak == 2

"""Running the same transcript twice.

A reviewer clicking a sample transcript a second time is the most likely thing
anyone will do to this system, and the ledger has to survive it. The meeting is
keyed on the transcript hash, so the second run is a re-run of a meeting that
already exists. Writing its output again would double every commitment and then
hand the Historian a slippage report assembled out of the workspace's own
duplicates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import (
    Classification,
    Evidence,
    ExtractedCommitment,
    ExtractedDecision,
)
from app.graph.state import MeetingState, ResolvedItem
from app.models.base import EventType, RunStatus
from app.models.domain import Commitment, CommitmentEvent, Decision, Meeting, Run, Workspace
from app.services import persistence
from app.services.roster import Attribution
from app.services.temporal import Resolved
from app.services.trace import RunTrace
from tests.conftest import requires_database

pytestmark = requires_database

TRANSCRIPT = "Meera: We're keeping the Friday release date rather than slipping to Monday.\n"


def state() -> MeetingState:
    """What the graph hands persistence after a run of the transcript above."""
    return cast(
        MeetingState,
        {
            "decisions": [
                ExtractedDecision(
                    statement="The team will keep the Friday release date.",
                    confidence=0.9,
                    evidence=[Evidence(quote=TRANSCRIPT.split(": ", 1)[1].strip())],
                )
            ],
            "items": [],
            "rejections": [],
            "questions": [],
            "enrichments": {},
            "communications": {"recap": "Decisions: keep Friday.", "recap_subject": "Recap"},
        },
    )


def _obligation() -> ResolvedItem:
    """One commitment, owned by nobody in particular but dated, which is enough
    to make it a row in the ledger."""
    return ResolvedItem(
        commitment=ExtractedCommitment(
            text="Ship the release on Friday.",
            classification=Classification.COMMITMENT,
            reasoning="Meera accepted it.",
            confidence=0.9,
            evidence=[Evidence(quote=TRANSCRIPT.split(": ", 1)[1].strip())],
        ),
        attribution=Attribution(
            person_id=None,
            display_name="Meera",
            confidence=0.5,
            reason="Named in the turn.",
            method="speaker",
        ),
        deadline=Resolved(due=date(2026, 9, 4), confidence=0.9, method="dateparser", raw="Friday"),
    )


async def _meeting(session: AsyncSession) -> Meeting:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4()}", settings={})
    session.add(workspace)
    await session.flush()

    meeting, created = await persistence.upsert_meeting(
        session,
        workspace_id=workspace.id,
        title="Release planning",
        transcript=TRANSCRIPT,
        occurred_at=datetime.now(UTC),
        timezone="Asia/Kolkata",
        participants=["Meera"],
        turns=[],
    )
    assert created
    return meeting


async def _run(session: AsyncSession, meeting: Meeting) -> Run:
    run = Run(meeting_id=meeting.id, thread_id=str(uuid.uuid4()), status=RunStatus.RUNNING)
    session.add(run)
    await session.flush()
    return run


async def _save(session: AsyncSession, meeting: Meeting) -> dict[str, int]:
    run = await _run(session, meeting)
    recorder = RunTrace(str(run.id))
    recorder.record("analyst:decisions", "artifact", payload={"kept": 1})
    return await persistence.save_run_results(
        session, run=run, meeting=meeting, state=state(), recorder=recorder
    )


async def test_the_same_transcript_twice_leaves_one_copy_of_the_ledger(
    session: AsyncSession,
) -> None:
    meeting = await _meeting(session)

    first = await _save(session, meeting)
    second = await _save(session, meeting)

    decisions = (
        await session.scalars(select(Decision).where(Decision.meeting_id == meeting.id))
    ).all()

    assert first["decisions"] == 1
    assert len(decisions) == 1, "the second run must not write the same decision again"
    assert second["decisions"] == 1, "the counts describe the meeting, not the individual run"


async def test_the_repeat_run_still_records_what_the_agents_did(session: AsyncSession) -> None:
    """The agents genuinely ran. Dropping their trace would make the ops view
    lie about what the system spent."""
    meeting = await _meeting(session)
    await _save(session, meeting)

    run = await _run(session, meeting)
    recorder = RunTrace(str(run.id))
    recorder.record("chief_of_staff", "model_call", tokens_in=700, tokens_out=90, cost_usd=0.0001)
    counts = await persistence.save_run_results(
        session, run=run, meeting=meeting, state=state(), recorder=recorder
    )

    assert counts["trace"] == 1
    assert run.status is RunStatus.SUCCEEDED
    assert run.tokens_in == 700
    assert run.finished_at is not None


async def test_a_run_that_died_before_persisting_still_gets_to_write(
    session: AsyncSession,
) -> None:
    """The guard is on rows, not on whether the meeting record was new. A first
    attempt that crashed mid-run leaves the meeting behind with nothing in it,
    and the retry has to be allowed to fill it."""
    meeting = await _meeting(session)

    # The meeting exists, the extraction never landed.
    assert not await persistence._already_extracted(session, meeting=meeting)

    counts = await _save(session, meeting)

    assert counts["decisions"] == 1


async def test_a_different_transcript_is_a_different_meeting(session: AsyncSession) -> None:
    """The guard keys on the meeting, so a genuine second meeting is unaffected
    even when it decides something identical."""
    first = await _meeting(session)
    await _save(session, first)

    second, created = await persistence.upsert_meeting(
        session,
        workspace_id=first.workspace_id,
        title="Release planning, again",
        transcript=TRANSCRIPT + "Adit: Agreed.\n",
        occurred_at=datetime.now(UTC),
        timezone="Asia/Kolkata",
        participants=["Meera", "Adit"],
        turns=[],
    )
    assert created

    counts = await _save(session, second)
    assert counts["decisions"] == 1

    everything: Any = (await session.scalars(select(Decision))).all()
    assert len([row for row in everything if row.meeting_id in {first.id, second.id}]) == 2


async def test_commitments_are_not_duplicated_across_repeat_runs(session: AsyncSession) -> None:
    """The count that actually matters. Duplicated commitments would show up as
    phantom slippage the next time the Historian runs, because the workspace
    would be full of open promises it had made to itself."""
    meeting = await _meeting(session)
    with_promise = state()
    with_promise["items"] = [_obligation()]

    for _ in range(2):
        run = await _run(session, meeting)
        await persistence.save_run_results(
            session,
            run=run,
            meeting=meeting,
            state=with_promise,
            recorder=RunTrace(str(run.id)),
        )

    commitments = (
        await session.scalars(
            select(Commitment).where(Commitment.first_seen_meeting_id == meeting.id)
        )
    ).all()
    events = (
        await session.scalars(
            select(CommitmentEvent).where(CommitmentEvent.meeting_id == meeting.id)
        )
    ).all()

    assert len(commitments) == 1
    assert [event.type for event in events] == [EventType.CREATED, EventType.DEADLINE_SET]

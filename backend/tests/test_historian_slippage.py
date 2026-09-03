"""Cross-meeting slippage, proved rather than asserted.

The fuzzy-match harness in evals/runner.py scores one transcript against a set
of expected commitments, which has nothing to say about a *pair* of meetings
and the state that connects them. This is the real proof.

Meeting one's commitments are seeded directly rather than extracted, on
purpose. What is actually under test here is the Historian's reconciliation,
not whether extraction succeeds twice in a row under a shared free-tier rate
limit; conflating the two would make a Historian bug and a rate-limit hiccup
indistinguishable. Meeting two runs through the real pipeline (extraction,
review, resolution) and then the real Historian, so the adjudication a model
actually performs is what gets checked.

This test needs a live model provider, which conftest deliberately strips from
every other test's environment for isolation. It reads GROQ_API_KEY back out of
backend/.env for itself, the same way conftest reads TEST_DATABASE_URL, rather
than weakening that isolation for the whole suite.

Groq's free tier caps at 2000 tokens a day per model on top of the per-minute
budget app.services.rate_budget paces around. A day of running this file
repeatedly can exhaust that, in which case it fails with a 429 that says so
explicitly rather than a wrong assertion; each test has been verified
individually with a fresh daily budget. Configuring GOOGLE_API_KEY or
OPENAI_API_KEY gives ModelFallbackMiddleware somewhere to go when this happens
in production.
"""

from __future__ import annotations

import os
import pathlib
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import COMMITTED_CLASSES
from app.agents.historian import reconcile
from app.agents.intelligence import extract, review
from app.agents.resolution import resolve_deadlines, resolve_owners
from app.config import Settings
from app.graph.state import MeetingState, ResolvedItem
from app.models.base import CommitmentKind, CommitmentStatus, RunStatus
from app.models.domain import Commitment, Meeting, Person, Run, Workspace
from app.services import persistence
from app.services.ledger import canonical_key
from app.services.model_router import ModelRouter
from app.services.roster import RosterEntry
from app.services.segmentation import segment
from app.services.trace import RunTrace
from tests.conftest import requires_database

GOLD = pathlib.Path(__file__).resolve().parents[2] / "evals" / "gold"
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
FOLLOWUP_DATE = date(2026, 9, 16)


def _provider_key(name: str) -> str | None:
    """Read one credential out of backend/.env, bypassing conftest's isolation.

    Deliberately narrow: only this test needs a live model, and reaching past
    the isolation for the whole suite would reintroduce the shell-env bug that
    conftest exists to prevent.
    """
    if value := os.environ.get(name):
        return value
    env_file = BACKEND_ROOT / ".env"
    if not env_file.exists():
        return None
    match = re.search(rf"^{name}=(.+)$", env_file.read_text(), re.M)
    return match.group(1).strip().strip('"') if match else None


requires_live_model = pytest.mark.skipif(
    _provider_key("GROQ_API_KEY") is None
    and _provider_key("GOOGLE_API_KEY") is None
    and _provider_key("OPENAI_API_KEY") is None,
    reason="No model provider credentials in backend/.env.",
)

pytestmark = [requires_database, requires_live_model]


def _live_settings() -> Settings:
    """A Settings instance carrying real model credentials, independent of
    conftest's process-wide isolation."""
    return Settings(
        groq_api_key=_provider_key("GROQ_API_KEY"),
        google_api_key=_provider_key("GOOGLE_API_KEY"),
        openai_api_key=_provider_key("OPENAI_API_KEY"),
        tavily_api_key=_provider_key("TAVILY_API_KEY"),
    )


async def _seed_roster(session: AsyncSession) -> tuple[Workspace, list[RosterEntry]]:
    workspace = Workspace(name="Historian test", slug=f"historian-{uuid.uuid4()}", settings={})
    session.add(workspace)
    await session.flush()

    people = [
        Person(workspace_id=workspace.id, name=name, aliases=list(aliases))
        for name, aliases in [
            ("Priya Nair", ("Priya", "Pri")),
            ("Rahul Menon", ("Rahul", "Rah")),
            ("Adit Sharma", ("Adit", "Adi")),
            ("Meera Krishnan", ("Meera",)),
            ("Tom Whitfield", ("Tom",)),
            ("Sana Qureshi", ("Sana",)),
        ]
    ]
    session.add_all(people)
    await session.flush()

    roster = [RosterEntry(p.id, p.name, tuple(p.aliases)) for p in people]
    return workspace, roster


async def _seed_meeting_one(
    session: AsyncSession, workspace: Workspace, people: dict[str, Person]
) -> None:
    """Seed sprint planning's outcome directly: what a person reading that
    transcript would have written down, without spending a live extraction run
    to reproduce it."""
    meeting = Meeting(
        workspace_id=workspace.id,
        title="Sprint planning",
        occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
        timezone="Asia/Kolkata",
        raw_transcript=(GOLD / "hard_cases.txt").read_text(),
        transcript_sha256=uuid.uuid4().hex,
        participants=["Meera", "Priya", "Adit", "Tom", "Rahul", "Sana"],
    )
    session.add(meeting)
    await session.flush()

    rows = [
        Commitment(
            workspace_id=workspace.id,
            canonical_key=canonical_key("Deliver the migration plan", "Priya Nair"),
            text="Deliver the migration plan",
            kind=CommitmentKind.COMMITMENT,
            status=CommitmentStatus.CONFIRMED,
            owner_id=people["Priya Nair"].id,
            owner_confidence=1.0,
            due_date=date(2026, 9, 8),
            due_confidence=0.95,
            due_raw_text="Tuesday",
            evidence=[{"quote": "I'll get it to you Tuesday instead."}],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
        ),
        Commitment(
            workspace_id=workspace.id,
            canonical_key=canonical_key("Ship the EU region", "Rahul Menon"),
            text="Ship the EU region",
            kind=CommitmentKind.COMMITMENT,
            status=CommitmentStatus.CONFIRMED,
            owner_id=people["Rahul Menon"].id,
            owner_confidence=1.0,
            due_date=date(2026, 9, 7),
            due_confidence=0.95,
            due_raw_text="Monday",
            blocked_by="legal sign-off on data residency",
            evidence=[
                {
                    "quote": (
                        "If legal signs off on the data residency question, "
                        "I'll ship the EU region on Monday."
                    )
                }
            ],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
        ),
        Commitment(
            workspace_id=workspace.id,
            canonical_key=canonical_key("Own the vendor call with Vanta", "Priya Nair"),
            text="Own the vendor call with Vanta",
            kind=CommitmentKind.ACTION_ITEM,
            status=CommitmentStatus.CONFIRMED,
            owner_id=people["Priya Nair"].id,
            owner_confidence=1.0,
            evidence=[{"quote": "Priya, can you also own the vendor call with Vanta?"}],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
        ),
        # Genuinely unmentioned in the follow-up, unlike the vendor call: the
        # follow-up transcript never brings up the architecture doc at all, so
        # this is the row that proves silence detection rather than confusing
        # it with a commitment somebody explicitly addressed and stalled on.
        Commitment(
            workspace_id=workspace.id,
            canonical_key=canonical_key("Update the architecture doc", "Rahul Menon"),
            text="Update the architecture doc",
            kind=CommitmentKind.ACTION_ITEM,
            status=CommitmentStatus.CONFIRMED,
            owner_id=people["Rahul Menon"].id,
            owner_confidence=0.6,
            due_date=date(2026, 9, 5),
            evidence=[{"quote": "We'll get that sorted."}],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
        ),
    ]
    session.add_all(rows)
    await session.flush()


async def test_the_migration_plan_is_recognised_as_slipped_a_second_time(
    session: AsyncSession,
) -> None:
    """Promised Friday, then Tuesday, now Thursday in the follow-up. The
    Historian's job is to know this is the same promise moving for a second
    time, not a new one."""
    settings = _live_settings()
    router = ModelRouter(settings)
    workspace, roster = await _seed_roster(session)
    people = await _people_by_name(session, workspace)
    await _seed_meeting_one(session, workspace, people)

    found = await _run_followup(session, workspace, roster, router, settings)

    assert _mentions(found["slipped"], "migration")


async def test_the_eu_region_is_recognised_as_completed(session: AsyncSession) -> None:
    """Legal cleared it and Rahul shipped it Monday, exactly as conditioned."""
    settings = _live_settings()
    router = ModelRouter(settings)
    workspace, roster = await _seed_roster(session)
    people = await _people_by_name(session, workspace)
    await _seed_meeting_one(session, workspace, people)

    found = await _run_followup(session, workspace, roster, router, settings)

    assert _mentions(found["completed"], "eu") or _mentions(found["completed"], "region")


async def test_the_architecture_doc_going_unmentioned_is_caught(session: AsyncSession) -> None:
    """The follow-up never brings up the architecture doc at all. This is the
    finding no summarizer produces, because there is nothing in the transcript
    to summarise: the absence itself is the signal.

    Deliberately not the vendor call: the follow-up transcript has Priya say
    "Haven't got to it" when asked about Vanta, which is a real mention (and
    should land as progress or blocked, not silence). Testing silence against
    a commitment someone explicitly addressed would be checking the wrong
    thing."""
    settings = _live_settings()
    router = ModelRouter(settings)
    workspace, roster = await _seed_roster(session)
    people = await _people_by_name(session, workspace)
    await _seed_meeting_one(session, workspace, people)

    found = await _run_followup(session, workspace, roster, router, settings)

    assert any("architecture" in text.lower() for text in found["unmentioned"])


async def _people_by_name(session: AsyncSession, workspace: Workspace) -> dict[str, Person]:
    people = (
        await session.scalars(select(Person).where(Person.workspace_id == workspace.id))
    ).all()
    return {person.name: person for person in people}


async def _run_followup(
    session: AsyncSession,
    workspace: Workspace,
    roster: list[RosterEntry],
    router: ModelRouter,
    settings: Settings,
) -> dict[str, list[str]]:
    """Run the follow-up transcript through the real pipeline and the real
    Historian, and return what it found."""
    transcript = (GOLD / "followup_slippage.txt").read_text()
    turns = segment(transcript)

    meeting, _ = await persistence.upsert_meeting(
        session,
        workspace_id=workspace.id,
        title="Follow-up planning",
        transcript=transcript,
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
        timezone="Asia/Kolkata",
        participants=[],
        turns=[t.as_dict() for t in turns],
    )
    await session.flush()

    found = await extract(transcript, turns, router=router, settings=settings)
    kept, _ = await review(found.commitments, transcript, turns, router=router, settings=settings)
    obligations = [item for item in kept if item.classification in COMMITTED_CLASSES]

    attributed = await resolve_owners(obligations, roster, turns, router=router, settings=settings)
    deadlines = await resolve_deadlines(
        obligations,
        meeting_date=FOLLOWUP_DATE,
        timezone="Asia/Kolkata",
        router=router,
        settings=settings,
    )

    items = [
        ResolvedItem(commitment=commitment, attribution=attribution, deadline=deadline)
        for (commitment, attribution), deadline in zip(attributed, deadlines, strict=True)
    ]

    run = Run(meeting_id=meeting.id, thread_id=str(uuid.uuid4()), status=RunStatus.RUNNING)
    session.add(run)
    await session.flush()

    state: MeetingState = {  # type: ignore[typeddict-item]
        "decisions": [],
        "items": items,
        "rejections": [],
        "questions": [],
    }
    await persistence.save_run_results(
        session, run=run, meeting=meeting, state=state, recorder=RunTrace(run_id=str(run.id))
    )
    await session.flush()

    found_slippage = await reconcile(
        session,
        transcript,
        items,
        workspace_id=workspace.id,
        meeting_id=meeting.id,
        meeting_date=FOLLOWUP_DATE,
        timezone="Asia/Kolkata",
        router=router,
        settings=settings,
    )
    await session.flush()

    return {
        "progressed": found_slippage.progressed,
        "completed": found_slippage.completed,
        "slipped": found_slippage.slipped,
        "blocked": found_slippage.blocked,
        "unmentioned": found_slippage.unmentioned,
    }


def _mentions(bucket: list[str], *needles: str) -> bool:
    return any(all(needle.lower() in text.lower() for needle in needles) for text in bucket)

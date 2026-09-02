"""Read endpoints for the console.

Risk is recomputed on read rather than served from the stored column. The
stored value is what the score was when the run happened; a commitment that
was fine on Tuesday is overdue by Friday without anything having been written
to it. Since scoring is a pure function over data already loaded, recomputing
costs nothing and is the only way the number is ever right.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.deps import SessionDep, SettingsDep
from app.models.base import ClarificationStatus, CommitmentStatus
from app.models.domain import (
    AgentTraceEntry,
    Clarification,
    Commitment,
    CommitmentEvent,
    Communication,
    Decision,
    Meeting,
    MockTask,
    Person,
    Rejection,
    Run,
)
from app.services.risk import RiskAssessment, score_commitment
from app.services.temporal import today_in

router = APIRouter(tags=["views"])

CommitmentFilter = Literal["all", "at_risk", "unowned", "no_deadline", "overdue", "needs_input"]


class MeetingSummary(BaseModel):
    id: uuid.UUID
    title: str
    occurred_at: datetime
    project: str | None
    participants: list[str]
    commitments: int
    decisions: int
    open_questions: int


class EvidenceOut(BaseModel):
    quote: str
    speaker: str | None = None
    start: int | None = None
    end: int | None = None
    match: str | None = None


class CommitmentOut(BaseModel):
    id: uuid.UUID
    text: str
    kind: str
    status: str
    owner: str | None
    owner_id: uuid.UUID | None
    owner_confidence: float
    owner_reason: str | None
    due_date: date | None
    original_due_date: date | None
    due_spoken_as: str | None
    due_confidence: float
    slip_count: int
    silence_streak: int
    blocked_by: str | None
    external_task_id: str | None
    evidence: list[EvidenceOut]
    risk_score: float
    risk_band: str
    risk_why: str
    risk_factors: list[dict[str, Any]]
    meeting_id: uuid.UUID | None


class DecisionOut(BaseModel):
    id: uuid.UUID
    statement: str
    rationale: str | None
    alternatives_considered: list[str]
    confidence: float
    evidence: list[EvidenceOut]
    enrichment: dict[str, Any] | None


class RejectionOut(BaseModel):
    id: uuid.UUID
    text: str
    rejected_by: str
    stage: str
    reason: str


class CommunicationOut(BaseModel):
    id: uuid.UUID
    kind: str
    subject: str | None
    body: str
    status: str
    created_at: datetime


class MeetingDetail(BaseModel):
    meeting: MeetingSummary
    transcript: str
    decisions: list[DecisionOut]
    commitments: list[CommitmentOut]
    rejections: list[RejectionOut]
    communications: list[CommunicationOut]


class ClarificationOut(BaseModel):
    id: uuid.UUID
    question: str
    options: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    status: str
    commitment_id: uuid.UUID | None
    commitment_text: str | None
    asked_at: datetime


class TraceOut(BaseModel):
    seq: int
    agent: str
    event: str
    provider: str | None
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    payload: dict[str, Any]


class RunOut(BaseModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    status: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    trace: list[TraceOut]


def _assess(commitment: Commitment, today: date, open_questions: int = 0) -> RiskAssessment:
    return score_commitment(
        due_date=commitment.due_date,
        today=today,
        status=commitment.status.value,
        owner_id=commitment.owner_id,
        owner_confidence=commitment.owner_confidence,
        due_confidence=commitment.due_confidence,
        slip_count=commitment.slip_count,
        silence_streak=commitment.silence_streak,
        open_questions=open_questions,
        blocked=bool(commitment.blocked_by),
    )


def _to_out(commitment: Commitment, owner: str | None, today: date) -> CommitmentOut:
    risk = _assess(commitment, today)
    return CommitmentOut(
        id=commitment.id,
        text=commitment.text,
        kind=commitment.kind.value,
        status=commitment.status.value,
        owner=owner,
        owner_id=commitment.owner_id,
        owner_confidence=commitment.owner_confidence,
        owner_reason=commitment.owner_inference_reason,
        due_date=commitment.due_date,
        original_due_date=commitment.original_due_date,
        due_spoken_as=commitment.due_raw_text,
        due_confidence=commitment.due_confidence,
        slip_count=commitment.slip_count,
        silence_streak=commitment.silence_streak,
        blocked_by=commitment.blocked_by,
        external_task_id=commitment.external_task_id,
        evidence=[EvidenceOut(**item) for item in commitment.evidence],
        risk_score=risk.score,
        risk_band=risk.band,
        risk_why=risk.explain(),
        risk_factors=risk.as_dicts(),
        meeting_id=commitment.last_seen_meeting_id,
    )


async def _count_by(session: SessionDep, query: Any) -> dict[uuid.UUID, int]:
    """Run a grouped count and index it by the grouping key."""
    return dict((await session.execute(query)).all())  # type: ignore[arg-type]


async def _owner_names(session: SessionDep) -> dict[uuid.UUID, str]:
    return {person.id: person.name for person in (await session.scalars(select(Person))).all()}


@router.get("/meetings", response_model=list[MeetingSummary])
async def list_meetings(
    session: SessionDep, limit: Annotated[int, Query(le=100)] = 50
) -> list[MeetingSummary]:
    meetings = (
        await session.scalars(select(Meeting).order_by(Meeting.occurred_at.desc()).limit(limit))
    ).all()
    if not meetings:
        return []

    # Counted in three grouped queries rather than per meeting, so the list
    # costs four round trips regardless of how many meetings it shows.
    ids = [meeting.id for meeting in meetings]
    commitments = await _count_by(
        session,
        select(Commitment.last_seen_meeting_id, func.count())
        .where(Commitment.last_seen_meeting_id.in_(ids))
        .group_by(Commitment.last_seen_meeting_id),
    )
    decisions = await _count_by(
        session,
        select(Decision.meeting_id, func.count())
        .where(Decision.meeting_id.in_(ids))
        .group_by(Decision.meeting_id),
    )
    open_questions = await _count_by(
        session,
        select(Run.meeting_id, func.count())
        .join(Clarification, Clarification.run_id == Run.id)
        .where(Run.meeting_id.in_(ids), Clarification.status == ClarificationStatus.OPEN)
        .group_by(Run.meeting_id),
    )

    return [
        MeetingSummary(
            id=meeting.id,
            title=meeting.title,
            occurred_at=meeting.occurred_at,
            project=meeting.project,
            participants=meeting.participants,
            commitments=commitments.get(meeting.id, 0),
            decisions=decisions.get(meeting.id, 0),
            open_questions=open_questions.get(meeting.id, 0),
        )
        for meeting in meetings
    ]


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(meeting_id: uuid.UUID, session: SessionDep) -> MeetingDetail:
    meeting = await session.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such meeting.")

    today = today_in(meeting.timezone)
    owners = await _owner_names(session)

    decisions = (
        await session.scalars(select(Decision).where(Decision.meeting_id == meeting_id))
    ).all()
    commitments = (
        await session.scalars(
            select(Commitment).where(Commitment.last_seen_meeting_id == meeting_id)
        )
    ).all()
    rejections = (
        await session.scalars(select(Rejection).where(Rejection.meeting_id == meeting_id))
    ).all()
    communications = (
        await session.scalars(select(Communication).where(Communication.meeting_id == meeting_id))
    ).all()

    return MeetingDetail(
        meeting=MeetingSummary(
            id=meeting.id,
            title=meeting.title,
            occurred_at=meeting.occurred_at,
            project=meeting.project,
            participants=meeting.participants,
            commitments=len(commitments),
            decisions=len(decisions),
            open_questions=0,
        ),
        transcript=meeting.raw_transcript,
        decisions=[
            DecisionOut(
                id=item.id,
                statement=item.statement,
                rationale=item.rationale,
                alternatives_considered=item.alternatives_considered,
                confidence=item.confidence,
                evidence=[EvidenceOut(**e) for e in item.evidence],
                enrichment=item.enrichment,
            )
            for item in decisions
        ],
        commitments=[
            _to_out(item, owners.get(item.owner_id) if item.owner_id else None, today)
            for item in commitments
        ],
        rejections=[
            RejectionOut(
                id=item.id,
                text=str(
                    item.candidate.get("text") or item.candidate.get("statement") or "(unnamed)"
                ),
                rejected_by=item.rejected_by,
                stage=item.stage,
                reason=item.reason,
            )
            for item in rejections
        ],
        communications=[
            CommunicationOut(
                id=item.id,
                kind=item.kind.value,
                subject=item.subject,
                body=item.body,
                status="sent" if item.sent_at else "draft",
                created_at=item.created_at,
            )
            for item in communications
        ],
    )


@router.get("/commitments", response_model=list[CommitmentOut])
async def list_commitments(
    session: SessionDep,
    settings: SettingsDep,
    view: CommitmentFilter = "all",
    limit: Annotated[int, Query(le=500)] = 200,
) -> list[CommitmentOut]:
    """The execution view.

    `at_risk` and `overdue` are filtered after scoring rather than in SQL,
    because risk is computed from today's date and is not a stored fact.
    """
    query = select(Commitment).order_by(Commitment.created_at.desc()).limit(limit)

    if view == "unowned":
        query = query.where(Commitment.owner_id.is_(None))
    elif view == "no_deadline":
        query = query.where(Commitment.due_date.is_(None))
    elif view == "needs_input":
        query = query.where(Commitment.status == CommitmentStatus.NEEDS_CLARIFICATION)

    commitments = (await session.scalars(query)).all()
    owners = await _owner_names(session)
    today = today_in(settings.default_timezone)

    out = [
        _to_out(item, owners.get(item.owner_id) if item.owner_id else None, today)
        for item in commitments
    ]

    if view == "at_risk":
        return [item for item in out if item.risk_band != "low"]
    if view == "overdue":
        return [item for item in out if item.due_date and item.due_date < today]
    return out


@router.get("/commitments/{commitment_id}/timeline")
async def commitment_timeline(commitment_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    """The append-only history of one commitment, across every meeting."""
    commitment = await session.get(Commitment, commitment_id)
    if commitment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such commitment.")

    events = (
        await session.scalars(
            select(CommitmentEvent)
            .where(CommitmentEvent.commitment_id == commitment_id)
            .order_by(CommitmentEvent.created_at)
        )
    ).all()

    return {
        "id": str(commitment.id),
        "text": commitment.text,
        "original_due_date": commitment.original_due_date,
        "due_date": commitment.due_date,
        "slip_count": commitment.slip_count,
        "silence_streak": commitment.silence_streak,
        "events": [
            {
                "type": event.type.value,
                "actor": event.actor,
                "actor_kind": event.actor_kind.value,
                "payload": event.payload,
                "at": event.created_at,
            }
            for event in events
        ],
    }


class PersonSummary(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None
    aliases: list[str]
    open_items: int
    overdue: int
    at_risk: int


class PersonLedger(BaseModel):
    """One person's promises, which is the view the ledger exists to produce.

    Everything else in the console is organised by meeting, and nobody thinks
    about their own work that way. They think "what have I said I would do,
    and what is late".
    """

    person: PersonSummary
    commitments: list[CommitmentOut]
    tasks: list[dict[str, Any]]


@router.get("/people", response_model=list[PersonSummary])
async def list_people(session: SessionDep, settings: SettingsDep) -> list[PersonSummary]:
    people = (await session.scalars(select(Person).order_by(Person.name))).all()
    if not people:
        return []

    commitments = (
        await session.scalars(
            select(Commitment).where(
                Commitment.owner_id.in_([person.id for person in people]),
                Commitment.status.notin_([CommitmentStatus.DONE, CommitmentStatus.DROPPED]),
            )
        )
    ).all()
    today = today_in(settings.default_timezone)

    return [
        PersonSummary(
            id=person.id,
            name=person.name,
            role=person.role,
            aliases=person.aliases,
            open_items=len(owned),
            overdue=len([c for c in owned if c.due_date and c.due_date < today]),
            at_risk=len([c for c in owned if _assess(c, today).band != "low"]),
        )
        for person in people
        for owned in [[c for c in commitments if c.owner_id == person.id]]
    ]


@router.get("/people/{person_id}", response_model=PersonLedger)
async def person_ledger(
    person_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> PersonLedger:
    person = await session.get(Person, person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")

    commitments = (
        await session.scalars(
            select(Commitment)
            .where(Commitment.owner_id == person_id)
            .order_by(Commitment.due_date.is_(None), Commitment.due_date)
        )
    ).all()
    today = today_in(settings.default_timezone)

    # The tasks the Operator actually created for this person, so the page
    # shows what left the system as well as what it decided.
    tasks = (
        await session.scalars(
            select(MockTask)
            .where(MockTask.assignee == person.name)
            .order_by(MockTask.created_at.desc())
        )
    ).all()

    open_items = [
        item
        for item in commitments
        if item.status not in (CommitmentStatus.DONE, CommitmentStatus.DROPPED)
    ]

    return PersonLedger(
        person=PersonSummary(
            id=person.id,
            name=person.name,
            role=person.role,
            aliases=person.aliases,
            open_items=len(open_items),
            overdue=len([c for c in open_items if c.due_date and c.due_date < today]),
            at_risk=len([c for c in open_items if _assess(c, today).band != "low"]),
        ),
        commitments=[_to_out(item, person.name, today) for item in commitments],
        tasks=[
            {
                "external_id": task.external_id,
                "title": task.title,
                "status": task.status.value,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "url": f"/mock/v1/tasks/{task.external_id}",
            }
            for task in tasks
        ],
    )


@router.get("/clarifications", response_model=list[ClarificationOut])
async def list_clarifications(
    session: SessionDep, only_open: bool = True
) -> list[ClarificationOut]:
    query = select(Clarification).order_by(Clarification.created_at.desc())
    if only_open:
        query = query.where(Clarification.status == ClarificationStatus.OPEN)

    clarifications = (await session.scalars(query)).all()
    texts = {
        commitment.id: commitment.text
        for commitment in (
            await session.scalars(
                select(Commitment).where(
                    Commitment.id.in_([c.commitment_id for c in clarifications if c.commitment_id])
                )
            )
        ).all()
    }

    return [
        ClarificationOut(
            id=item.id,
            question=item.question,
            options=item.options,
            evidence=item.evidence,
            status=item.status.value,
            commitment_id=item.commitment_id,
            commitment_text=texts.get(item.commitment_id) if item.commitment_id else None,
            asked_at=item.created_at,
        )
        for item in clarifications
    ]


class RunSummary(BaseModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    meeting_title: str
    status: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    error: str | None
    created_at: datetime
    agents: int
    """Distinct agents that did something, which is the multi-agent claim in
    one number rather than in prose."""


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    session: SessionDep, limit: Annotated[int, Query(le=50)] = 20
) -> list[RunSummary]:
    runs = (await session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))).all()
    if not runs:
        return []

    titles = {
        meeting.id: meeting.title
        for meeting in (
            await session.scalars(
                select(Meeting).where(Meeting.id.in_([run.meeting_id for run in runs]))
            )
        ).all()
    }
    agent_counts: dict[uuid.UUID, int] = dict(
        (
            await session.execute(
                select(AgentTraceEntry.run_id, func.count(func.distinct(AgentTraceEntry.agent)))
                .where(AgentTraceEntry.run_id.in_([run.id for run in runs]))
                .group_by(AgentTraceEntry.run_id)
            )
        ).all()  # type: ignore[arg-type]
    )

    return [
        RunSummary(
            id=run.id,
            meeting_id=run.meeting_id,
            meeting_title=titles.get(run.meeting_id, "Unknown meeting"),
            status=run.status.value,
            cost_usd=run.cost_usd,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            error=run.error,
            created_at=run.created_at,
            agents=agent_counts.get(run.id, 0),
        )
        for run in runs
    ]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: SessionDep) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run.")

    entries = (
        await session.scalars(
            select(AgentTraceEntry)
            .where(AgentTraceEntry.run_id == run_id)
            .order_by(AgentTraceEntry.seq)
        )
    ).all()

    return RunOut(
        id=run.id,
        meeting_id=run.meeting_id,
        status=run.status.value,
        cost_usd=run.cost_usd,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        error=run.error,
        created_at=run.created_at,
        finished_at=run.finished_at,
        trace=[
            TraceOut(
                seq=entry.seq,
                agent=entry.agent,
                event=entry.event,
                provider=entry.provider,
                model=entry.model,
                tokens_in=entry.tokens_in,
                tokens_out=entry.tokens_out,
                latency_ms=entry.latency_ms,
                cost_usd=entry.cost_usd,
                payload=entry.payload,
            )
            for entry in entries
        ],
    )

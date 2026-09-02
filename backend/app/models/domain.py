"""The domain schema.

Two tables exist purely so the system can show its work rather than ask to be
trusted: `rejections` records every candidate an agent threw out and why, and
`agent_trace` records every handoff, tool call, and model call in a run.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    ActorKind,
    Base,
    ClarificationStatus,
    CommitmentKind,
    CommitmentStatus,
    CommunicationKind,
    CreatedAtMixin,
    EventType,
    MentionOutcome,
    RunStatus,
    TaskStatus,
    TimestampMixin,
    uuid_pk,
)

# Gemini's text-embedding-004 output width. Kept in one place because changing
# embedding model means a migration, not an edit.
EMBEDDING_DIMENSIONS = 768


def _enum(python_enum: type, name: str) -> Enum:
    """Store enums as their string values, not their Python member names."""
    return Enum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    people: Mapped[list[Person]] = relationship(back_populates="workspace")


class Person(Base, TimestampMixin):
    """A meeting participant.

    `aliases` carries the nicknames and mis-transcriptions a real transcript
    contains, which is what lets the Attributor resolve "Pri" or "Preeya" to
    the right person instead of inventing a new one.
    """

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    email: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(120))
    team: Mapped[str | None] = mapped_column(String(120))

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="roster")
    """Where this person came from: `roster` if a human put them there,
    `transcript` if the system enrolled them because they spoke in a meeting.

    Kept apart because they are not the same claim. A seeded person is
    verified; a learned one is the system's reading of a speaker label, and
    the console says so rather than presenting the two as equivalent."""

    first_seen_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )

    workspace: Mapped[Workspace] = relationship(back_populates="people")


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"
    __table_args__ = (
        # The same transcript submitted twice is the same meeting. This turns a
        # double-click into a no-op instead of a duplicated ledger.
        UniqueConstraint("workspace_id", "transcript_sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    """IANA zone of the meeting. "Friday" means a different day in Gurugram
    than it does in San Francisco, and Chronos needs to know which."""

    project: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="paste")
    raw_transcript: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    participants: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    turns: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    """Scribe output: speaker-attributed turns with character offsets, which
    every evidence span in this meeting is indexed against."""

    injection_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class Decision(Base, TimestampMixin):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    alternatives_considered: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """Researcher output: a short cited brief on an entity named in the
    decision, plus its source URLs."""


class Commitment(Base, TimestampMixin):
    """A promise, tracked across every meeting it is mentioned in.

    `owner_id` and `due_date` are nullable on purpose. An agent that cannot
    work out who owns something must abstain and raise a clarification, because
    a confidently wrong owner is a silently dropped task.
    """

    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint(
            "owner_confidence >= 0 AND owner_confidence <= 1",
            name="owner_confidence_range",
        ),
        CheckConstraint("due_confidence >= 0 AND due_confidence <= 1", name="due_confidence_range"),
        Index("ix_commitments_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    """Normalised (owner, verb, object) slug. A cheap prefilter that runs
    before the vector search when the Historian looks for a prior match."""

    text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[CommitmentKind] = mapped_column(
        _enum(CommitmentKind, "commitment_kind"), nullable=False
    )
    status: Mapped[CommitmentStatus] = mapped_column(
        _enum(CommitmentStatus, "commitment_status"),
        nullable=False,
        default=CommitmentStatus.EXTRACTED,
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL"), index=True
    )
    owner_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    owner_inference_reason: Mapped[str | None] = mapped_column(Text)

    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    original_due_date: Mapped[date | None] = mapped_column(Date)
    """The first date promised. Slippage is the distance between this and
    `due_date`, which is the whole point of the ledger."""

    due_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_raw_text: Mapped[str | None] = mapped_column(String(255))
    """The words actually spoken ("end of next week"), kept so a human can
    check the resolution rather than trusting it."""

    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    """Per-factor contributions from the deterministic scorer, so the UI can
    say "slipped twice (+0.30)" instead of showing an unexplained number."""

    first_seen_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    last_seen_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    slip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    silence_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Consecutive meetings where this was open and nobody mentioned it.
    Silence predicts failure better than any single missed date."""

    blocked_by: Mapped[str | None] = mapped_column(Text)
    external_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    owner: Mapped[Person | None] = relationship()
    events: Mapped[list[CommitmentEvent]] = relationship(
        back_populates="commitment", order_by="CommitmentEvent.created_at"
    )


class CommitmentEvent(Base, CreatedAtMixin):
    """Append-only audit trail. Never updated, never deleted."""

    __tablename__ = "commitment_events"
    __table_args__ = (Index("ix_events_commitment_created", "commitment_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    commitment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE")
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    """Who did it: an agent name like "historian", or a person's name."""

    actor_kind: Mapped[ActorKind] = mapped_column(_enum(ActorKind, "actor_kind"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    commitment: Mapped[Commitment] = relationship(back_populates="events")


class Rejection(Base, CreatedAtMixin):
    """A candidate an agent threw out, kept with its reason.

    Storing rejections is what lets the console show judgment rather than only
    output, and it is what makes the Skeptic's contribution measurable.
    """

    __tablename__ = "rejections"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    candidate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rejected_by: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    """Where it died: "grounding" (the Verifier) or "review" (the Skeptic)."""

    reason: Mapped[str] = mapped_column(Text, nullable=False)


class Clarification(Base, TimestampMixin):
    """A question an agent could not answer, put to a human."""

    __tablename__ = "clarifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    """Candidates the agent already inferred, so answering is one click rather
    than free-text typing."""

    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ClarificationStatus] = mapped_column(
        _enum(ClarificationStatus, "clarification_status"),
        nullable=False,
        default=ClarificationStatus.OPEN,
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    """LangGraph checkpoint thread. Resuming an interrupted run needs this and
    nothing else, which is why an answer works after a cold start."""

    interrupt_id: Mapped[str | None] = mapped_column(String(64))
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), nullable=False, default=RunStatus.RUNNING
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentTraceEntry(Base, CreatedAtMixin):
    """One line of the run's flight recorder.

    This is what the swim-lane console renders, and what makes the claim "this
    is genuinely multi-agent" checkable rather than decorative.
    """

    __tablename__ = "agent_trace"
    __table_args__ = (Index("ix_trace_run_seq", "run_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    """One of: handoff, model_call, tool_call, artifact, error."""

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(120))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class MockTask(Base, TimestampMixin):
    """Store behind the mock task-management API."""

    __tablename__ = "mock_tasks"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    external_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    """Human-readable reference such as KPT-104, the way a real tracker would."""

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assignee: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.TODO
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    source_commitment_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """Deliberately not a foreign key. This table stands in for Linear or Jira,
    and a real tracker holds our identifier as an opaque reference in a custom
    field. A constraint here would couple the "external" system to our schema
    and quietly make the integration easier than the real one."""
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class Communication(Base, TimestampMixin):
    """A drafted follow-up. Drafted and stored, never actually sent."""

    __tablename__ = "communications"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[CommunicationKind] = mapped_column(
        _enum(CommunicationKind, "communication_kind"), nullable=False
    )
    recipient: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommitmentMention(Base, CreatedAtMixin):
    """How a prior commitment was treated in a later meeting.

    The Historian writes one row per (commitment, meeting) pair, including the
    ones nobody mentioned, because silence is a finding.
    """

    __tablename__ = "commitment_mentions"
    __table_args__ = (UniqueConstraint("commitment_id", "meeting_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    commitment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    outcome: Mapped[MentionOutcome] = mapped_column(
        _enum(MentionOutcome, "mention_outcome"), nullable=False
    )
    reasoning: Mapped[str | None] = mapped_column(Text)
    similarity: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class SearchCacheEntry(Base):
    """Web search results, cached so a rerun of the same meeting costs nothing."""

    __tablename__ = "search_cache"

    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""Declarative base, shared column conventions, and the domain enumerations."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint naming so Alembic can autogenerate reversible migrations.
# Without this, dropping an unnamed constraint later requires hand-written SQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    """For append-only tables, which are written once and never updated."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CommitmentKind(enum.StrEnum):
    """What kind of obligation this is.

    `decision` and the two negative classes (`suggestion`, `discussion`) never
    reach this table: decisions have their own, and the negatives are recorded
    as rejections. See the five-way taxonomy in docs/ARCHITECTURE.md.
    """

    COMMITMENT = "commitment"
    """Someone accepted personal responsibility: "I'll have it by Friday"."""

    ACTION_ITEM = "action_item"
    """Work was assigned and not refused: "Priya, can you own this?" / "Sure"."""


class CommitmentStatus(enum.StrEnum):
    """Lifecycle state that a human or an agent actually set.

    Deliberately excludes `overdue`, `at_risk`, and `slipped`. Those are
    arithmetic over the due date, slip count, and silence streak, so storing
    them would mean a row silently going stale as soon as a day passes.
    """

    EXTRACTED = "extracted"
    NEEDS_CLARIFICATION = "needs_clarification"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"


class EventType(enum.StrEnum):
    """Append-only audit trail vocabulary."""

    CREATED = "created"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RESOLVED = "clarification_resolved"
    OWNER_ASSIGNED = "owner_assigned"
    DEADLINE_SET = "deadline_set"
    DEADLINE_MOVED = "deadline_moved"
    TASK_CREATED = "task_created"
    TASK_FAILED = "task_failed"
    PROGRESSED = "progressed"
    BLOCKED = "blocked"
    SLIPPED = "slipped"
    UNMENTIONED = "unmentioned"
    COMPLETED = "completed"
    DROPPED = "dropped"
    DESCOPED = "descoped"


class ActorKind(enum.StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class ClarificationStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskStatus(enum.StrEnum):
    """Status vocabulary of the mock task-management system."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class CommunicationKind(enum.StrEnum):
    RECAP_EMAIL = "recap_email"
    OWNER_NUDGE = "owner_nudge"
    DIGEST = "digest"


class MentionOutcome(enum.StrEnum):
    """How a prior commitment was treated when it came up again."""

    PROGRESS = "progress"
    COMPLETED = "completed"
    RECOMMITTED = "recommitted"
    BLOCKED = "blocked"
    DESCOPED = "descoped"
    CONTRADICTED = "contradicted"
    UNMENTIONED = "unmentioned"

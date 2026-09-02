"""Answering the questions the agents could not.

This is the other half of ambiguity handling. Detecting that nobody owns a
commitment and raising a precise question is worth nothing on its own; the
value is a human answering it in one click and the ledger being corrected.

Resolution is deliberately *not* a graph resume. The run that raised the
question has already finished and persisted everything it knew. Replaying a
whole multi-agent pipeline to write one owner would spend a run's budget to
apply a fact a human just supplied. Instead the answer is applied directly and
appended to the commitment's history with the human recorded as the actor, so
the audit trail shows exactly which facts came from a person rather than a
model.

That distinction is the point. Every event in the trail carries `actor_kind`,
so "who decided this" is always answerable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import SessionDep
from app.logging import get_logger
from app.models.base import (
    ActorKind,
    ClarificationStatus,
    CommitmentStatus,
    EventType,
)
from app.models.domain import Clarification, Commitment, CommitmentEvent, Person
from app.security.auth import require_demo_key
from app.services.temporal import resolve as resolve_deadline

router = APIRouter(prefix="/clarifications", tags=["clarifications"])
log = get_logger(__name__)


class Answer(BaseModel):
    owner: str | None = Field(
        default=None, description="A roster name. Matched again rather than trusted."
    )
    due_date: str | None = Field(
        default=None, description="An ISO date, or a phrase like 'next Friday'."
    )
    answered_by: str = Field(default="reviewer", max_length=120)
    dismiss: bool = Field(default=False, description="The question does not need answering.")


class Resolution(BaseModel):
    id: uuid.UUID
    status: str
    applied: dict[str, str | None]
    commitment_status: str | None


@router.post(
    "/{clarification_id}/resolve",
    response_model=Resolution,
    dependencies=[Depends(require_demo_key)],
)
async def resolve_clarification(
    clarification_id: uuid.UUID, answer: Answer, session: SessionDep
) -> Resolution:
    """Apply a human's answer to the commitment the question was about."""
    clarification = await session.get(Clarification, clarification_id)
    if clarification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such clarification.")

    if clarification.status is not ClarificationStatus.OPEN:
        # Idempotent rather than an error: a double-click, or a reviewer on two
        # devices, should not produce a 409 over an answer already applied.
        return Resolution(
            id=clarification.id,
            status=clarification.status.value,
            applied=clarification.resolution or {},
            commitment_status=None,
        )

    applied: dict[str, str | None] = {}
    commitment = (
        await session.get(Commitment, clarification.commitment_id)
        if clarification.commitment_id
        else None
    )

    if answer.dismiss:
        clarification.status = ClarificationStatus.ABANDONED
    else:
        if answer.owner and commitment is not None:
            person = await _match_person(session, answer.owner)
            if person is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"'{answer.owner}' is not on the roster.",
                )
            commitment.owner_id = person.id
            # A human said so. Certainty, and recorded as such.
            commitment.owner_confidence = 1.0
            commitment.owner_inference_reason = f"Answered by {answer.answered_by}."
            applied["owner"] = person.name
            _event(
                session,
                commitment.id,
                EventType.OWNER_ASSIGNED,
                answer.answered_by,
                {"owner": person.name, "source": "human"},
            )

        if answer.due_date and commitment is not None:
            resolved = resolve_deadline(answer.due_date, meeting_date=datetime.now(UTC).date())
            if resolved.due is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Could not read '{answer.due_date}' as a date.",
                )
            if commitment.original_due_date is None:
                commitment.original_due_date = commitment.due_date
            commitment.due_date = resolved.due
            commitment.due_confidence = 1.0
            applied["due_date"] = resolved.due.isoformat()
            _event(
                session,
                commitment.id,
                EventType.DEADLINE_SET,
                answer.answered_by,
                {"due_date": resolved.due.isoformat(), "source": "human"},
            )

        clarification.status = ClarificationStatus.RESOLVED

    clarification.resolution = {**applied, "answered_by": answer.answered_by}
    clarification.resolved_at = datetime.now(UTC)

    if commitment is not None:
        still_open = await session.scalar(
            select(Clarification).where(
                Clarification.commitment_id == commitment.id,
                Clarification.status == ClarificationStatus.OPEN,
                Clarification.id != clarification.id,
            )
        )
        # Only confirmed once nothing is outstanding and it has what it needs.
        if still_open is None and commitment.owner_id is not None:
            commitment.status = CommitmentStatus.CONFIRMED

        _event(
            session,
            commitment.id,
            EventType.CLARIFICATION_RESOLVED,
            answer.answered_by,
            {"question": clarification.question[:200], "applied": applied},
        )

    await session.flush()
    log.info("clarification.resolved", id=str(clarification.id), applied=list(applied))

    return Resolution(
        id=clarification.id,
        status=clarification.status.value,
        applied=applied,
        commitment_status=commitment.status.value if commitment else None,
    )


async def _match_person(session: SessionDep, name: str) -> Person | None:
    """Match an answer against the roster rather than trusting the string.

    The same rule the Attributor follows: a name that is not on the roster does
    not become an owner, whoever typed it.
    """
    from app.services.roster import RosterEntry, resolve_owner

    people = list((await session.scalars(select(Person))).all())
    roster = [
        RosterEntry(person.id, person.name, tuple(person.aliases), person.role) for person in people
    ]
    match = resolve_owner(name, roster)
    if match.person_id is None:
        return None
    return next((person for person in people if person.id == match.person_id), None)


def _event(
    session: SessionDep,
    commitment_id: uuid.UUID,
    event: EventType,
    actor: str,
    payload: dict[str, object],
) -> None:
    session.add(
        CommitmentEvent(
            commitment_id=commitment_id,
            type=event,
            actor=actor,
            # The whole reason the trail records actor_kind: which facts came
            # from a person, and which from a model.
            actor_kind=ActorKind.HUMAN,
            payload=payload,
        )
    )

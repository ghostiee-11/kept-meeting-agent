"""The Historian: what happened to the promises from last time.

This is the agent that makes Kept a ledger rather than a summarizer. Everything
else processes one meeting; this one connects them.

Reconciliation runs **per open commitment against the whole new transcript**,
not the other way round. The first version of this module worked the other
way: it took whatever the Analyst had freshly extracted this meeting and tried
to match each one against the ledger. That failed on the case that matters
most. "Legal came back clean, so I shipped it Monday as planned" is a status
report, not a new promise, so the Analyst correctly never turns it into a
commitment, and a completion the ledger most needs to hear about was invisible
before it ever reached the Historian.

Asking, for each open commitment, "does this meeting say anything about this,
and if so what" is what a person reconciling a promise list against a meeting
actually does, and it does not depend on some other agent having re-surfaced
the exact sentence in the right shape first.

Two outputs, and the second matters more:

**Slippage.** A commitment promised again with a new date is a slip. The
original date is preserved, `slip_count` goes up, and the risk score reflects
it. Somebody who has moved the same deadline three times is in different
trouble from somebody who is three days late once.

**Silence.** Commitments that were due and that nobody mentioned at all. Nobody
argues about a promise they have forgotten, which is exactly why forgetting is
the failure mode worth surfacing. No summarizer catches this, because there is
nothing in the transcript to summarise.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent
from app.config import Settings
from app.logging import get_logger
from app.models.base import ActorKind, CommitmentStatus, EventType, MentionOutcome
from app.models.domain import Commitment, CommitmentEvent, CommitmentMention
from app.services import ledger, trace
from app.services.model_router import ModelRouter, Tier
from app.services.temporal import resolve as resolve_deadline

log = get_logger(__name__)

# Outcomes that mean the commitment is finished, one way or another.
_CLOSING = {MentionOutcome.COMPLETED, MentionOutcome.DESCOPED}

# Free-tier models under load intermittently return an empty generation that
# fails schema validation rather than a clean error. Long enough for a
# per-minute token window to roll over before trying again.
_RETRY_PAUSE_SECONDS = 20.0

# One Historian call per open commitment, each carrying the whole transcript.
# A real workspace has a handful of things open at once; this caps the cost on
# one that has accumulated a long tail of stale commitments nobody closed out.
MAX_COMMITMENTS_CHECKED = 12


class MatchVerdict(BaseModel):
    mentioned: bool = Field(
        description="Whether this meeting says anything about this specific commitment."
    )
    outcome: str = Field(
        default="progress",
        description=(
            "Only when mentioned is true: progress, completed, recommitted, "
            "blocked, descoped, or contradicted."
        ),
    )
    new_due_hint: str | None = Field(
        default=None, description="The new deadline as spoken, when recommitted."
    )
    blocker: str | None = Field(default=None, description="What is in the way, when blocked.")
    reasoning: str = Field(description="One sentence a human can check against the transcript.")


@dataclass
class Slippage:
    """What the Historian found, phrased for a human to read."""

    progressed: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    slipped: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    unmentioned: list[str] = field(default_factory=list)
    matched_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        return not any(
            [self.progressed, self.completed, self.slipped, self.blocked, self.unmentioned]
        )

    def summary(self) -> str:
        if self.is_empty:
            return "history: nothing from earlier meetings came up"

        parts = []
        if self.progressed:
            parts.append(f"{len(self.progressed)} progressed")
        if self.completed:
            parts.append(f"{len(self.completed)} completed")
        if self.slipped:
            parts.append(f"{len(self.slipped)} slipped")
        if self.blocked:
            parts.append(f"{len(self.blocked)} blocked")
        if self.unmentioned:
            parts.append(f"{len(self.unmentioned)} went unmentioned and are overdue")
        return "history: " + ", ".join(parts)


async def reconcile(
    session: AsyncSession,
    transcript: str,
    *,
    workspace_id: uuid.UUID,
    meeting_id: uuid.UUID,
    meeting_date: date,
    timezone: str,
    router: ModelRouter,
    settings: Settings,
) -> Slippage:
    """Check every open commitment against this meeting's transcript.

    Ranked by lexical similarity first so the commitments most likely to be
    relevant are the ones checked when a ledger has grown past
    `MAX_COMMITMENTS_CHECKED`; a promise nobody has mentioned in months is also
    the one least likely to come up in today's meeting.
    """
    existing = await ledger.open_commitments(
        session, workspace_id=workspace_id, exclude_meeting_id=meeting_id
    )
    result = Slippage()

    if not existing:
        trace.record("historian", "artifact", payload={"open_commitments": 0})
        return result

    to_check = sorted(existing, key=lambda c: ledger.similarity(c.text, transcript), reverse=True)[
        :MAX_COMMITMENTS_CHECKED
    ]

    agent = build_agent(
        AgentSpec(
            name="historian",
            tier=Tier.REASON,
            purpose="Check open commitments against this meeting's transcript.",
            system_prompt=prompts.HISTORIAN,
            response_format=MatchVerdict,
        ),
        router=router,
        settings=settings,
    )

    for commitment in to_check:
        verdict = await _check_one(agent, commitment, transcript)
        if verdict is None or not verdict.mentioned:
            continue

        _apply(
            session,
            commitment,
            verdict,
            result,
            meeting_id=meeting_id,
            meeting_date=meeting_date,
            timezone=timezone,
        )

    _record_silence(session, existing, result, meeting_id=meeting_id, as_of=meeting_date)

    trace.record(
        "historian",
        "artifact",
        payload={
            "open": len(existing),
            "checked": len(to_check),
            "matched": len(result.matched_ids),
            "slipped": len(result.slipped),
            "unmentioned": len(result.unmentioned),
        },
    )
    return result


async def _check_one(agent: object, commitment: Commitment, transcript: str) -> MatchVerdict | None:
    message = (
        f"Open commitment: {commitment.text}\n"
        f"Owner: {commitment.owner.name if commitment.owner else 'unowned'}\n"
        f"Due: {commitment.due_date or 'no date'}"
        f"{f' (moved {commitment.slip_count}x already)' if commitment.slip_count else ''}\n\n"
        f"This meeting's transcript:\n{transcript}\n\n"
        "Does this meeting say anything about the commitment above? If so, what happened to it?"
    )

    # One retry, not a loop: the same policy extraction uses, for the same
    # reason. A model that cannot produce valid JSON twice is not going to get
    # there on a third attempt, and the budget is better spent elsewhere.
    for attempt in (1, 2):
        try:
            if attempt > 1:
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
            result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("historian.failed", attempt=attempt, error=str(exc)[:200])
            trace.record(
                "historian", "error", payload={"attempt": attempt, "error": str(exc)[:200]}
            )
            continue
        verdict: MatchVerdict | None = result.get("structured_response")
        return verdict
    return None


def _apply(
    session: AsyncSession,
    matched: Commitment,
    verdict: MatchVerdict,
    result: Slippage,
    *,
    meeting_id: uuid.UUID,
    meeting_date: date,
    timezone: str,
) -> None:
    """Update the ledger row, and append to its history."""
    outcome = _outcome_of(verdict.outcome)
    result.matched_ids.add(matched.id)
    matched.last_seen_meeting_id = meeting_id
    # Mentioned at all is enough to reset the silence counter, whatever was said.
    matched.silence_streak = 0

    if outcome is MentionOutcome.RECOMMITTED:
        new_due = resolve_deadline(
            verdict.new_due_hint, meeting_date=meeting_date, timezone=timezone
        )
        matched.slip_count += 1
        if matched.original_due_date is None:
            matched.original_due_date = matched.due_date
        if new_due.due is not None:
            matched.due_date = new_due.due
            matched.due_confidence = new_due.confidence
            matched.due_raw_text = verdict.new_due_hint

        result.slipped.append(
            f"{matched.text} ({matched.owner.name if matched.owner else 'unowned'}) "
            f"moved to {matched.due_date or 'no date'}, slip {matched.slip_count}"
        )
        _event(
            session,
            matched,
            meeting_id,
            EventType.SLIPPED,
            {
                "slip_count": matched.slip_count,
                "new_due": str(matched.due_date),
                "original_due": str(matched.original_due_date),
                "reason": verdict.reasoning,
            },
        )

    elif outcome in _CLOSING:
        matched.status = (
            CommitmentStatus.DONE
            if outcome is MentionOutcome.COMPLETED
            else CommitmentStatus.DROPPED
        )
        result.completed.append(matched.text)
        _event(
            session,
            matched,
            meeting_id,
            EventType.COMPLETED if outcome is MentionOutcome.COMPLETED else EventType.DESCOPED,
            {"reason": verdict.reasoning},
        )

    elif outcome is MentionOutcome.BLOCKED:
        matched.blocked_by = verdict.blocker or matched.blocked_by
        result.blocked.append(f"{matched.text} — {matched.blocked_by}")
        _event(
            session,
            matched,
            meeting_id,
            EventType.BLOCKED,
            {"blocker": matched.blocked_by, "reason": verdict.reasoning},
        )

    else:
        matched.status = CommitmentStatus.IN_PROGRESS
        result.progressed.append(matched.text)
        _event(session, matched, meeting_id, EventType.PROGRESSED, {"reason": verdict.reasoning})

    session.add(
        CommitmentMention(
            commitment_id=matched.id,
            meeting_id=meeting_id,
            outcome=outcome,
            reasoning=verdict.reasoning,
        )
    )


def _record_silence(
    session: AsyncSession,
    existing: list[Commitment],
    result: Slippage,
    *,
    meeting_id: uuid.UUID,
    as_of: date,
) -> None:
    """The finding nobody else produces: promises that went unmentioned."""
    for commitment in ledger.unmentioned(existing, result.matched_ids, as_of=as_of):
        commitment.silence_streak += 1
        days = (as_of - commitment.due_date).days if commitment.due_date else 0
        result.unmentioned.append(
            f"{commitment.text} ({commitment.owner.name if commitment.owner else 'unowned'}) "
            f"— {days} days past due, unmentioned for {commitment.silence_streak}"
        )
        session.add(
            CommitmentMention(
                commitment_id=commitment.id,
                meeting_id=meeting_id,
                outcome=MentionOutcome.UNMENTIONED,
                reasoning=f"Due {commitment.due_date} and not raised in this meeting.",
            )
        )
        _event(
            session,
            commitment,
            meeting_id,
            EventType.UNMENTIONED,
            {"silence_streak": commitment.silence_streak, "days_overdue": days},
        )


def _event(
    session: AsyncSession,
    commitment: Commitment,
    meeting_id: uuid.UUID,
    event: EventType,
    payload: dict[str, object],
) -> None:
    session.add(
        CommitmentEvent(
            commitment_id=commitment.id,
            meeting_id=meeting_id,
            type=event,
            actor="historian",
            actor_kind=ActorKind.AGENT,
            payload=payload,
        )
    )


def _outcome_of(value: str) -> MentionOutcome:
    try:
        return MentionOutcome(value.strip().lower())
    except ValueError:
        return MentionOutcome.PROGRESS

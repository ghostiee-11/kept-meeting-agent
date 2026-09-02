"""The Historian: what happened to the promises from last time.

This is the agent that makes Kept a ledger rather than a summarizer. Everything
else processes one meeting; this one connects them.

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

import uuid
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent
from app.agents.contracts import ExtractedCommitment
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


class MatchVerdict(BaseModel):
    candidate_index: int = Field(
        description="Which shortlisted commitment this is, or -1 for none of them."
    )
    relation: str = Field(description="same, related, or different.")
    outcome: str = Field(
        default="progress",
        description=(
            "Only when relation is same: progress, completed, recommitted, "
            "blocked, descoped, or contradicted."
        ),
    )
    new_due_hint: str | None = Field(
        default=None, description="The new deadline as spoken, when recommitted."
    )
    blocker: str | None = Field(default=None, description="What is in the way, when blocked.")
    reasoning: str = Field(description="One sentence a human can check.")


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
    new_commitments: list[tuple[ExtractedCommitment, str | None]],
    *,
    workspace_id: uuid.UUID,
    meeting_id: uuid.UUID,
    meeting_date: date,
    timezone: str,
    router: ModelRouter,
    settings: Settings,
) -> Slippage:
    """Match this meeting's commitments against the open ledger and update it.

    `new_commitments` pairs each extracted commitment with its resolved owner
    name, because owner is half of what makes two commitments the same one.
    """
    existing = await ledger.open_commitments(
        session, workspace_id=workspace_id, exclude_meeting_id=meeting_id
    )
    result = Slippage()

    if not existing:
        trace.record("historian", "artifact", payload={"open_commitments": 0})
        return result

    agent = build_agent(
        AgentSpec(
            name="historian",
            tier=Tier.REASON,
            purpose="Match commitments against earlier meetings and record what happened.",
            system_prompt=prompts.HISTORIAN,
            response_format=MatchVerdict,
        ),
        router=router,
        settings=settings,
    )

    for commitment, owner in new_commitments:
        candidates = ledger.rank_candidates(commitment.text, owner, existing)
        if not candidates:
            continue

        verdict = await _adjudicate(agent, commitment, owner, candidates)
        if verdict is None or verdict.relation.strip().lower() != "same":
            continue
        if not 0 <= verdict.candidate_index < len(candidates):
            # Models miscount lists, and acting on a bad index would rewrite
            # the history of somebody else's commitment.
            continue

        matched = candidates[verdict.candidate_index].commitment
        if matched.id in result.matched_ids:
            continue

        _apply(
            session,
            matched,
            verdict,
            result,
            meeting_id=meeting_id,
            meeting_date=meeting_date,
            timezone=timezone,
            similarity=candidates[verdict.candidate_index].score,
        )

    _record_silence(session, existing, result, meeting_id=meeting_id, as_of=meeting_date)

    trace.record(
        "historian",
        "artifact",
        payload={
            "open": len(existing),
            "matched": len(result.matched_ids),
            "slipped": len(result.slipped),
            "unmentioned": len(result.unmentioned),
        },
    )
    return result


async def _adjudicate(
    agent: object,
    commitment: ExtractedCommitment,
    owner: str | None,
    candidates: list[ledger.Candidate],
) -> MatchVerdict | None:
    listing = "\n".join(
        f"[{index}] {candidate.commitment.text} "
        f"(due {candidate.commitment.due_date or 'no date'}, "
        f"slipped {candidate.commitment.slip_count}x) — {candidate.reason}"
        for index, candidate in enumerate(candidates)
    )
    message = (
        f"New commitment: {commitment.text}\n"
        f"Owner: {owner or 'unknown'}\n"
        f'Said as: "{commitment.evidence[0].quote}"\n'
        f"Deadline as spoken: {commitment.due_hint or 'none given'}\n\n"
        f"Open commitments it might be:\n{listing}\n\n"
        "Is this one of them, and if so what happened to it?"
    )

    try:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})  # type: ignore[attr-defined]
    except Exception as exc:
        log.warning("historian.failed", error=str(exc)[:200])
        trace.record("historian", "error", payload={"error": str(exc)[:200]})
        return None
    verdict: MatchVerdict | None = result.get("structured_response")
    return verdict


def _apply(
    session: AsyncSession,
    matched: Commitment,
    verdict: MatchVerdict,
    result: Slippage,
    *,
    meeting_id: uuid.UUID,
    meeting_date: date,
    timezone: str,
    similarity: float,
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
            similarity=similarity,
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

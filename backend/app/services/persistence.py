"""Writing a run's results to the database.

Kept out of the graph nodes on purpose. A node that both reasons and persists
cannot be tested without a database, and cannot be reused by the evaluation
harness, which runs the same agents hundreds of times and wants none of it
written down.

Every state change also appends to `commitment_events`, which is never updated
and never deleted. That log is what the timeline renders and what makes the
audit trail real rather than a claim in a README.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import Classification
from app.graph.state import MeetingState, Question, ResolvedItem
from app.logging import get_logger
from app.models.base import (
    ActorKind,
    ClarificationStatus,
    CommitmentKind,
    CommitmentStatus,
    CommunicationKind,
    EventType,
    RunStatus,
)
from app.models.domain import (
    AgentTraceEntry,
    Clarification,
    Commitment,
    CommitmentEvent,
    Communication,
    Decision,
    Meeting,
    Rejection,
    Run,
)
from app.services.trace import RunTrace

log = get_logger(__name__)

_KIND = {
    Classification.COMMITMENT: CommitmentKind.COMMITMENT,
    Classification.ACTION_ITEM: CommitmentKind.ACTION_ITEM,
}


def transcript_digest(transcript: str) -> str:
    return hashlib.sha256(transcript.encode()).hexdigest()


def canonical_key(text: str, owner: str | None) -> str:
    """A cheap, stable identity for a commitment.

    Used as a prefilter before the Historian's vector search. Deliberately
    lossy: it exists to cut the candidate set, not to decide identity.
    """
    words = [word for word in text.lower().split() if len(word) > 3][:6]
    return f"{(owner or 'unowned').lower().replace(' ', '-')}:{'-'.join(sorted(words))}"[:255]


async def upsert_meeting(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    title: str,
    transcript: str,
    occurred_at: datetime,
    timezone: str,
    participants: list[str],
    turns: list[dict[str, Any]],
    project: str | None = None,
) -> tuple[Meeting, bool]:
    """Create the meeting, or return the existing one for the same transcript.

    Keyed on the transcript hash, so submitting the same text twice is a no-op
    rather than a second copy of the ledger. Returns whether it was new.
    """
    digest = transcript_digest(transcript)
    existing = await session.scalar(
        select(Meeting).where(
            Meeting.workspace_id == workspace_id, Meeting.transcript_sha256 == digest
        )
    )
    if existing is not None:
        return existing, False

    meeting = Meeting(
        workspace_id=workspace_id,
        title=title,
        occurred_at=occurred_at,
        timezone=timezone,
        project=project,
        raw_transcript=transcript,
        transcript_sha256=digest,
        participants=participants,
        turns=turns,
    )
    session.add(meeting)
    await session.flush()
    return meeting, True


async def save_run_results(
    session: AsyncSession,
    *,
    run: Run,
    meeting: Meeting,
    state: MeetingState,
    recorder: RunTrace,
) -> dict[str, int]:
    """Persist everything one run produced, and its flight recorder."""
    if await _already_extracted(session, meeting=meeting):
        return await _record_repeat_run(session, run=run, meeting=meeting, recorder=recorder)

    counts = {
        "decisions": 0,
        "commitments": 0,
        "rejections": 0,
        "clarifications": 0,
        "communications": 0,
        "trace": 0,
    }

    enrichments = state.get("enrichments", {})
    for index, decision in enumerate(state.get("decisions", [])):
        enriched = enrichments.get(index)
        session.add(
            Decision(
                meeting_id=meeting.id,
                statement=decision.statement,
                rationale=decision.rationale,
                alternatives_considered=decision.alternatives_considered,
                evidence=[item.model_dump(mode="json") for item in decision.evidence],
                confidence=decision.confidence,
                enrichment=(
                    {"summary": enriched.summary, "citations": enriched.citations}
                    if enriched
                    else None
                ),
            )
        )
        counts["decisions"] += 1

    commitments = await _save_commitments(session, meeting=meeting, items=state.get("items", []))
    counts["commitments"] = len(commitments)

    for rejection in state.get("rejections", []):
        session.add(
            Rejection(
                meeting_id=meeting.id,
                candidate=rejection.candidate,
                rejected_by=rejection.rejected_by,
                stage=rejection.stage,
                reason=rejection.reason,
            )
        )
        counts["rejections"] += 1

    counts["clarifications"] = await _save_questions(
        session, run=run, questions=state.get("questions", []), commitments=commitments
    )
    counts["communications"] = _save_communications(
        session, meeting=meeting, drafts=state.get("communications", {})
    )
    counts["trace"] = _save_trace(session, run=run, recorder=recorder)

    tokens_in, tokens_out = recorder.tokens
    run.cost_usd = round(recorder.cost_usd, 6)
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.status = RunStatus.AWAITING_HUMAN if counts["clarifications"] else RunStatus.SUCCEEDED
    run.finished_at = datetime.now(UTC)

    await session.flush()
    log.info("persistence.saved", run_id=str(run.id), **counts)
    return counts


async def _already_extracted(session: AsyncSession, *, meeting: Meeting) -> bool:
    """Whether an earlier run already wrote this meeting's ledger rows.

    Keyed on rows rather than on whether the meeting record was new, so a run
    that died before it could persist still gets to write on the next attempt.
    """
    return bool(
        await session.scalar(select(Decision.id).where(Decision.meeting_id == meeting.id).limit(1))
        or await session.scalar(
            select(Commitment.id).where(Commitment.first_seen_meeting_id == meeting.id).limit(1)
        )
    )


async def _record_repeat_run(
    session: AsyncSession, *, run: Run, meeting: Meeting, recorder: RunTrace
) -> dict[str, int]:
    """Keep the trace and the cost, keep the ledger as it was.

    Submitting the same transcript twice is a re-run, not a second meeting. The
    agents genuinely ran and that work is recorded, but writing their output
    again would double every commitment and hand the Historian a phantom
    slippage report built out of the workspace's own duplicates. The counts
    returned describe the meeting, not this particular run, because that is
    what the reviewer is actually asking about when they run it again.
    """
    counts = {
        "decisions": await _count(session, Decision.meeting_id == meeting.id, Decision.id),
        "commitments": await _count(
            session, Commitment.first_seen_meeting_id == meeting.id, Commitment.id
        ),
        "rejections": await _count(session, Rejection.meeting_id == meeting.id, Rejection.id),
        "clarifications": 0,
        "communications": await _count(
            session, Communication.meeting_id == meeting.id, Communication.id
        ),
        "trace": _save_trace(session, run=run, recorder=recorder),
    }

    tokens_in, tokens_out = recorder.tokens
    run.cost_usd = round(recorder.cost_usd, 6)
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.status = RunStatus.SUCCEEDED
    run.finished_at = datetime.now(UTC)

    await session.flush()
    log.info("persistence.repeat_run", run_id=str(run.id), meeting_id=str(meeting.id), **counts)
    return counts


async def _count(session: AsyncSession, where: Any, column: Any) -> int:
    return await session.scalar(select(func.count(column)).where(where)) or 0


async def _save_commitments(
    session: AsyncSession, *, meeting: Meeting, items: list[ResolvedItem]
) -> list[Commitment]:
    saved: list[Commitment] = []

    for item in items:
        kind = _KIND.get(item.commitment.classification)
        if kind is None:
            continue

        owner_name = item.attribution.display_name
        commitment = Commitment(
            workspace_id=meeting.workspace_id,
            canonical_key=canonical_key(item.commitment.text, owner_name),
            text=item.commitment.text,
            kind=kind,
            status=(
                CommitmentStatus.NEEDS_CLARIFICATION
                if item.owner_id is None or item.deadline.due is None
                else CommitmentStatus.CONFIRMED
            ),
            owner_id=item.owner_id,
            owner_confidence=item.attribution.confidence,
            owner_inference_reason=item.attribution.reason,
            due_date=item.deadline.due,
            original_due_date=item.deadline.due,
            due_confidence=item.deadline.confidence,
            due_raw_text=item.commitment.due_hint,
            evidence=[e.model_dump(mode="json") for e in item.commitment.evidence],
            risk_score=item.risk.score if item.risk else 0.0,
            risk_factors=item.risk.as_dicts() if item.risk else [],
            first_seen_meeting_id=meeting.id,
            last_seen_meeting_id=meeting.id,
            blocked_by=item.commitment.conditional_on,
            external_task_id=item.external_task_id,
        )
        session.add(commitment)
        await session.flush()
        saved.append(commitment)

        _append_event(
            session,
            commitment_id=commitment.id,
            meeting_id=meeting.id,
            event=EventType.CREATED,
            actor="analyst",
            payload={
                "classification": item.commitment.classification.value,
                "confidence": item.commitment.confidence,
            },
        )
        if item.owner_id is not None:
            _append_event(
                session,
                commitment_id=commitment.id,
                meeting_id=meeting.id,
                event=EventType.OWNER_ASSIGNED,
                actor="attributor",
                payload={"owner": owner_name, "reason": item.attribution.reason},
            )
        if item.deadline.due is not None:
            _append_event(
                session,
                commitment_id=commitment.id,
                meeting_id=meeting.id,
                event=EventType.DEADLINE_SET,
                actor="chronos",
                payload={
                    "due_date": item.deadline.due.isoformat(),
                    "spoken_as": item.deadline.raw,
                    "method": item.deadline.method,
                },
            )

    return saved


async def _save_questions(
    session: AsyncSession,
    *,
    run: Run,
    questions: list[Question],
    commitments: list[Commitment],
) -> int:
    saved = 0
    for question in questions:
        commitment = (
            commitments[question.commitment_index]
            if 0 <= question.commitment_index < len(commitments)
            else None
        )
        session.add(
            Clarification(
                commitment_id=commitment.id if commitment else None,
                run_id=run.id,
                question=question.question,
                options=question.options,
                evidence=question.evidence,
                status=ClarificationStatus.OPEN,
                thread_id=run.thread_id,
            )
        )
        if commitment is not None:
            _append_event(
                session,
                commitment_id=commitment.id,
                meeting_id=run.meeting_id,
                event=EventType.CLARIFICATION_REQUESTED,
                actor=question.field_name,
                payload={"question": question.question},
            )
        saved += 1
    return saved


def _save_communications(session: AsyncSession, *, meeting: Meeting, drafts: dict[str, str]) -> int:
    """The Herald's drafts, stored so they can be shown rather than discarded
    at the end of the run they were written for.

    Drafted and stored, never sent: nothing here dispatches an email. `status`
    stays "draft" until a human explicitly sends it, which this build does
    not do on anyone's behalf.
    """
    if not drafts.get("recap"):
        return 0

    session.add(
        Communication(
            meeting_id=meeting.id,
            kind=CommunicationKind.RECAP_EMAIL,
            subject=drafts.get("recap_subject"),
            body=drafts["recap"],
        )
    )
    return 1


def _save_trace(session: AsyncSession, *, run: Run, recorder: RunTrace) -> int:
    for entry in recorder.entries:
        session.add(
            AgentTraceEntry(
                run_id=run.id,
                seq=entry.seq,
                agent=entry.agent,
                event=entry.event,
                payload=entry.payload,
                provider=entry.provider,
                model=entry.model,
                tokens_in=entry.tokens_in,
                tokens_out=entry.tokens_out,
                latency_ms=entry.latency_ms,
                cost_usd=entry.cost_usd,
            )
        )
    return len(recorder.entries)


def _append_event(
    session: AsyncSession,
    *,
    commitment_id: UUID,
    meeting_id: UUID | None,
    event: EventType,
    actor: str,
    payload: dict[str, Any],
    actor_kind: ActorKind = ActorKind.AGENT,
) -> None:
    """Append to the audit trail. Never updates, never deletes."""
    session.add(
        CommitmentEvent(
            commitment_id=commitment_id,
            meeting_id=meeting_id,
            type=event,
            actor=actor,
            actor_kind=actor_kind,
            payload=payload,
        )
    )

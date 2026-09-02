"""The Execution team: Operator and Herald.

The Operator is the only thing in Kept that writes to the outside world, and it
is deliberately the least clever agent in the system. It takes commitments that
have already been extracted, grounded, reviewed, attributed, and dated, and
creates a task for each. It does not decide what deserves a task, because by
the time work reaches it that has already been decided by agents that could see
the transcript.

That is the injection defence, stated structurally: the agent that reads
untrusted text has no tools, and the agent with tools never reads untrusted
text. An instruction hidden in a transcript has nothing to reach.

Two rules the Operator enforces in code rather than in a prompt:

An unowned commitment never becomes a task. A task assigned to a guess is worse
than no task, because it looks handled.

A commitment yields one task however many times the run is retried, because the
idempotency key is derived from the commitment rather than the attempt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent
from app.config import Settings
from app.graph.state import Question, ResolvedItem
from app.logging import get_logger
from app.services import trace
from app.services.model_router import ModelRouter, Tier
from app.services.task_client import TaskClient, TaskClientError

log = get_logger(__name__)


class Recap(BaseModel):
    subject: str = Field(description="A subject line someone will actually open.")
    body: str = Field(description="The recap. Decisions, then owed work, then what is open.")


class Nudge(BaseModel):
    body: str = Field(description="A short message to one person about their own commitments.")


@dataclass(frozen=True)
class TaskOutcome:
    item: ResolvedItem
    external_id: str | None
    skipped_because: str | None = None
    error: str | None = None


async def create_tasks(
    items: list[ResolvedItem],
    *,
    base_url: str,
    approve_unowned: bool = False,
) -> list[TaskOutcome]:
    """Create one task per owned commitment.

    Unowned commitments are skipped rather than assigned to a best guess. They
    are still tracked and still chased; they simply do not become somebody
    else's to-do until a human says whose they are.
    """
    outcomes: list[TaskOutcome] = []

    async with TaskClient(base_url) as client:
        for item in items:
            if item.owner_id is None and not approve_unowned:
                outcomes.append(TaskOutcome(item, None, skipped_because="nobody owns it yet"))
                continue

            try:
                task = await client.create_task(
                    title=item.commitment.text[:255],
                    commitment_id=_commitment_key(item),
                    description=_describe(item),
                    assignee=item.attribution.display_name,
                    due_date=item.deadline.due,
                )
            except TaskClientError as exc:
                # A tracker being down loses the task, not the commitment. The
                # ledger is the source of truth and the sweep will retry.
                log.warning("operator.task_failed", error=str(exc)[:200])
                trace.record("operator", "error", payload={"error": str(exc)[:200]})
                outcomes.append(TaskOutcome(item, None, error=str(exc)[:200]))
                continue

            trace.record(
                "operator",
                "tool_call",
                payload={"task": task.id, "assignee": task.assignee},
            )
            outcomes.append(TaskOutcome(item, task.id))

    created = sum(1 for outcome in outcomes if outcome.external_id)
    trace.record(
        "operator",
        "artifact",
        payload={
            "created": created,
            "skipped": sum(1 for o in outcomes if o.skipped_because),
            "failed": sum(1 for o in outcomes if o.error),
        },
    )
    return outcomes


def _commitment_key(item: ResolvedItem) -> uuid.UUID:
    """A stable identifier for the idempotency key.

    Derived from the commitment's text and owner rather than generated, so the
    same commitment produces the same key on a retry and the tracker returns
    the original task instead of a duplicate.
    """
    seed = f"{item.commitment.text}|{item.attribution.display_name}"
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def _describe(item: ResolvedItem) -> str:
    """The task body, carrying the evidence back to the tracker.

    Anyone looking at the task can see the sentence that created it without
    coming back here, which is what stops an automated task from feeling like
    it appeared out of nowhere.
    """
    lines = [f'From the meeting: "{item.commitment.evidence[0].quote}"']
    if item.commitment.conditional_on:
        lines.append(f"Conditional on: {item.commitment.conditional_on}")
    if item.deadline.raw:
        lines.append(f'Deadline as spoken: "{item.deadline.raw}"')
    if item.attribution.reason:
        lines.append(f"Owner: {item.attribution.reason}")
    return "\n".join(lines)


async def draft_communications(
    items: list[ResolvedItem],
    decisions: list[str],
    questions: list[Question],
    *,
    meeting_title: str,
    router: ModelRouter,
    settings: Settings,
) -> dict[str, str]:
    """Draft the recap for everyone who was in the meeting.

    Nudges are not written here. Right after a meeting nobody is late yet, and
    a reminder about work agreed to four minutes ago is noise. They are written
    by the sweep, once a deadline has actually passed. See `draft_nudge`.

    Drafted and stored, never sent. Nothing in this system emails anybody: a
    system that writes on your behalf should have to be told to send.
    """
    agent = build_agent(
        AgentSpec(
            name="herald",
            tier=Tier.FAST,
            purpose="Draft the recap and per-owner nudges.",
            system_prompt=prompts.HERALD,
            response_format=Recap,
            redact_pii=True,
        ),
        router=router,
        settings=settings,
    )

    brief = _brief(items, decisions, questions)
    drafts: dict[str, str] = {}

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Meeting: {meeting_title}\n\n{brief}\n\nWrite the recap.",
                    }
                ]
            }
        )
    except Exception as exc:
        log.warning("herald.failed", error=str(exc)[:200])
        trace.record("herald", "error", payload={"error": str(exc)[:200]})
        return drafts

    if recap := result.get("structured_response"):
        drafts["recap_subject"] = recap.subject
        drafts["recap"] = recap.body

    trace.record("herald", "artifact", payload={"drafts": len(drafts)})
    return drafts


async def draft_nudge(
    owner: str,
    lines: list[str],
    *,
    router: ModelRouter,
    settings: Settings,
) -> str | None:
    """One message to one person about work of theirs that is now late.

    Each line is already rendered by the caller from the ledger, so the Herald
    is writing from facts rather than deciding which of them are true. Returns
    None when the draft fails, which the sweep reports rather than papers over:
    a nudge nobody wrote is better than a nudge nobody can check.
    """
    agent = build_agent(
        AgentSpec(
            name="herald",
            tier=Tier.FAST,
            purpose="Nudge one owner about their overdue commitments.",
            system_prompt=prompts.HERALD,
            response_format=Nudge,
            redact_pii=True,
        ),
        router=router,
        settings=settings,
    )

    body = "\n".join(f"  - {line}" for line in lines)
    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{owner} is past the date on the following.\n\n{body}\n\n"
                            "Write the nudge to them."
                        ),
                    }
                ]
            }
        )
    except Exception as exc:
        log.warning("herald.nudge_failed", owner=owner, error=str(exc)[:200])
        trace.record("herald", "error", payload={"owner": owner, "error": str(exc)[:200]})
        return None

    nudge = result.get("structured_response")
    return nudge.body if nudge else None


def _brief(items: list[ResolvedItem], decisions: list[str], questions: list[Question]) -> str:
    """Everything the Herald is allowed to write about, and nothing else."""
    lines: list[str] = []

    if decisions:
        lines.append("Decisions:")
        lines.extend(f"  - {statement}" for statement in decisions)

    if items:
        lines.append("\nCommitments:")
        for item in items:
            owner = item.attribution.display_name or "NOBODY YET"
            due = item.deadline.due.isoformat() if item.deadline.due else "NO DATE"
            lines.append(f"  - {item.commitment.text} | {owner} | {due}")

    if questions:
        lines.append("\nStill open:")
        lines.extend(f"  - {question.question}" for question in questions)

    return "\n".join(lines) if lines else "Nothing was extracted from this meeting."

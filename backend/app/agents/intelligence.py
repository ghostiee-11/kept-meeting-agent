"""The Intelligence team: Scribe, Analyst, Skeptic.

The Analyst is deliberately generous and the Skeptic deliberately harsh, and
they run on different models. Self-critique inside one prompt is weak because
the model is grading text it just committed to; an independent reviewer with
the opposite instruction and a tool to re-read the source is not.

The Analyst has **no tools at all**. It is the only agent that reads raw
transcript text, so giving it none is what makes prompt injection structurally
uninteresting rather than something a prompt has to talk an agent out of.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent, wrap_untrusted
from app.agents.contracts import (
    COMMITTED_CLASSES,
    Classification,
    ExtractedBlocker,
    ExtractedCommitment,
    ExtractedDecision,
    Grounded,
    RejectionRecord,
    ReviewVerdict,
)
from app.config import Settings
from app.logging import get_logger
from app.services import trace
from app.services.ledger import similarity
from app.services.model_router import ModelRouter, Tier
from app.services.segmentation import Turn, turn_texts
from app.services.verifier import verify_evidence

log = get_logger(__name__)

# Long enough for a per-minute token window to roll over.
_RETRY_PAUSE_SECONDS = 20.0


class Brief(StrEnum):
    """The three jobs the Analyst is fanned out across.

    One prompt asked to do all three regresses on all three, and per-brief
    prompts can be evaluated and improved independently.
    """

    DECISIONS = "decisions"
    COMMITMENTS = "commitments"
    BLOCKERS = "blockers"


class DecisionBatch(BaseModel):
    items: list[ExtractedDecision] = Field(default_factory=list)


class CommitmentBatch(BaseModel):
    items: list[ExtractedCommitment] = Field(default_factory=list)


class BlockerBatch(BaseModel):
    items: list[ExtractedBlocker] = Field(default_factory=list)


class ReviewBatch(BaseModel):
    verdicts: list[ReviewVerdict] = Field(default_factory=list)


_BRIEFS: dict[Brief, tuple[str, type[BaseModel]]] = {
    Brief.DECISIONS: (prompts.ANALYST_DECISIONS, DecisionBatch),
    Brief.COMMITMENTS: (prompts.ANALYST_COMMITMENTS, CommitmentBatch),
    Brief.BLOCKERS: (prompts.ANALYST_BLOCKERS, BlockerBatch),
}


@dataclass
class IntelligenceResult:
    decisions: list[ExtractedDecision]
    commitments: list[ExtractedCommitment]
    blockers: list[ExtractedBlocker]
    rejections: list[RejectionRecord]

    failed_briefs: list[str] = field(default_factory=list)
    """Briefs that errored rather than returning nothing.

    The distinction matters more than it looks. Without it, a failed
    extraction and a meeting containing no commitments produce identical
    output, and the supervisor confidently reports an empty meeting when the
    truth is that the work was lost. This is the difference between a system
    that degrades and one that lies.
    """

    @property
    def obligations(self) -> list[ExtractedCommitment]:
        """The two classes that create work."""
        return [item for item in self.commitments if item.classification in COMMITTED_CLASSES]

    @property
    def set_aside(self) -> list[ExtractedCommitment]:
        """Considered and correctly found not to be obligations.

        These are kept rather than discarded, and shown separately from
        rejections. A suggestion correctly identified as a suggestion is the
        taxonomy working, not a mistake, and the difference matters to anyone
        judging whether the system is too eager or too timid.
        """
        return [item for item in self.commitments if item.classification not in COMMITTED_CLASSES]


def analyst_for(brief: Brief) -> AgentSpec:
    system_prompt, response_format = _BRIEFS[brief]
    return AgentSpec(
        name=f"analyst:{brief.value}",
        tier=Tier.REASON,
        purpose=f"Extract {brief.value} from the transcript with verbatim evidence.",
        system_prompt=system_prompt,
        response_format=response_format,
        grounded=True,
        # No tools. This agent reads untrusted text, so there is nothing here
        # for an injected instruction to reach for.
        tools=[],
    )


def transcript_reader(transcript: str, turns: list[Turn]) -> BaseTool:
    """The Skeptic's one tool: re-read the source rather than trust a quote."""

    @tool("read_transcript")
    def read_transcript(from_turn: int = 0, to_turn: int = 20) -> str:
        """Read the transcript between two turn numbers, inclusive of the first.

        Args:
            from_turn: First turn to read, starting at 0.
            to_turn: Last turn to read.
        """
        window = turns[max(0, from_turn) : max(0, to_turn) + 1]
        if not window:
            return f"No turns in that range. The transcript has {len(turns)} turns."
        return "\n".join(
            f"[{turn.index}] {turn.speaker or 'Unknown'}: {turn.text}" for turn in window
        )

    return read_transcript


def skeptic_spec(transcript: str, turns: list[Turn]) -> AgentSpec:
    return AgentSpec(
        name="skeptic",
        tier=Tier.SKEPTIC,
        purpose="Challenge every extracted obligation and reject what does not hold up.",
        system_prompt=prompts.SKEPTIC,
        response_format=ReviewBatch,
        tools=[transcript_reader(transcript, turns)],
        tool_call_limit=8,
    )


async def extract(
    transcript: str,
    turns: list[Turn],
    *,
    router: ModelRouter,
    settings: Settings,
) -> IntelligenceResult:
    """Run the three Analyst briefs concurrently, then ground what comes back.

    Concurrency is the point of splitting the briefs: three focused calls in
    the wall-clock time of the slowest one, instead of one prompt doing three
    jobs badly and sequentially.
    """
    windows = turn_texts(turns)
    state = {"transcript": transcript, "turn_texts": windows}
    limit = asyncio.Semaphore(settings.analyst_concurrency)

    async def run(brief: Brief) -> tuple[Brief, BaseModel | None]:
        """Run one brief, retrying once after a pause.

        The pause matters more than the retry. The usual failure here is a
        per-minute token budget being exhausted by the concurrent briefs, and
        an immediate retry goes straight back into the same exhausted budget.
        Waiting lets the window roll over.

        One retry, not a loop: a second would be spending money on a model that
        has already shown it cannot do this right now.
        """
        agent = build_agent(analyst_for(brief), router=router, settings=settings)
        message = wrap_untrusted(transcript) + f"\n\nExtract the {brief.value}."

        for attempt in (1, 2):
            try:
                async with limit:
                    if attempt > 1:
                        await asyncio.sleep(_RETRY_PAUSE_SECONDS)
                    result = await agent.ainvoke(
                        {"messages": [{"role": "user", "content": message}], **state}
                    )
            except Exception as exc:
                log.warning(
                    "analyst.brief_failed",
                    brief=brief.value,
                    attempt=attempt,
                    error=str(exc)[:300],
                )
                trace.record(
                    f"analyst:{brief.value}",
                    "error",
                    payload={"attempt": attempt, "error": str(exc)[:300]},
                )
                continue
            return brief, result.get("structured_response")

        return brief, None

    outcomes = dict(
        await asyncio.gather(run(Brief.DECISIONS), run(Brief.COMMITMENTS), run(Brief.BLOCKERS))
    )
    decisions = outcomes[Brief.DECISIONS]
    commitments = outcomes[Brief.COMMITMENTS]
    blockers = outcomes[Brief.BLOCKERS]
    failed = [brief.value for brief, result in outcomes.items() if result is None]

    rejections: list[RejectionRecord] = []
    grounded_commitments = _ground(
        commitments, transcript, windows, rejections, "commitments", ExtractedCommitment
    )

    return IntelligenceResult(
        decisions=_ground(
            decisions, transcript, windows, rejections, "decisions", ExtractedDecision
        ),
        # A statement the commitments brief classed as a decision belongs to the
        # decisions brief. Keeping both would list the same sentence twice.
        commitments=[
            item
            for item in grounded_commitments
            if item.classification is not Classification.DECISION
        ],
        blockers=_ground(blockers, transcript, windows, rejections, "blockers", ExtractedBlocker),
        rejections=rejections,
        failed_briefs=failed,
    )


def _ground[GroundedT: Grounded](
    batch: Any,
    transcript: str,
    windows: list[str],
    rejections: list[RejectionRecord],
    label: str,
    _kind: type[GroundedT],
) -> list[GroundedT]:
    """Keep only items whose quotes are genuinely in the transcript.

    The grounding middleware already gave the model one chance to correct
    itself. Anything still ungrounded here is recorded as a rejection rather
    than dropped silently, because a reviewer should be able to see what the
    system threw away and why.
    """
    if batch is None:
        return []

    kept: list[GroundedT] = []
    for item in getattr(batch, "items", []):
        grounded, reasons = verify_evidence(transcript, item.evidence, turns=windows)
        if not grounded:
            rejections.append(
                RejectionRecord(
                    candidate=item.model_dump(mode="json"),
                    rejected_by="verifier",
                    stage="grounding",
                    reason=reasons[0] if reasons else "No evidence survived verification.",
                )
            )
            continue

        item = item.model_copy(update={"evidence": grounded})
        twin = _duplicate_of(item, kept)
        if twin is not None:
            rejections.append(
                RejectionRecord(
                    candidate=item.model_dump(mode="json"),
                    rejected_by="verifier",
                    stage="dedupe",
                    reason=f'Same sentence, already recorded as "{_body(twin)}".',
                )
            )
            continue
        kept.append(item)

    trace.record(
        f"analyst:{label}",
        "artifact",
        payload={"kept": len(kept), "rejected": len(rejections)},
    )
    return kept


def _body(item: Grounded) -> str:
    """The item's own sentence, whichever field carries it."""
    for name in ("statement", "text", "description"):
        value = getattr(item, name, None)
        if isinstance(value, str):
            return value
    return ""


# Two items are the same thing when they cite the same sentence and say roughly
# the same thing about it. The span alone is not enough: "I'll write the runbook
# and send Priya the credentials" is one sentence and two commitments.
#
# The threshold sits in a wide gap. Restatements of one decision measured 0.50
# to 0.59; genuinely different promises sharing a sentence measured 0.11 to
# 0.31. Anything in between is rare enough to be worth keeping both rows.
_SAME_THING = 0.45


def _duplicate_of[GroundedT: Grounded](item: GroundedT, kept: list[GroundedT]) -> GroundedT | None:
    """A near-twin already kept, or None.

    The Analyst occasionally restates one sentence twice inside a single brief,
    which reaches the reviewer as two rows that mean the same thing. Cheaper to
    catch here, once, than to ask the model to be more careful.
    """
    spans = {(evidence.start, evidence.end) for evidence in item.evidence}
    for other in kept:
        shares_span = spans & {(evidence.start, evidence.end) for evidence in other.evidence}
        if shares_span and similarity(_body(item), _body(other)) >= _SAME_THING:
            return other
    return None


async def review(
    candidates: list[ExtractedCommitment],
    transcript: str,
    turns: list[Turn],
    *,
    router: ModelRouter,
    settings: Settings,
) -> tuple[list[ExtractedCommitment], list[RejectionRecord]]:
    """Have the Skeptic challenge each candidate; apply its verdicts.

    A verdict that names an index we did not send is ignored rather than
    trusted. Models miscount lists, and acting on a bad index would reject
    somebody else's commitment.
    """
    if not candidates:
        return [], []

    agent = build_agent(skeptic_spec(transcript, turns), router=router, settings=settings)
    listing = "\n".join(
        f"[{index}] class={item.classification.value} owner={item.owner_hint or 'unknown'} "
        f"due={item.due_hint or 'none'}\n"
        f"     text: {item.text}\n"
        f"     quote: {item.evidence[0].quote!r}"
        for index, item in enumerate(candidates)
    )

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{len(candidates)} candidates were extracted from a meeting. "
                            "Return one verdict for each.\n\n"
                            f"{listing}"
                        ),
                    }
                ],
                "transcript": transcript,
            }
        )
    except Exception as exc:
        # Losing review is a quality regression, not a run failure. Better to
        # ship unreviewed candidates than nothing, and the trace records it.
        log.warning("skeptic.failed", error=str(exc)[:300])
        trace.record("skeptic", "error", payload={"error": str(exc)[:300]})
        return candidates, []

    verdicts = getattr(result.get("structured_response"), "verdicts", [])
    return _apply_verdicts(candidates, verdicts)


def _apply_verdicts(
    candidates: list[ExtractedCommitment], verdicts: list[ReviewVerdict]
) -> tuple[list[ExtractedCommitment], list[RejectionRecord]]:
    by_index = {v.index: v for v in verdicts if 0 <= v.index < len(candidates)}
    kept: list[ExtractedCommitment] = []
    rejections: list[RejectionRecord] = []

    for index, item in enumerate(candidates):
        verdict = by_index.get(index)
        if verdict is None:
            # No verdict means no objection. Silence must not delete work.
            kept.append(item)
            continue

        decision = verdict.verdict.strip().lower()
        if decision == "reject":
            rejections.append(
                RejectionRecord(
                    candidate=item.model_dump(mode="json"),
                    rejected_by="skeptic",
                    stage="review",
                    reason=verdict.reason,
                )
            )
            continue

        if decision == "downgrade" and verdict.reclassify_to is not None:
            kept.append(
                item.model_copy(
                    update={
                        "classification": verdict.reclassify_to,
                        "reasoning": f"{item.reasoning} Reviewed: {verdict.reason}",
                    }
                )
            )
            continue

        kept.append(item)

    trace.record(
        "skeptic",
        "artifact",
        payload={
            "reviewed": len(candidates),
            "rejected": len(rejections),
            "downgraded": sum(
                1 for v in by_index.values() if v.verdict.strip().lower() == "downgrade"
            ),
        },
    )
    return kept, rejections

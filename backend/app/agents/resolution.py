"""The Resolution team: Attributor, Chronos, Researcher.

Each follows the same shape. A deterministic pass handles what has an exact
answer, and only the residue reaches a model. That is not just a cost saving:
matching a name against a roster is something code does perfectly and a model
does approximately, so sending everything to the model would make the common
case worse as well as slower.

What survives both passes unresolved becomes a clarification for a human. That
is the intended ending, not a failure. A commitment with no owner and an honest
question attached is worth more than one with a guessed owner and no question.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent
from app.agents.contracts import ExtractedCommitment, ExtractedDecision
from app.config import Settings
from app.logging import get_logger
from app.services import trace
from app.services.model_router import ModelRouter, Tier
from app.services.roster import Attribution, RosterEntry, resolve_owner
from app.services.search import SearchService
from app.services.segmentation import Turn
from app.services.temporal import Resolved, resolve

log = get_logger(__name__)

# Windows of context handed to the Attributor around the turn in question.
_CONTEXT_TURNS_BEFORE = 4
_CONTEXT_TURNS_AFTER = 2


class OwnerVerdict(BaseModel):
    person_name: str | None = Field(
        default=None,
        description=(
            "The roster name of the owner, copied exactly. Null if you cannot "
            "tell, or if two people are equally plausible."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="What in the turns told you this.")
    candidates: list[str] = Field(
        default_factory=list,
        description="Roster names that are plausible. Fill this in when abstaining.",
    )


class DeadlineVerdict(BaseModel):
    due_date: date | None = Field(
        default=None, description="ISO date, or null when nothing fixes it."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="What fixes this date. Name it explicitly.")
    source_url: str | None = Field(default=None, description="Where an external date came from.")


class Enrichment(BaseModel):
    summary: str = Field(description="Two or three sentences, or empty if none is needed.")
    citations: list[str] = Field(default_factory=list)


@dataclass
class ResolvedCommitment:
    """A commitment after the Resolution team has been over it."""

    commitment: ExtractedCommitment
    attribution: Attribution
    deadline: Resolved
    questions: list[str] = field(default_factory=list)

    @property
    def owner_id(self) -> UUID | None:
        return self.attribution.person_id

    @property
    def needs_clarification(self) -> bool:
        return bool(self.questions)


def _context_around(turns: list[Turn], index: int | None) -> str:
    if index is None:
        return "\n".join(f"[{t.index}] {t.speaker or 'Unknown'}: {t.text}" for t in turns[:12])
    window = turns[max(0, index - _CONTEXT_TURNS_BEFORE) : index + _CONTEXT_TURNS_AFTER + 1]
    return "\n".join(f"[{t.index}] {t.speaker or 'Unknown'}: {t.text}" for t in window)


async def resolve_owners(
    commitments: list[ExtractedCommitment],
    roster: list[RosterEntry],
    turns: list[Turn],
    *,
    router: ModelRouter,
    settings: Settings,
) -> list[tuple[ExtractedCommitment, Attribution]]:
    """Match owners deterministically, then send only the residue to the Attributor."""
    speaker_of = {turn.index: turn.speaker for turn in turns}

    def speaker_for(item: ExtractedCommitment) -> str | None:
        """Who said the line this item was extracted from.

        This is what makes first-person resolution exact: "I'll take it" said
        by Priya is Priya, no reasoning required.
        """
        if not item.evidence or item.evidence[0].turn_index is None:
            return None
        return speaker_of.get(item.evidence[0].turn_index)

    attributions = [
        resolve_owner(item.owner_hint, roster, speaker=speaker_for(item)) for item in commitments
    ]

    residue = [index for index, a in enumerate(attributions) if a.needs_agent]
    trace.record(
        "attributor",
        "artifact",
        payload={"matched": len(commitments) - len(residue), "escalated": len(residue)},
    )
    if not residue:
        return list(zip(commitments, attributions, strict=True))

    agent = build_agent(
        AgentSpec(
            name="attributor",
            tier=Tier.REASON,
            purpose="Resolve owners a name lookup could not.",
            system_prompt=prompts.ATTRIBUTOR,
            response_format=OwnerVerdict,
        ),
        router=router,
        settings=settings,
    )
    roster_text = "\n".join(
        f"- {person.name}"
        + (f" (also called {', '.join(person.aliases)})" if person.aliases else "")
        + (f", {person.role}" if person.role else "")
        for person in roster
    )

    async def ask(index: int) -> tuple[int, OwnerVerdict | None]:
        item = commitments[index]
        turn_index = item.evidence[0].turn_index if item.evidence else None
        message = (
            f"Roster:\n{roster_text}\n\n"
            f"Turns around the commitment:\n{_context_around(turns, turn_index)}\n\n"
            f"Commitment: {item.text}\n"
            f'Owner as spoken: "{item.owner_hint}"\n'
            f"Why matching could not settle it: {attributions[index].reason}\n\n"
            "Who owns this?"
        )
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})
        except Exception as exc:
            log.warning("attributor.failed", error=str(exc)[:200])
            return index, None
        return index, result.get("structured_response")

    for index, verdict in await asyncio.gather(*(ask(i) for i in residue)):
        if verdict is None:
            continue
        attributions[index] = _attribution_from(verdict, roster, commitments[index])

    return list(zip(commitments, attributions, strict=True))


def _attribution_from(
    verdict: OwnerVerdict, roster: list[RosterEntry], item: ExtractedCommitment
) -> Attribution:
    """Turn the agent's answer back into an attribution, refusing to invent people.

    The agent returns a name as text. It is matched against the roster again
    rather than trusted, so a hallucinated colleague cannot become an owner.
    """
    if not verdict.person_name:
        candidates = ", ".join(verdict.candidates) if verdict.candidates else "nobody obvious"
        return Attribution(
            None,
            None,
            0.0,
            f"{verdict.reasoning} Candidates: {candidates}.",
            "ambiguous",
        )

    confirmed = resolve_owner(verdict.person_name, roster)
    if confirmed.person_id is None:
        return Attribution(
            None,
            verdict.person_name,
            0.0,
            f'The Attributor proposed "{verdict.person_name}", who is not on the roster.',
            "unmatched",
        )

    return Attribution(
        confirmed.person_id,
        confirmed.display_name,
        min(verdict.confidence, confirmed.confidence),
        verdict.reasoning,
        "attributor",
    )


async def resolve_deadlines(
    commitments: list[ExtractedCommitment],
    *,
    meeting_date: date,
    timezone: str,
    router: ModelRouter,
    settings: Settings,
    search: SearchService | None = None,
) -> list[Resolved]:
    """Resolve deadlines by arithmetic, then send only the residue to Chronos."""
    resolved = [
        resolve(item.due_hint, meeting_date=meeting_date, timezone=timezone) for item in commitments
    ]

    residue = [
        index for index, r in enumerate(resolved) if r.needs_help and commitments[index].due_hint
    ]
    trace.record(
        "chronos",
        "artifact",
        payload={"computed": len(resolved) - len(residue), "escalated": len(residue)},
    )
    if not residue:
        return resolved

    agent = build_agent(
        AgentSpec(
            name="chronos",
            tier=Tier.REASON,
            purpose="Resolve deadlines that calendar arithmetic could not.",
            system_prompt=prompts.CHRONOS,
            response_format=DeadlineVerdict,
        ),
        router=router,
        settings=settings,
    )

    async def ask(index: int) -> tuple[int, DeadlineVerdict | None]:
        phrase = commitments[index].due_hint or ""
        context = ""
        if search is not None and search.available:
            found = await search.search(f"{phrase} {meeting_date.year} date")
            context = f"\n\nWeb results:\n{found.as_context()}"

        message = (
            f"The meeting was on {meeting_date.isoformat()} ({timezone}).\n"
            f"Commitment: {commitments[index].text}\n"
            f'Deadline as spoken: "{phrase}"{context}\n\n'
            "What is the due date? Return null if nothing fixes it."
        )
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})
        except Exception as exc:
            log.warning("chronos.failed", error=str(exc)[:200])
            return index, None
        return index, result.get("structured_response")

    for index, verdict in await asyncio.gather(*(ask(i) for i in residue)):
        if verdict is None or verdict.due_date is None:
            continue
        resolved[index] = Resolved(
            verdict.due_date,
            verdict.confidence,
            "chronos",
            commitments[index].due_hint or "",
        )

    return resolved


async def enrich_decisions(
    decisions: list[ExtractedDecision],
    *,
    router: ModelRouter,
    settings: Settings,
    search: SearchService,
) -> dict[int, Enrichment]:
    """Add cited context to decisions that named something unexplained.

    Only decisions the Analyst flagged are researched, and only while the
    search budget lasts. Enrichment is the first thing to give up when the
    budget is tight, because a meeting is still processed without it.
    """
    wanted = [index for index, item in enumerate(decisions) if item.needs_external_context]
    if not wanted or not search.available:
        return {}

    agent = build_agent(
        AgentSpec(
            name="researcher",
            tier=Tier.REASON,
            purpose="Add cited external context to a decision.",
            system_prompt=prompts.RESEARCHER,
            response_format=Enrichment,
            redact_pii=True,
            tool_call_limit=4,
        ),
        router=router,
        settings=settings,
    )

    enrichments: dict[int, Enrichment] = {}
    for index in wanted:
        if search.budget_remaining == 0:
            log.info("researcher.budget_exhausted", remaining=len(wanted) - len(enrichments))
            break

        decision = decisions[index]
        found = await search.search(decision.statement)
        message = (
            f"Decision: {decision.statement}\n"
            f"Said in the meeting: {decision.evidence[0].quote}\n\n"
            f"Web results:\n{found.as_context()}\n\n"
            "Write the context a reader who was not in the room would need."
        )
        try:
            result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})
        except Exception as exc:
            log.warning("researcher.failed", error=str(exc)[:200])
            continue

        enrichment = result.get("structured_response")
        if enrichment and enrichment.summary.strip():
            # Citations come from the search result, not from the model, so a
            # plausible-looking URL cannot be invented alongside a fact.
            enrichments[index] = Enrichment(
                summary=enrichment.summary, citations=found.citations[:3]
            )

    trace.record("researcher", "artifact", payload={"enriched": len(enrichments)})
    return enrichments

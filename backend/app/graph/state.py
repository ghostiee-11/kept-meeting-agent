"""The blackboard every agent reads from and writes to.

Only typed artifacts live here. An agent's own reasoning does not: each one
receives a written brief and hands back a contract, so cost grows with the
number of agents rather than with its square, and nobody has to read anybody
else's half-finished thinking.

`progress` is what the Chief of Staff routes on. Without a record of what has
already been done, a supervisor re-delegates work it has already delegated,
which is the classic way a supervised team burns its budget going in circles.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.graph import MessagesState

from app.agents.contracts import (
    ExtractedBlocker,
    ExtractedCommitment,
    ExtractedDecision,
    RejectionRecord,
)
from app.agents.resolution import Enrichment
from app.services.risk import RiskAssessment
from app.services.roster import Attribution, RosterEntry
from app.services.temporal import Resolved


@dataclass
class Question:
    """Something an agent could not settle, phrased for a human to answer."""

    subject: str
    """The commitment text, so the question makes sense on its own."""

    question: str
    options: list[dict[str, Any]] = field(default_factory=list)
    """Candidates already inferred, so answering is a click and not an essay."""

    evidence: list[dict[str, Any]] = field(default_factory=list)
    field_name: str = "owner"
    commitment_index: int = -1

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "question": self.question,
            "options": self.options,
            "evidence": self.evidence,
            "field": self.field_name,
            "commitment_index": self.commitment_index,
        }


@dataclass
class ResolvedItem:
    """One obligation after every team has been over it."""

    commitment: ExtractedCommitment
    attribution: Attribution
    deadline: Resolved
    risk: RiskAssessment | None = None
    external_task_id: str | None = None

    @property
    def owner_id(self) -> UUID | None:
        return self.attribution.person_id


class MeetingState(MessagesState):
    """Shared state for one meeting run."""

    # ---- Input, set once -----------------------------------------------
    meeting_id: str
    run_id: str
    transcript: str
    turn_texts: list[str]
    """Windows the Verifier searches when repairing a near-miss quote. Named
    to match what GroundingMiddleware reads out of agent state."""

    turns: list[Any]
    roster: list[RosterEntry]
    meeting_date: date
    timezone: str

    # ---- Supervisor bookkeeping ----------------------------------------
    progress: Annotated[list[str], operator.add]
    current_brief: str
    current_agent: str
    final_summary: str
    replans: int
    """How many reruns have actually happened. Bounded by MAX_REPLANS."""

    replan_wanted: bool
    """Set by a team that reports its own output is untrustworthy, and cleared
    by the rerun. Kept separate from the counter because gating a rerun on the
    counter alone blocks the retry it is meant to permit: the run that asks for
    the retry is the run that increments it."""

    # ---- Artifacts ------------------------------------------------------
    decisions: list[ExtractedDecision]
    blockers: list[ExtractedBlocker]
    items: list[ResolvedItem]
    set_aside: list[ExtractedCommitment]
    rejections: Annotated[list[RejectionRecord], operator.add]
    enrichments: dict[int, Enrichment]
    questions: list[Question]
    communications: dict[str, str]


class TeamReport(TypedDict):
    """What a team node hands back to the Chief.

    Deliberately small. The Chief routes on outcomes, not on transcripts of
    how each team reached them.
    """

    team: str
    summary: str
    counts: dict[str, int]

"""Typed artifacts agents produce and hand to each other.

One rule shapes every schema here: **models describe language, code does
arithmetic**. An agent is asked for the words it saw, never for a character
offset, because asking a language model to count characters is asking it to do
the one thing it is worst at. The Verifier finds the offsets afterwards, which
is both exact and free.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Classification(StrEnum):
    """The five-way taxonomy.

    The two negatives matter as much as the three positives: a system that
    cannot say "that was only a suggestion" invents work nobody agreed to.
    """

    DECISION = "decision"
    """A choice was settled: "OK, we're going with Postgres"."""

    COMMITMENT = "commitment"
    """A named person accepted personal responsibility: "I'll have it Friday"."""

    ACTION_ITEM = "action_item"
    """Work assigned and not refused: "Priya, can you own this?" / "Sure"."""

    SUGGESTION = "suggestion"
    """Proposed, never accepted: "someone should look at caching"."""

    DISCUSSION = "discussion"
    """Context with no obligation attached."""


COMMITTED_CLASSES = frozenset({Classification.COMMITMENT, Classification.ACTION_ITEM})


class Evidence(BaseModel):
    """A verbatim span of the transcript that justifies an extracted item.

    `quote` and `speaker` come from the model. `start`, `end`, and `turn_index`
    are filled in by the Verifier, and an item whose quote cannot be located in
    the source is rejected before it reaches the database.
    """

    quote: str = Field(
        description=(
            "The exact words from the transcript that justify this item. Copy "
            "them character for character. Do not paraphrase, summarise, or "
            "join separated lines."
        ),
        min_length=1,
    )
    speaker: str | None = Field(
        default=None, description="Who said it, exactly as the transcript labels them."
    )

    start: int | None = Field(default=None, description="Filled by the Verifier.")
    end: int | None = Field(default=None, description="Filled by the Verifier.")
    turn_index: int | None = Field(default=None, description="Filled by the Verifier.")
    match: str | None = Field(
        default=None,
        description="How the Verifier located it: exact, normalized, or repaired.",
    )


class Grounded(BaseModel):
    """Base for anything that must cite the transcript."""

    evidence: list[Evidence] = Field(
        min_length=1, description="At least one verbatim quote. No quote, no item."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0 to 1.")


class ExtractedDecision(Grounded):
    statement: str = Field(description="The decision, stated as a settled choice.")
    rationale: str | None = Field(default=None, description="Why, only if the transcript says why.")
    alternatives_considered: list[str] = Field(
        default_factory=list, description="Options explicitly weighed and not chosen."
    )
    needs_external_context: bool = Field(
        default=False,
        description=(
            "True when the decision names a vendor, tool, standard, or "
            "regulation whose meaning is not explained in the transcript."
        ),
    )


class ExtractedCommitment(Grounded):
    text: str = Field(
        description=(
            "The task, in one sentence. What will be done, and to what. Someone "
            "reading only this line three weeks later must know what to do. Not "
            'the words of acceptance: "Own the vendor call with Vanta", never '
            '"Sure, I\'ll take it".'
        )
    )
    classification: Classification = Field(description="Which of the five classes this is.")
    reasoning: str = Field(
        description="One sentence on why this class and not the neighbouring one."
    )

    owner_hint: str | None = Field(
        default=None,
        description=(
            "The name or pronoun the transcript uses for whoever owns this. "
            "Leave null if nobody accepted it. Never guess a name."
        ),
    )
    due_hint: str | None = Field(
        default=None,
        description=(
            'The deadline exactly as spoken, such as "end of next week" or '
            '"before the Diwali break". Leave null if none was given.'
        ),
    )
    conditional_on: str | None = Field(
        default=None,
        description='Any precondition, such as "if legal signs off".',
    )
    is_retracted: bool = Field(
        default=False,
        description="True if the speaker later walked this back in the same meeting.",
    )


class ExtractedBlocker(Grounded):
    description: str = Field(description="What is in the way.")
    blocks: str | None = Field(default=None, description="Which work it holds up.")
    raised_by: str | None = None


class ReviewVerdict(BaseModel):
    """The Skeptic's judgment on one candidate."""

    index: int = Field(description="Position of the candidate in the list under review.")
    verdict: str = Field(description="keep, downgrade, or reject.")
    reclassify_to: Classification | None = Field(
        default=None, description="Required when the verdict is downgrade."
    )
    reason: str = Field(description="One sentence a human can check against the transcript.")


class RejectionRecord(BaseModel):
    """A candidate that did not survive, kept so the console can show judgment."""

    candidate: dict[str, object]
    rejected_by: str
    stage: str
    reason: str

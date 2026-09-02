"""The commitment ledger: finding what a new meeting is talking about.

A commitment mentioned again in a later meeting is the same commitment, not a
new one. Getting that right is what turns a pile of per-meeting extractions
into a ledger you can hold someone to.

Three stages, cheapest first, because the expensive one should only see
plausible candidates:

1. **Canonical key.** A normalised (owner, content words) slug. Exact matches
   here are almost always the same commitment and cost nothing to find.
2. **Similarity.** Ranks the remaining open commitments. Uses pgvector when an
   embedding provider is configured and lexical overlap when one is not, behind
   the same interface, because the deployment this runs on has neither Gemini
   nor OpenAI credentials and degrading is better than not shipping the feature.
3. **Adjudication.** An agent decides whether the top candidates are actually
   the same promise. This is the stage that carries the semantic weight, which
   is why stage 2 being lexical is a cost rather than a correctness problem.

**Silence is a finding.** The most useful thing this module does is notice
commitments that were due and that nobody mentioned at all. A missed date gets
discussed; a forgotten promise does not, and that is exactly why it is the one
that fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging import get_logger
from app.models.base import CommitmentStatus
from app.models.domain import Commitment

log = get_logger(__name__)

# Below this, two commitments are about different things and not worth a model
# call. Deliberately generous: adjudication is what actually decides, and a
# missed match is a duplicate in the ledger, which is worse than a wasted call.
SIMILARITY_FLOOR = 0.35
MAX_CANDIDATES = 4

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "for",
        "by",
        "on",
        "of",
        "and",
        "with",
        "up",
        "get",
        "it",
        "that",
        "this",
        "will",
        "be",
        "is",
        "are",
        "in",
        "at",
        "have",
        "has",
        "do",
        "does",
        "our",
        "we",
        "i",
        "you",
    }
)


def content_words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in _STOPWORDS and len(word) > 2
    }


def canonical_key(text: str, owner: str | None) -> str:
    """A stable, lossy identity used only to cut the candidate set."""
    words = sorted(content_words(text))[:6]
    return f"{(owner or 'unowned').lower().replace(' ', '-')}:{'-'.join(words)}"[:255]


def similarity(left: str, right: str) -> float:
    """How alike two commitments are, without an embedding model.

    Jaccard over content words, averaged with a sequence ratio. Word overlap
    catches "send the migration plan" against "get the migration plan to Meera";
    the sequence ratio stops two commitments that merely share common words from
    scoring highly.
    """
    left_words, right_words = content_words(left), content_words(right)
    if not left_words or not right_words:
        return 0.0

    overlap = len(left_words & right_words) / len(left_words | right_words)
    sequence = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    return round((overlap * 2 + sequence) / 3, 3)


@dataclass(frozen=True)
class Candidate:
    commitment: Commitment
    score: float
    reason: str


async def open_commitments(
    session: AsyncSession, *, workspace_id: UUID, exclude_meeting_id: UUID | None = None
) -> list[Commitment]:
    """Everything still outstanding in this workspace."""
    # The owner is eager-loaded because the Historian names people in its
    # findings. A lazy relationship access inside async code raises
    # MissingGreenlet, which surfaces as the whole node failing rather than as
    # anything resembling a missing join.
    query = (
        select(Commitment)
        .options(selectinload(Commitment.owner))
        .where(
            Commitment.workspace_id == workspace_id,
            Commitment.status.notin_([CommitmentStatus.DONE, CommitmentStatus.DROPPED]),
        )
    )
    if exclude_meeting_id is not None:
        query = query.where(Commitment.last_seen_meeting_id != exclude_meeting_id)
    return list((await session.scalars(query)).all())


def rank_candidates(text: str, owner: str | None, existing: list[Commitment]) -> list[Candidate]:
    """Rank open commitments against a new one, best first.

    An exact canonical-key match is promoted to the front regardless of its
    similarity score, because the same person restating the same work in
    different words is precisely the case worth catching.
    """
    key = canonical_key(text, owner)
    scored: list[Candidate] = []

    for commitment in existing:
        if commitment.canonical_key == key:
            scored.append(Candidate(commitment, 1.0, "same canonical key"))
            continue

        score = similarity(text, commitment.text)
        if score >= SIMILARITY_FLOOR:
            scored.append(Candidate(commitment, score, f"lexical similarity {score}"))

    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    return scored[:MAX_CANDIDATES]


def unmentioned(existing: list[Commitment], matched: set[UUID], *, as_of: date) -> list[Commitment]:
    """Open commitments that were due and that nobody brought up.

    Only counts ones already due. A commitment due next month going unmentioned
    in today's standup is not news; one that was due last week and went
    unmentioned is the most reliable failure signal there is.
    """
    return [
        commitment
        for commitment in existing
        if commitment.id not in matched
        and commitment.due_date is not None
        and commitment.due_date <= as_of
    ]

"""Resolving who owns something.

The same shape as segmentation and deadlines: do the exact part exactly, and
send only the genuinely ambiguous residue to a model.

Much of what looks like coreference is arithmetic once turns are attributed.
"I'll take it" said by Priya means Priya, with certainty, no reasoning
required. A name matches the roster or it does not. What actually needs
language is "you", "he", and a first name shared by two people, and that is the
short list the Attributor agent gets.

The abstention rule runs through all of it: an unknown owner is left unknown.
A confidently wrong owner is worse than a blank one, because it becomes a task
the real owner never sees and nobody chases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID

# Below this, a name is a different name rather than a mistyped one.
_NAME_SIMILARITY_FLOOR = 0.82

_FIRST_PERSON = frozenset({"i", "i'll", "ill", "me", "my", "myself", "i've", "i'd", "i'm"})

# Phrases that name no one. Extracting these as owners is how "we" becomes a
# person, and how work ends up assigned to nobody in particular but marked done.
_COLLECTIVE = frozenset(
    {
        "we",
        "we'll",
        "us",
        "our",
        "the team",
        "someone",
        "somebody",
        "anyone",
        "everyone",
        "people",
        "they",
        "folks",
        "whoever",
        "tbd",
        "unassigned",
        "nobody",
    }
)

_SECOND_OR_THIRD_PERSON = frozenset({"you", "you'll", "your", "he", "she", "him", "her", "his"})


@dataclass(frozen=True)
class RosterEntry:
    id: UUID
    name: str
    aliases: tuple[str, ...] = ()
    role: str | None = None

    def labels(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Attribution:
    person_id: UUID | None
    display_name: str | None
    confidence: float
    reason: str
    method: str

    @property
    def needs_agent(self) -> bool:
        """Whether this should go to the Attributor, and possibly to a human."""
        return self.person_id is None and self.method in {"pronoun", "ambiguous", "unmatched"}


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z\s']", "", value.strip().lower()).strip()


def resolve_owner(
    hint: str | None,
    roster: list[RosterEntry],
    *,
    speaker: str | None = None,
) -> Attribution:
    """Map a spoken owner hint onto a person, or abstain.

    `speaker` is who said the line the hint came from, which is what makes
    first-person resolution exact rather than guesswork.
    """
    if not hint or not hint.strip():
        return Attribution(None, None, 0.0, "Nobody was named.", "absent")

    text = _normalise(hint)

    if text in _COLLECTIVE:
        return Attribution(None, None, 0.0, f'"{hint.strip()}" names no individual.', "collective")

    if text in _FIRST_PERSON:
        if speaker is None:
            return Attribution(
                None, None, 0.0, "First person, but the turn has no speaker.", "pronoun"
            )
        matched = _match_name(speaker, roster)
        if matched is None:
            return Attribution(
                None,
                speaker,
                0.4,
                f'"{hint.strip()}" is {speaker}, who is not on the roster.',
                "unmatched",
            )
        person, score, how = matched
        return Attribution(
            person.id,
            person.name,
            min(1.0, 0.95 * score),
            f'"{hint.strip()}" was said by {speaker}, matched to {person.name} ({how}).',
            "first_person",
        )

    if text in _SECOND_OR_THIRD_PERSON:
        return Attribution(
            None,
            None,
            0.0,
            f'"{hint.strip()}" needs the surrounding turns to resolve.',
            "pronoun",
        )

    matched = _match_name(hint, roster)
    if matched is None:
        return Attribution(
            None,
            hint.strip(),
            0.3,
            f'"{hint.strip()}" does not match anyone on the roster.',
            "unmatched",
        )

    person, score, how = matched
    if score < 0:
        return Attribution(
            None,
            hint.strip(),
            0.0,
            f'"{hint.strip()}" matches more than one person on the roster.',
            "ambiguous",
        )

    return Attribution(
        person.id,
        person.name,
        min(1.0, score),
        f'"{hint.strip()}" matched {person.name} ({how}).',
        "matched",
    )


def _match_name(value: str, roster: list[RosterEntry]) -> tuple[RosterEntry, float, str] | None:
    """Exact, then alias, then unique first name, then near-miss.

    A first name shared by two people returns a negative score rather than
    picking one, because "Alex" in a room with three of them is a question for
    a human, not a coin flip.
    """
    target = _normalise(value)
    if not target:
        return None

    # Every exact match, not the first one. Three people can share the alias
    # "Alex", and taking whoever appears first in the roster would assign work
    # by list order.
    exact = [
        person for person in roster if any(_normalise(label) == target for label in person.labels())
    ]
    if len(exact) == 1:
        return exact[0], 1.0, "exact"
    if len(exact) > 1:
        return exact[0], -1.0, "ambiguous exact match"

    first_name_matches = [
        person
        for person in roster
        if any(_normalise(label).split(" ")[0] == target for label in person.labels())
    ]
    if len(first_name_matches) == 1:
        return first_name_matches[0], 0.9, "first name"
    if len(first_name_matches) > 1:
        return first_name_matches[0], -1.0, "ambiguous first name"

    best: tuple[RosterEntry, float] | None = None
    for person in roster:
        for label in person.labels():
            score = SequenceMatcher(None, target, _normalise(label)).ratio()
            if score >= _NAME_SIMILARITY_FLOOR and (best is None or score > best[1]):
                best = (person, score)

    if best is None:
        return None
    return best[0], round(best[1] * 0.9, 3), "near match"

"""Grounding verification: the gate a hallucinated item cannot pass.

Every extracted item must quote the transcript. This module finds that quote in
the source and attaches its character offsets, or rejects the item. It is plain
code rather than an agent on purpose: "is this string in that string" has an
exact answer, and asking a model for it would reintroduce the very failure the
gate exists to stop.

Three tiers, most trustworthy first:

``exact``       the quote is a substring of the transcript.
``normalized``  it matches once whitespace, case, and typographic punctuation
                are folded. Models routinely straighten curly quotes and
                collapse a line break, and rejecting those loses real items
                for no gain in safety.
``repaired``    a near miss above the similarity floor, snapped to the actual
                transcript text. The stored quote is still verbatim, which
                keeps the guarantee intact while not throwing away a good item
                over one dropped word.

Anything below the floor is rejected with a reason.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.agents.contracts import Evidence

# A near miss this close is a transcription artefact, not an invention. Set by
# eyeballing the gold set: below this, matches start being genuinely different
# sentences. The evaluation harness reports the repair rate so this number
# stays honest rather than becoming folklore.
SIMILARITY_FLOOR = 0.90

# Typographic characters models silently normalise on their way out.
_FOLDED_CHARACTERS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        " ": " ",
    }
)


@dataclass(frozen=True)
class Located:
    start: int
    end: int
    text: str
    method: str
    similarity: float = 1.0


class _FoldedText:
    """A case- and whitespace-folded view that can map back to the original.

    The index map is what makes repair safe: a match found in folded space is
    always reported using offsets into the untouched source, so the quote that
    reaches the database is the transcript's own wording.
    """

    def __init__(self, source: str) -> None:
        folded: list[str] = []
        offsets: list[int] = []
        previous_was_space = False

        for index, character in enumerate(unicodedata.normalize("NFKC", source)):
            translated = character.translate(_FOLDED_CHARACTERS)
            if translated.isspace():
                if previous_was_space:
                    continue
                folded.append(" ")
                offsets.append(index)
                previous_was_space = True
                continue
            folded.append(translated.lower())
            offsets.append(index)
            previous_was_space = False

        self.text = "".join(folded)
        self._offsets = offsets

    def to_source_span(self, start: int, length: int) -> tuple[int, int]:
        if not self._offsets or length <= 0:
            return 0, 0
        first = self._offsets[start]
        last = self._offsets[min(start + length - 1, len(self._offsets) - 1)]
        return first, last + 1


def _fold(value: str) -> str:
    collapsed = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", value).translate(_FOLDED_CHARACTERS)
    )
    return collapsed.strip().lower()


def locate(transcript: str, quote: str, *, windows: list[str] | None = None) -> Located | None:
    """Find `quote` in `transcript`, repairing a near miss if one is close enough."""
    cleaned = quote.strip()
    if not cleaned:
        return None

    position = transcript.find(cleaned)
    if position != -1:
        return Located(position, position + len(cleaned), cleaned, "exact")

    folded_source = _FoldedText(transcript)
    folded_quote = _fold(cleaned)
    if not folded_quote:
        return None

    position = folded_source.text.find(folded_quote)
    if position != -1:
        start, end = folded_source.to_source_span(position, len(folded_quote))
        return Located(start, end, transcript[start:end], "normalized")

    return _repair(transcript, folded_source, folded_quote, windows)


def _repair(
    transcript: str,
    folded_source: _FoldedText,
    folded_quote: str,
    windows: list[str] | None,
) -> Located | None:
    """Snap a near miss onto real transcript text, or give up.

    Candidates are the speaker turns when the Scribe has produced them, and
    lines otherwise. Comparing against whole-transcript sliding windows would
    be quadratic, and a commitment never spans half a meeting anyway.

    ponytail: linear scan over turns. Fine at meeting scale (hundreds of
    turns); if transcripts ever get long enough for this to show up in a
    profile, index the folded text with a suffix automaton instead.
    """
    candidates = windows if windows else transcript.splitlines()
    matcher = SequenceMatcher(a=folded_quote, autojunk=False)

    best_ratio = 0.0
    best_candidate: str | None = None
    for candidate in candidates:
        folded_candidate = _fold(candidate)
        if not folded_candidate:
            continue
        matcher.set_seq2(folded_candidate)
        # Cheap upper bounds first: both are O(1)-ish and skip most candidates.
        if matcher.real_quick_ratio() < SIMILARITY_FLOOR:
            continue
        if matcher.quick_ratio() < SIMILARITY_FLOOR:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_candidate = ratio, candidate

    if best_candidate is None or best_ratio < SIMILARITY_FLOOR:
        return None

    position = folded_source.text.find(_fold(best_candidate))
    if position == -1:
        return None

    start, end = folded_source.to_source_span(position, len(_fold(best_candidate)))
    return Located(start, end, transcript[start:end], "repaired", round(best_ratio, 3))


def verify_evidence(
    transcript: str,
    evidence: list[Evidence],
    *,
    turns: list[str] | None = None,
) -> tuple[list[Evidence], list[str]]:
    """Ground every quote, returning the ones that held and the reasons for the rest."""
    grounded: list[Evidence] = []
    reasons: list[str] = []

    for item in evidence:
        found = locate(transcript, item.quote, windows=turns)
        if found is None:
            reasons.append(f"Quote not found in the transcript: {item.quote[:120]!r}")
            continue

        grounded.append(
            item.model_copy(
                update={
                    "quote": found.text,
                    "start": found.start,
                    "end": found.end,
                    "turn_index": _turn_index(turns, found.text),
                    "match": found.method,
                }
            )
        )

    return grounded, reasons


def _turn_index(turns: list[str] | None, text: str) -> int | None:
    if not turns:
        return None
    folded = _fold(text)
    for index, turn in enumerate(turns):
        if folded in _fold(turn):
            return index
    return None

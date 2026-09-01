"""Turn segmentation.

Almost every transcript in the wild is ``Speaker: text``, sometimes with a
timestamp. That is a parsing problem with an exact answer, so it is parsed
rather than inferred: a model call here would be slower, cost money, and be
wrong more often than a regex.

The Scribe agent exists for the residue, transcripts this cannot parse, which
is a real case (raw ASR output arrives as one unpunctuated block). Deciding
whether to escalate is `needs_model`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "Priya:", "[00:04:12] Priya:", "Priya (Eng):", "PRIYA NAIR:"
_SPEAKER_LINE = re.compile(
    r"""
    ^
    (?:\[?\s*(?P<timestamp>\d{1,2}:\d{2}(?::\d{2})?)\s*\]?\s*)?   # optional timestamp
    (?P<speaker>[A-Z][\w.'\-]*(?:\s+[\w.'\-]+){0,3})              # up to four words
    (?:\s*\([^)]{1,40}\))?                                        # optional role
    \s*:\s
    (?P<text>.*)
    $
    """,
    re.VERBOSE,
)

# A line that only looks like a speaker line because it starts with a capital
# and contains a colon. Without this, "Note: we should ship Friday" becomes a
# person called Note.
_NOT_A_SPEAKER = frozenset(
    {
        "note",
        "action",
        "actions",
        "decision",
        "decisions",
        "agenda",
        "attendees",
        "present",
        "apologies",
        "summary",
        "todo",
        "next steps",
        "recording",
        "transcript",
        "system",
    }
)


@dataclass(frozen=True)
class Turn:
    index: int
    speaker: str | None
    text: str
    start: int
    """Character offset of `text` in the original transcript, so evidence spans
    resolve against the source rather than against a cleaned-up copy."""

    end: int
    timestamp: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "speaker": self.speaker,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "timestamp": self.timestamp,
        }


def segment(transcript: str) -> list[Turn]:
    """Split into speaker turns, keeping offsets into the original text.

    Continuation lines are folded into the turn above, because a wrapped
    sentence is one utterance and splitting it would cut evidence spans in half.
    """
    turns: list[Turn] = []
    offset = 0

    for line in transcript.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            offset += len(line)
            continue

        match = _SPEAKER_LINE.match(stripped)
        speaker = match.group("speaker").strip() if match else None

        if match and speaker and speaker.lower() not in _NOT_A_SPEAKER:
            text = match.group("text").strip()
            start = offset + line.index(text) if text and text in line else offset
            turns.append(
                Turn(
                    index=len(turns),
                    speaker=speaker,
                    text=text,
                    start=start,
                    end=start + len(text),
                    timestamp=match.group("timestamp"),
                )
            )
        elif turns:
            previous = turns[-1]
            start = offset + (len(line) - len(line.lstrip()))
            turns[-1] = Turn(
                index=previous.index,
                speaker=previous.speaker,
                text=f"{previous.text} {stripped}".strip(),
                start=previous.start,
                end=start + len(stripped),
                timestamp=previous.timestamp,
            )
        else:
            start = offset + (len(line) - len(line.lstrip()))
            turns.append(
                Turn(
                    index=len(turns),
                    speaker=None,
                    text=stripped,
                    start=start,
                    end=start + len(stripped),
                )
            )

        offset += len(line)

    return turns


def needs_model(transcript: str, turns: list[Turn]) -> bool:
    """Whether this transcript is messy enough to be worth a model call.

    Two triggers: nobody is attributed at all, or the whole thing landed in one
    turn while being long enough that it plainly is not one utterance. Both are
    what unpunctuated ASR output looks like.
    """
    if not turns:
        return True
    attributed = sum(1 for turn in turns if turn.speaker)
    if attributed == 0:
        return True
    return len(turns) == 1 and len(transcript) > 400


def participants(turns: list[Turn]) -> list[str]:
    """Distinct speakers, in the order they first spoke."""
    seen: dict[str, None] = {}
    for turn in turns:
        if turn.speaker:
            seen.setdefault(turn.speaker, None)
    return list(seen)


def turn_texts(turns: list[Turn]) -> list[str]:
    """Windows the Verifier searches when repairing a near-miss quote."""
    return [turn.text for turn in turns]

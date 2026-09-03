"""Spotting an instruction hidden in a transcript.

This is the *detector*, and it is deliberately not the defence. The defence is
structural and lives in the agent roster: the agents that read raw transcript
text have no tools, and the agent that writes to the outside world never reads
the transcript and accepts only commitment IDs that have already been
extracted, grounded, reviewed, attributed and dated. An instruction buried in a
meeting has nothing to reach, whatever this module says about it.

So what is this for? Three things a structural defence cannot do on its own:

It **records** the attempt, so a human can see that somebody tried. A silent
defence teaches nobody.

It **shows up in the run**, so a reviewer watching the console sees the system
notice rather than having to trust that it would have.

It **fails safe rather than clever**. Matching is plain code, not a model:
a classifier asked whether text is an injection is itself reading the
injection, and the answer would be one more thing to defend.

Detection is heuristic and says so. A phrase list catches the attempts people
actually paste; it will miss a careful one, and a real meeting can say "ignore
what I said earlier" in complete innocence, so a flag is a note rather than a
verdict. Nothing here blocks a run: an over-eager filter that refuses to
process a legitimate meeting is a worse product than one that processes it and
tells you what it saw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Ordered by how strongly each indicates an attempt rather than a sentence.
# Every pattern below appeared in a real prompt-injection corpus or in the
# adversarial fixtures under evals/, not in a list of things that sound scary.
_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "instruction_override",
        "Tries to cancel the instructions the agents were given.",
        re.compile(
            r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?"
            r"(previous|prior|earlier|above|preceding)\s+"
            r"(instructions?|prompts?|rules?|directions?)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_impersonation",
        "Impersonates a system or developer turn inside the transcript.",
        re.compile(
            r"(^|\n)\s*(system|developer|assistant)\s*[:>\]]",
            re.IGNORECASE,
        ),
    ),
    (
        "agent_targeting",
        "Addresses one of this system's agents by name.",
        re.compile(
            r"\b(operator|analyst|skeptic|attributor|chronos|herald|historian|"
            r"researcher|scribe|chief of staff)\s*[,:]\s*(please\s+)?"
            r"(create|make|assign|mark|delete|send|ignore|do)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration",
        "Asks for credentials, keys, or the system prompt.",
        re.compile(
            r"\b(reveal|print|show|repeat|leak|send)\b[^.\n]{0,40}\b"
            r"(system prompt|instructions|api[\s_-]?key|secret|password|token)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "forced_completion",
        "Tries to have work marked done without anybody doing it.",
        re.compile(
            r"\bmark\s+(all|every|each)\b[^.\n]{0,30}\b(complete|completed|done|closed)\b",
            re.IGNORECASE,
        ),
    ),
]


@dataclass(frozen=True)
class Flag:
    kind: str
    why: str
    quote: str
    start: int

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "why": self.why, "quote": self.quote, "start": self.start}


def scan(transcript: str) -> list[Flag]:
    """Every suspicious span in the transcript, with the text that matched.

    The quote is included on purpose. A flag that says "injection detected"
    and cannot show what it saw is unfalsifiable, and the whole point of the
    audit trail here is that a person can check the system's judgment instead
    of taking it.
    """
    flags: list[Flag] = []

    for kind, why, pattern in _PATTERNS:
        for match in pattern.finditer(transcript):
            # A little context each side, because the matched fragment alone
            # often reads as innocuous.
            start = max(match.start() - 30, 0)
            end = min(match.end() + 60, len(transcript))
            flags.append(
                Flag(
                    kind=kind,
                    why=why,
                    quote=transcript[start:end].strip().replace("\n", " ")[:200],
                    start=match.start(),
                )
            )

    return sorted(flags, key=lambda flag: flag.start)


def summarise(flags: list[Flag]) -> str:
    """One line for the run log, phrased for somebody who did not write this."""
    if not flags:
        return ""
    kinds = sorted({flag.kind for flag in flags})
    return (
        f"security: {len(flags)} suspicious passage"
        f"{'' if len(flags) == 1 else 's'} in this transcript ({', '.join(kinds)}). "
        "Recorded, not obeyed: the agents that read the transcript have no tools."
    )

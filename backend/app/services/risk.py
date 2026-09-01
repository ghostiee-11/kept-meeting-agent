"""Risk scoring.

Deliberately a pure function rather than a model call. Three reasons, and the
third is the one that matters:

It is testable. A weight change shows up as a diff in a test, not as a vibe.

It is free and instant, so risk can be recomputed on every read rather than
being cached and going stale.

It is explainable. The UI shows "slipped twice (+0.30), owner inferred rather
than stated (+0.12), unmentioned for two meetings (+0.20)" instead of an
unsourced number. A person being told their commitment is at risk deserves to
know why, and an LLM-produced score cannot tell them.

Weights are ordered by how well each factor actually predicts failure. Silence
is weighted highest: a commitment nobody has mentioned in two meetings is in
more trouble than one that is three days late and being actively discussed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Contribution ceilings. A commitment can only reach 1.0 by being bad in
# several ways at once, which is the intent: one missed day is not a crisis.
WEIGHTS = {
    "silence": 0.30,
    "slippage": 0.25,
    "overdue": 0.25,
    "no_owner": 0.20,
    "no_deadline": 0.15,
    "unresolved_question": 0.15,
    "blocked": 0.15,
    "weak_owner": 0.10,
    "weak_deadline": 0.10,
    "conditional": 0.05,
}

# Beyond this, later days stop adding risk. Something 40 days late is not twice
# as at risk as something 20 days late; both are in the same trouble.
_OVERDUE_SATURATION_DAYS = 14
_SLIP_SATURATION = 3
_SILENCE_SATURATION = 3


@dataclass(frozen=True)
class RiskFactor:
    name: str
    contribution: float
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "contribution": round(self.contribution, 3),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    factors: list[RiskFactor]

    @property
    def band(self) -> str:
        if self.score >= 0.6:
            return "high"
        if self.score >= 0.3:
            return "medium"
        return "low"

    def as_dicts(self) -> list[dict[str, object]]:
        return [factor.as_dict() for factor in self.factors]

    def explain(self) -> str:
        if not self.factors:
            return "On track."
        return "; ".join(f"{factor.detail} (+{factor.contribution:.2f})" for factor in self.factors)


def score_commitment(
    *,
    due_date: date | None,
    today: date,
    status: str = "confirmed",
    owner_id: object | None = None,
    owner_confidence: float = 1.0,
    due_confidence: float = 1.0,
    slip_count: int = 0,
    silence_streak: int = 0,
    open_questions: int = 0,
    blocked: bool = False,
    conditional: bool = False,
) -> RiskAssessment:
    """Score one commitment, returning the total and what produced it.

    `today` is passed in rather than read from the clock so the score is a
    function of its inputs and a test can pin the date.
    """
    factors: list[RiskFactor] = []

    if status in {"done", "dropped"}:
        # A finished or abandoned commitment carries no forward risk. Scoring
        # one as high-risk would clutter the view with settled history.
        return RiskAssessment(0.0, [])

    if silence_streak > 0:
        share = min(silence_streak / _SILENCE_SATURATION, 1.0)
        meetings = "meeting" if silence_streak == 1 else "meetings"
        factors.append(
            RiskFactor(
                "silence",
                WEIGHTS["silence"] * share,
                f"not mentioned in {silence_streak} {meetings}",
            )
        )

    if slip_count > 0:
        share = min(slip_count / _SLIP_SATURATION, 1.0)
        times = "once" if slip_count == 1 else f"{slip_count} times"
        factors.append(
            RiskFactor("slippage", WEIGHTS["slippage"] * share, f"deadline moved {times}")
        )

    if due_date is not None and due_date < today:
        days = (today - due_date).days
        share = min(days / _OVERDUE_SATURATION_DAYS, 1.0)
        factors.append(
            RiskFactor(
                "overdue",
                WEIGHTS["overdue"] * share,
                f"{days} {'day' if days == 1 else 'days'} past due",
            )
        )

    if owner_id is None:
        factors.append(RiskFactor("no_owner", WEIGHTS["no_owner"], "nobody owns it"))
    elif owner_confidence < 0.7:
        factors.append(
            RiskFactor(
                "weak_owner",
                WEIGHTS["weak_owner"] * (1 - owner_confidence),
                "owner inferred rather than stated",
            )
        )

    if due_date is None:
        factors.append(RiskFactor("no_deadline", WEIGHTS["no_deadline"], "no deadline"))
    elif due_confidence < 0.7:
        factors.append(
            RiskFactor(
                "weak_deadline",
                WEIGHTS["weak_deadline"] * (1 - due_confidence),
                "deadline interpreted, not stated",
            )
        )

    if open_questions > 0:
        share = min(open_questions / 2, 1.0)
        label = "question" if open_questions == 1 else "questions"
        factors.append(
            RiskFactor(
                "unresolved_question",
                WEIGHTS["unresolved_question"] * share,
                f"{open_questions} unanswered {label}",
            )
        )

    if blocked:
        factors.append(RiskFactor("blocked", WEIGHTS["blocked"], "blocked"))

    if conditional:
        factors.append(
            RiskFactor("conditional", WEIGHTS["conditional"], "depends on something else first")
        )

    total = min(1.0, sum(factor.contribution for factor in factors))
    factors.sort(key=lambda factor: factor.contribution, reverse=True)
    return RiskAssessment(round(total, 3), factors)

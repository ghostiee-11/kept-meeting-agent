"""Risk scoring.

The point of a deterministic scorer is that its behaviour is pinned by tests
rather than by a prompt. A weight change should show up here as a diff.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from app.services.risk import score_commitment

TODAY = date(2026, 9, 2)
OWNER = uuid4()


def healthy(**overrides: object) -> dict[str, object]:
    return {
        "due_date": TODAY + timedelta(days=7),
        "today": TODAY,
        "owner_id": OWNER,
        "owner_confidence": 1.0,
        "due_confidence": 1.0,
        **overrides,
    }


def test_an_owned_dated_commitment_carries_no_risk() -> None:
    assessment = score_commitment(**healthy())  # type: ignore[arg-type]

    assert assessment.score == 0.0
    assert assessment.band == "low"
    assert assessment.explain() == "On track."


def test_a_finished_commitment_carries_no_forward_risk() -> None:
    """Scoring settled history as high risk would clutter the view with things
    nobody needs to act on."""
    assessment = score_commitment(
        **healthy(due_date=TODAY - timedelta(days=30), slip_count=3, status="done")  # type: ignore[arg-type]
    )

    assert assessment.score == 0.0


def test_an_unowned_commitment_is_riskier_than_an_owned_one() -> None:
    unowned = score_commitment(**healthy(owner_id=None))  # type: ignore[arg-type]

    assert unowned.score > 0
    assert any(factor.name == "no_owner" for factor in unowned.factors)


def test_overdue_risk_grows_with_the_delay_then_saturates() -> None:
    """Forty days late is not twice the trouble of twenty. Both are simply
    in trouble, and an unbounded score would drown out every other factor."""
    scores = [
        score_commitment(**healthy(due_date=TODAY - timedelta(days=days))).score  # type: ignore[arg-type]
        for days in (1, 7, 14, 40)
    ]

    assert scores[0] < scores[1] < scores[2]
    assert scores[2] == scores[3]


def test_silence_outweighs_being_a_few_days_late() -> None:
    """A commitment nobody has mentioned in two meetings is in more trouble
    than one that is three days late and still being actively discussed."""
    silent = score_commitment(**healthy(silence_streak=2))  # type: ignore[arg-type]
    late = score_commitment(**healthy(due_date=TODAY - timedelta(days=3)))  # type: ignore[arg-type]

    assert silent.score > late.score


def test_repeated_slippage_accumulates() -> None:
    once = score_commitment(**healthy(slip_count=1))  # type: ignore[arg-type]
    thrice = score_commitment(**healthy(slip_count=3))  # type: ignore[arg-type]

    assert thrice.score > once.score
    assert "3 times" in thrice.explain()


def test_the_score_is_capped_at_one() -> None:
    worst = score_commitment(
        due_date=TODAY - timedelta(days=60),
        today=TODAY,
        owner_id=None,
        owner_confidence=0.0,
        due_confidence=0.0,
        slip_count=5,
        silence_streak=5,
        open_questions=4,
        blocked=True,
        conditional=True,
    )

    assert worst.score == 1.0
    assert worst.band == "high"


def test_every_factor_explains_itself_in_words_a_person_can_act_on() -> None:
    """The whole reason this is not an LLM call. Someone told their commitment
    is at risk deserves to know which part is the problem."""
    assessment = score_commitment(
        **healthy(slip_count=2, silence_streak=1, owner_confidence=0.4)  # type: ignore[arg-type]
    )
    explanation = assessment.explain()

    assert "deadline moved 2 times" in explanation
    assert "not mentioned in 1 meeting" in explanation
    assert "owner inferred rather than stated" in explanation
    assert all(factor.detail for factor in assessment.factors)


def test_factors_are_ordered_by_how_much_they_contributed() -> None:
    """The UI shows the top few, so the biggest problem has to come first."""
    assessment = score_commitment(**healthy(owner_id=None, conditional=True))  # type: ignore[arg-type]

    contributions = [factor.contribution for factor in assessment.factors]
    assert contributions == sorted(contributions, reverse=True)


def test_a_stated_owner_beats_an_inferred_one() -> None:
    stated = score_commitment(**healthy(owner_confidence=1.0))  # type: ignore[arg-type]
    inferred = score_commitment(**healthy(owner_confidence=0.4))  # type: ignore[arg-type]

    assert inferred.score > stated.score


def test_bands_split_where_the_ui_colours_change() -> None:
    assert score_commitment(**healthy()).band == "low"  # type: ignore[arg-type]
    assert score_commitment(**healthy(owner_id=None, due_date=None)).band == "medium"  # type: ignore[arg-type]
    assert (
        score_commitment(
            **healthy(owner_id=None, due_date=None, silence_streak=2, slip_count=2)  # type: ignore[arg-type]
        ).band
        == "high"
    )

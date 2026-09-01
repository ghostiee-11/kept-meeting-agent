"""Verdict application and the obligation partition.

The model calls are exercised by `python -m app.cli analyse` and measured by
the evaluation harness. What is tested here is the logic around them, which is
where a silent bug would quietly delete somebody's commitment.
"""

from __future__ import annotations

from app.agents.contracts import Classification, Evidence, ExtractedCommitment, ReviewVerdict
from app.agents.intelligence import IntelligenceResult, _apply_verdicts


def candidate(
    text: str, classification: Classification = Classification.COMMITMENT
) -> ExtractedCommitment:
    return ExtractedCommitment(
        text=text,
        classification=classification,
        reasoning="because",
        confidence=0.8,
        evidence=[Evidence(quote=text)],
    )


def test_a_keep_verdict_leaves_the_item_alone() -> None:
    kept, rejected = _apply_verdicts(
        [candidate("Ship the EU region")],
        [ReviewVerdict(index=0, verdict="keep", reason="Rahul accepted it")],
    )

    assert len(kept) == 1
    assert rejected == []


def test_a_reject_verdict_removes_the_item_and_keeps_the_reason() -> None:
    """The reason is the product. A rejection nobody can check is just a
    deletion, and the console shows these so a human can overrule."""
    kept, rejected = _apply_verdicts(
        [candidate("Prepare the migration plan by Friday")],
        [ReviewVerdict(index=0, verdict="reject", reason="Retracted at turn 15")],
    )

    assert kept == []
    assert rejected[0].rejected_by == "skeptic"
    assert rejected[0].reason == "Retracted at turn 15"


def test_a_downgrade_reclassifies_and_records_why() -> None:
    kept, rejected = _apply_verdicts(
        [candidate("Look at caching")],
        [
            ReviewVerdict(
                index=0,
                verdict="downgrade",
                reclassify_to=Classification.SUGGESTION,
                reason="Nobody picked it up",
            )
        ],
    )

    assert kept[0].classification is Classification.SUGGESTION
    assert "Nobody picked it up" in kept[0].reasoning
    assert rejected == []


def test_a_downgrade_without_a_target_class_is_treated_as_keep() -> None:
    """Rather than guessing a class the reviewer did not name. Silently
    picking one would put an unreviewed judgment into the ledger."""
    kept, _ = _apply_verdicts(
        [candidate("Ship the EU region")],
        [ReviewVerdict(index=0, verdict="downgrade", reason="unsure")],
    )

    assert kept[0].classification is Classification.COMMITMENT


def test_an_out_of_range_index_is_ignored() -> None:
    """Models miscount lists. Acting on a bad index would reject somebody
    else's commitment, so a verdict that names nothing is dropped."""
    kept, rejected = _apply_verdicts(
        [candidate("Ship the EU region")],
        [ReviewVerdict(index=7, verdict="reject", reason="off by miles")],
    )

    assert len(kept) == 1
    assert rejected == []


def test_a_candidate_with_no_verdict_survives() -> None:
    """Silence is not an objection. A reviewer that returns a short list must
    not delete the items it forgot to mention."""
    kept, rejected = _apply_verdicts(
        [candidate("First"), candidate("Second"), candidate("Third")],
        [ReviewVerdict(index=1, verdict="reject", reason="duplicate")],
    )

    assert [item.text for item in kept] == ["First", "Third"]
    assert len(rejected) == 1


def test_obligations_and_set_aside_partition_the_classified_items() -> None:
    """A suggestion correctly called a suggestion is the taxonomy working, not
    a mistake, so it is shown separately from things that were rejected."""
    result = IntelligenceResult(
        decisions=[],
        commitments=[
            candidate("Ship the EU region", Classification.COMMITMENT),
            candidate("Own the vendor call", Classification.ACTION_ITEM),
            candidate("Look at caching", Classification.SUGGESTION),
            candidate("Latency has been rough", Classification.DISCUSSION),
        ],
        blockers=[],
        rejections=[],
    )

    assert [item.text for item in result.obligations] == [
        "Ship the EU region",
        "Own the vendor call",
    ]
    assert [item.text for item in result.set_aside] == [
        "Look at caching",
        "Latency has been rough",
    ]

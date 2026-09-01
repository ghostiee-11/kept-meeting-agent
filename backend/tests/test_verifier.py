"""The grounding gate.

If these pass, a fabricated commitment cannot reach the database, and every
stored quote is text that genuinely appears in the transcript.
"""

from __future__ import annotations

from app.agents.contracts import Evidence
from app.services.verifier import SIMILARITY_FLOOR, locate, verify_evidence

TRANSCRIPT = """Priya: I'll have the migration plan ready by Friday.
Adit: Someone should probably look at the caching layer at some point.
Meera: Let's go with Postgres, then — the Mongo option costs us the joins.
Tom: I won't be able to get to the design review this week.
"""

TURNS = TRANSCRIPT.strip().splitlines()


def test_exact_quote_is_located() -> None:
    found = locate(TRANSCRIPT, "I'll have the migration plan ready by Friday.")

    assert found is not None
    assert found.method == "exact"
    assert TRANSCRIPT[found.start : found.end] == found.text


def test_collapsed_whitespace_still_matches() -> None:
    """Models routinely join a wrapped line. That is a formatting artefact,
    not an invention, so it must not cost us a real commitment."""
    found = locate(TRANSCRIPT, "I'll   have the migration plan\n  ready by Friday.")

    assert found is not None
    assert found.method == "normalized"
    assert "migration plan ready by Friday" in found.text


def test_straightened_typographic_quotes_still_match() -> None:
    source = "Priya: I’ll send the deck — by Thursday."
    found = locate(source, "I'll send the deck - by Thursday.")

    assert found is not None
    assert found.method == "normalized"


def test_near_miss_is_repaired_to_the_transcript_wording() -> None:
    """A dropped word snaps back to the real sentence, so the stored quote is
    still the transcript's own words rather than the model's paraphrase."""
    found = locate(TRANSCRIPT, "I won't be able to get to design review this week.", windows=TURNS)

    assert found is not None
    assert found.method == "repaired"
    assert found.similarity >= SIMILARITY_FLOOR
    assert found.text in TRANSCRIPT
    assert "the design review" in found.text


def test_a_fabricated_quote_is_rejected() -> None:
    assert (
        locate(TRANSCRIPT, "Rahul: I'll ship the billing rewrite on Monday.", windows=TURNS) is None
    )


def test_verify_separates_grounded_items_from_rejected_ones() -> None:
    grounded, reasons = verify_evidence(
        TRANSCRIPT,
        [
            Evidence(quote="Let's go with Postgres", speaker="Meera"),
            Evidence(quote="We agreed to migrate to DynamoDB next quarter.", speaker="Meera"),
        ],
        turns=TURNS,
    )

    assert len(grounded) == 1
    assert grounded[0].start is not None
    assert TRANSCRIPT[grounded[0].start : grounded[0].end] == grounded[0].quote
    assert len(reasons) == 1
    assert "not found" in reasons[0]


def test_offsets_always_index_the_untouched_source() -> None:
    """The whole guarantee rests on this: whatever route a quote took through
    folding or repair, its offsets must slice the original transcript."""
    quotes = [
        "I'll have the migration plan ready by Friday.",
        "I'll   have the migration plan\n  ready by Friday.",
        "I won't be able to get to design review this week.",
    ]

    for quote in quotes:
        found = locate(TRANSCRIPT, quote, windows=TURNS)
        assert found is not None, quote
        assert TRANSCRIPT[found.start : found.end] == found.text


def test_turn_index_points_at_the_speaker_turn() -> None:
    grounded, _ = verify_evidence(
        TRANSCRIPT,
        [Evidence(quote="I won't be able to get to the design review this week.", speaker="Tom")],
        turns=TURNS,
    )

    assert grounded[0].turn_index == 3


def test_empty_quote_is_rejected_rather_than_matching_everything() -> None:
    assert locate(TRANSCRIPT, "   ") is None

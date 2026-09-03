"""Rules the prompts must keep stating.

Prompts are code with a bad type system: nothing fails when a rule is edited
away, and the damage shows up as a quietly worse extraction weeks later. These
pin the two distinctions that were actually got wrong during the build, so a
future rewrite has to keep them or explain itself to a failing test.
"""

from __future__ import annotations

from app.agents import prompts


def test_a_new_date_is_not_described_as_a_retraction() -> None:
    """The bug this catches lost a real promise. "Scratch Friday, I'll get it
    to you Tuesday" was read as a withdrawal, the item was dropped, and the
    obligation vanished from the ledger entirely."""
    assert "Rescheduled" in prompts.HARD_CASES
    assert "LATEST date" in prompts.HARD_CASES


def test_the_skeptic_is_told_the_same_thing() -> None:
    """Both halves have to agree. The Analyst keeping a rescheduled item is
    worth nothing if the reviewer then rejects it as retracted."""
    assert "not a withdrawal" in prompts.SKEPTIC.lower()


def test_the_skeptic_is_warned_against_rejecting_good_items() -> None:
    """An adversarial reviewer with no counterweight becomes a filter that
    quietly deletes the system's output. Precision is not free."""
    assert "Do not be contrarian" in prompts.SKEPTIC


def test_the_analyst_is_told_to_abstain_rather_than_guess() -> None:
    assert "Vague ownership" in prompts.HARD_CASES

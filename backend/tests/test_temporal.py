"""Deadline arithmetic.

A table test, because the failure mode here is quiet: a deadline resolved two
days late does not raise, it just makes the ledger wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.temporal import resolve

# Wednesday 2 September 2026.
MEETING = date(2026, 9, 2)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("today", date(2026, 9, 2)),
        ("EOD", date(2026, 9, 2)),
        ("tomorrow", date(2026, 9, 3)),
        ("by Friday", date(2026, 9, 4)),
        ("Monday", date(2026, 9, 7)),
        ("next Tuesday", date(2026, 9, 8)),
        ("in three days", date(2026, 9, 5)),
        ("in 2 weeks", date(2026, 9, 16)),
        ("end of the week", date(2026, 9, 4)),
        ("EOW", date(2026, 9, 4)),
        ("end of next week", date(2026, 9, 11)),
        ("end of the month", date(2026, 9, 30)),
        ("end of the quarter", date(2026, 9, 30)),
        ("2026-11-14", date(2026, 11, 14)),
    ],
)
def test_spoken_deadlines_resolve_to_the_right_day(phrase: str, expected: date) -> None:
    assert resolve(phrase, meeting_date=MEETING).due == expected


def test_end_of_week_is_friday_not_sunday() -> None:
    """Teams mean the working week. Resolving to Sunday would put every
    end-of-week deadline two days late."""
    assert resolve("end of the week", meeting_date=MEETING).due.weekday() == 4  # type: ignore[union-attr]


def test_a_bare_weekday_means_the_next_one_not_today() -> None:
    """Someone saying "Wednesday" in Wednesday's standup means next week."""
    assert resolve("Wednesday", meeting_date=MEETING).due == date(2026, 9, 9)


def test_no_deadline_given_is_not_a_deadline_of_today() -> None:
    for phrase in (None, "", "   "):
        resolved = resolve(phrase, meeting_date=MEETING)
        assert resolved.due is None
        assert resolved.confidence == 0.0


def test_an_unresolvable_phrase_abstains_rather_than_guessing() -> None:
    """ "Before the Diwali break" needs the web, not arithmetic. A guess here
    would silently move somebody's due date."""
    resolved = resolve("before the Diwali break", meeting_date=MEETING)

    assert resolved.due is None
    assert resolved.needs_help is True
    assert resolved.raw == "before the Diwali break"


def test_a_convention_is_marked_low_confidence_so_a_human_can_check() -> None:
    """A sprint is two weeks by convention, not by fact."""
    resolved = resolve("this sprint", meeting_date=MEETING)

    assert resolved.due == date(2026, 9, 16)
    assert resolved.confidence < 0.6
    assert resolved.needs_help is True


def test_a_weekday_is_high_confidence_because_it_is_unambiguous() -> None:
    resolved = resolve("by Friday", meeting_date=MEETING)

    assert resolved.confidence >= 0.9
    assert resolved.needs_help is False


def test_the_spoken_phrase_is_preserved_for_a_human_to_check() -> None:
    assert resolve("by end of next week", meeting_date=MEETING).raw == "by end of next week"


def test_next_weekday_means_the_following_calendar_week() -> None:
    """Said on Wednesday the 2nd, "next Tuesday" is the 8th: the Tuesday of the
    week after this one. This week's Tuesday has already gone."""
    assert resolve("next Tuesday", meeting_date=MEETING).due == date(2026, 9, 8)


def test_next_weekday_and_bare_weekday_differ_when_the_day_is_still_ahead() -> None:
    """Said on Monday, "Friday" is this week and "next Friday" is the week
    after. Collapsing the two would move a deadline by seven days."""
    monday = date(2026, 8, 31)

    assert resolve("Friday", meeting_date=monday).due == date(2026, 9, 4)
    assert resolve("next Friday", meeting_date=monday).due == date(2026, 9, 11)


def test_next_weekday_is_scored_lower_because_english_disagrees_with_itself() -> None:
    """Some speakers mean the very next occurrence. The convention is applied
    and flagged, rather than applied and hidden."""
    assert resolve("next Tuesday", meeting_date=MEETING).confidence < (
        resolve("Tuesday", meeting_date=MEETING).confidence
    )

"""Owner resolution and, more importantly, owner abstention.

A confidently wrong owner is worse than a blank one: it becomes a task the
real owner never sees and nobody chases. Most of these tests are about the
system declining to answer.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.roster import RosterEntry, resolve_owner

PRIYA = RosterEntry(uuid4(), "Priya Nair", ("Priya", "Pri", "Preeya"), "Engineering Lead")
ADIT = RosterEntry(uuid4(), "Adit Sharma", ("Adit", "Adi"), "Backend Engineer")
MEERA = RosterEntry(uuid4(), "Meera Krishnan", ("Meera", "Mira"), "Product Manager")
ROSTER = [PRIYA, ADIT, MEERA]

THREE_ALEXES = [
    RosterEntry(uuid4(), "Alex Chen", ("Alex",)),
    RosterEntry(uuid4(), "Alex Kumar", ("Alex",)),
    RosterEntry(uuid4(), "Alexandra Reid", ("Alex", "Alexandra")),
]


def test_a_full_name_matches_exactly() -> None:
    result = resolve_owner("Priya Nair", ROSTER)

    assert result.person_id == PRIYA.id
    assert result.confidence == 1.0


def test_an_alias_matches() -> None:
    assert resolve_owner("Pri", ROSTER).person_id == PRIYA.id


def test_a_transcription_error_still_matches_the_right_person() -> None:
    """Speech-to-text mangles names constantly. "Preeya" is Priya, not a
    seventh member of the team."""
    result = resolve_owner("Preeya", ROSTER)

    assert result.person_id == PRIYA.id
    assert result.confidence > 0.8


def test_first_person_resolves_to_whoever_said_it() -> None:
    """ "I'll take it" said by Priya means Priya, exactly, with no reasoning
    required. This is arithmetic once turns are attributed."""
    result = resolve_owner("I", ROSTER, speaker="Priya")

    assert result.person_id == PRIYA.id
    assert result.method == "first_person"
    assert "said by Priya" in result.reason


def test_first_person_with_no_speaker_abstains() -> None:
    result = resolve_owner("I'll", ROSTER, speaker=None)

    assert result.person_id is None
    assert result.needs_agent is True


@pytest.mark.parametrize("hint", ["we", "someone", "the team", "whoever", "TBD"])
def test_a_collective_names_nobody(hint: str) -> None:
    """This is how "we" becomes a person and work ends up assigned to nobody
    in particular but still marked as owned."""
    result = resolve_owner(hint, ROSTER)

    assert result.person_id is None
    assert result.method == "collective"
    assert result.needs_agent is False, "a collective is settled, not ambiguous"


@pytest.mark.parametrize("hint", ["you", "he", "she", "your"])
def test_a_second_or_third_person_pronoun_goes_to_the_agent(hint: str) -> None:
    """These genuinely need surrounding turns, which is the residue the
    Attributor exists for."""
    result = resolve_owner(hint, ROSTER)

    assert result.person_id is None
    assert result.method == "pronoun"
    assert result.needs_agent is True


def test_three_people_called_alex_produce_a_question_not_a_coin_flip() -> None:
    result = resolve_owner("Alex", THREE_ALEXES)

    assert result.person_id is None
    assert result.method == "ambiguous"
    assert result.needs_agent is True
    assert "more than one" in result.reason


def test_one_alex_is_not_ambiguous() -> None:
    result = resolve_owner("Alex", [THREE_ALEXES[0]])

    assert result.person_id == THREE_ALEXES[0].id


def test_a_name_nobody_recognises_is_not_forced_onto_the_nearest_person() -> None:
    result = resolve_owner("Jonathan", ROSTER)

    assert result.person_id is None
    assert result.method == "unmatched"
    assert result.display_name == "Jonathan", "the spoken name survives for a human to read"


def test_no_hint_at_all_abstains_quietly() -> None:
    result = resolve_owner(None, ROSTER)

    assert result.person_id is None
    assert result.method == "absent"
    assert result.needs_agent is False


def test_every_abstention_explains_itself() -> None:
    """The reason is shown in the clarification a human answers, so an empty
    one would make the question unanswerable."""
    for hint in ("we", "you", "Jonathan", None, "Alex"):
        assert resolve_owner(hint, THREE_ALEXES).reason

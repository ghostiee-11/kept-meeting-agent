"""Learning who people are from the transcript itself.

The roster cannot only come from a seed script: a system that recognises
nobody who joined after the seed was written is a demo, not a product. So a
speaker the workspace has never seen is enrolled from the meeting they spoke
in.

Every test here is about the line between reading and guessing. A speaker
label is evidence: whoever said "I'll do it" was in the room. A name that
merely appears in someone else's sentence is not, and neither is a label that
belongs to somebody already known, or to two of them at once. Getting this
wrong splits one person's promises across two rows, or buries a question a
human needed to answer.
"""

from __future__ import annotations

import uuid

from app.services.roster import RosterEntry, resolve_owner, unenrolled_speakers

ROSTER = [
    RosterEntry(uuid.uuid4(), "Priya Nair", ("Priya", "Pri", "Preeya")),
    RosterEntry(uuid.uuid4(), "Alex Reyes", ("Alex", "Alex R")),
    RosterEntry(uuid.uuid4(), "Alex Duarte", ("Alex", "Alex D")),
]


def test_a_speaker_nobody_knows_is_enrolled() -> None:
    assert unenrolled_speakers(["Kavya", "Daniel"], ROSTER) == ["Kavya", "Daniel"]


def test_somebody_already_on_the_roster_is_not_added_again() -> None:
    assert unenrolled_speakers(["Priya Nair", "Priya"], ROSTER) == []


def test_a_misspelt_name_joins_the_person_it_belongs_to() -> None:
    """ "Preeya" is Priya spelled badly. A second row for her would split her
    promises across two people, which is worse than not learning anything."""
    assert unenrolled_speakers(["Preeya"], ROSTER) == []


def test_a_name_two_people_answer_to_is_left_alone() -> None:
    """A third Alex would bury the question. "Alex" in a room with two of them
    is for a human to settle, and the Attributor raises it."""
    assert unenrolled_speakers(["Alex"], ROSTER) == []


def test_labels_that_are_not_people_are_refused() -> None:
    """Transcription tools emit these. Enrolling them puts "Everyone" on the
    roster and then assigns work to it."""
    labels = ["Unknown Speaker", "Everyone", "The team", "Speaker", "Moderator", "All"]

    assert unenrolled_speakers(labels, ROSTER) == []


def test_a_sentence_that_lost_its_colon_is_not_a_name() -> None:
    runaway = "So I think what we should do here is wait for the vendor to confirm"

    assert unenrolled_speakers([runaway], ROSTER) == []


def test_the_same_new_speaker_is_only_enrolled_once() -> None:
    """Speakers arrive once per turn. Enrolling per mention would create one
    person per line they spoke."""
    assert unenrolled_speakers(["Kavya", "kavya", "Kavya"], ROSTER) == ["Kavya"]


def test_enrolment_makes_their_own_promises_resolvable() -> None:
    """The point of the whole thing. Before enrolment a new speaker's "I'll do
    it" has no owner and becomes a clarification; after it, it is theirs."""
    before = resolve_owner("I", ROSTER, speaker="Kavya")
    assert before.person_id is None

    enrolled = [*ROSTER, RosterEntry(uuid.uuid4(), "Kavya", ("Kavya",))]
    after = resolve_owner("I", enrolled, speaker="Kavya")

    assert after.display_name == "Kavya"
    assert after.person_id is not None
    assert after.confidence > 0.9


def test_a_name_only_mentioned_by_someone_else_is_not_enrolled() -> None:
    """Enrolment reads speaker labels, never the body of a sentence. "Marketing
    said they'd handle the copy" must not put Marketing on the roster, and
    naming somebody who is not in the room must not create them."""
    spoke = ["Kavya"]

    assert unenrolled_speakers(spoke, ROSTER) == ["Kavya"]
    assert "Marketing" not in unenrolled_speakers(spoke, ROSTER)

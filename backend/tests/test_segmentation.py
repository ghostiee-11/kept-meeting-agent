from __future__ import annotations

from app.services.segmentation import needs_model, participants, segment, turn_texts

CLEAN = """Priya: I'll have the migration plan ready by Friday.
Adit: Sounds good. I'll review it Monday.
Meera: Let's go with Postgres.
"""

WRAPPED = """Priya: I'll have the migration plan ready by Friday,
assuming the staging environment is back up.
Adit: Fine by me.
"""

WITH_TIMESTAMPS = """[00:04:12] Priya: We should ship this week.
[00:04:20] Adit (Backend): I'll cut the release branch.
"""

MINUTES_STYLE = """Attendees: Priya, Adit, Meera
Agenda: migration, pricing
Priya: I'll own the migration.
Note: Meera was late.
"""

ASR_GARBLE = (
    "okay so i think we should probably get the migration done by friday "
    "priya can you take that yeah sure ill do it and then adit you said you "
    "would look at the caching thing right yeah at some point maybe next sprint "
    "we also need to decide on the database and i think postgres makes sense here"
)


def test_speaker_turns_are_split_and_attributed() -> None:
    turns = segment(CLEAN)

    assert [turn.speaker for turn in turns] == ["Priya", "Adit", "Meera"]
    assert turns[0].text == "I'll have the migration plan ready by Friday."


def test_offsets_index_the_original_transcript() -> None:
    """Evidence spans resolve against the source, so a turn's offsets must
    slice the untouched text rather than a cleaned-up copy."""
    for turn in segment(CLEAN):
        assert CLEAN[turn.start : turn.end] == turn.text


def test_a_wrapped_line_stays_one_utterance() -> None:
    """Splitting a wrapped sentence would cut evidence spans in half."""
    turns = segment(WRAPPED)

    assert len(turns) == 2
    assert "assuming the staging environment is back up" in turns[0].text


def test_timestamps_and_roles_are_stripped_from_the_speaker() -> None:
    turns = segment(WITH_TIMESTAMPS)

    assert [turn.speaker for turn in turns] == ["Priya", "Adit"]
    assert turns[0].timestamp == "00:04:12"
    assert turns[1].text == "I'll cut the release branch."


def test_minutes_headings_do_not_become_people() -> None:
    """Without this, "Note:" and "Agenda:" join the roster and the Attributor
    starts assigning work to a person called Note."""
    speakers = participants(segment(MINUTES_STYLE))

    assert speakers == ["Priya"]
    assert "Note" not in speakers
    assert "Agenda" not in speakers


def test_a_clean_transcript_does_not_need_a_model_call() -> None:
    """The common case is a parsing problem with an exact answer. Spending a
    model call on it would be slower, cost money, and be wrong more often."""
    assert needs_model(CLEAN, segment(CLEAN)) is False
    assert needs_model(WITH_TIMESTAMPS, segment(WITH_TIMESTAMPS)) is False


def test_unattributed_asr_output_escalates_to_the_scribe() -> None:
    assert needs_model(ASR_GARBLE, segment(ASR_GARBLE)) is True


def test_an_empty_transcript_yields_no_turns() -> None:
    assert segment("") == []
    assert segment("\n\n  \n") == []


def test_turn_texts_are_the_windows_the_verifier_searches() -> None:
    assert turn_texts(segment(CLEAN))[2] == "Let's go with Postgres."

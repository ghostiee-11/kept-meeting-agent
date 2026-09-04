"""Deciding that a promise made again is the promise the ledger already has.

The Historian's model says which of this meeting's obligations restates an
open commitment, and this is the guard around that answer. It is deliberately
suspicious: a wrong yes deletes a real new obligation, while a wrong no leaves
a duplicate row that a person can see and merge. The asymmetry decides every
rule here.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.agents.contracts import Classification, Evidence, ExtractedCommitment
from app.agents.historian import MatchVerdict, Slippage, _apply, _restated_index
from app.graph.state import ResolvedItem
from app.models.base import CommitmentKind, CommitmentStatus, MentionOutcome
from app.models.domain import Commitment, CommitmentMention
from app.services.roster import Attribution
from app.services.temporal import Resolved

RAHUL = uuid.uuid4()
PRIYA = uuid.uuid4()


class RecordingSession:
    def __init__(self) -> None:
        self.rows: list[object] = []

    def add(self, row: object) -> None:
        self.rows.append(row)


def ledger_row(text: str, owner_id: uuid.UUID | None = RAHUL) -> Commitment:
    return Commitment(
        workspace_id=uuid.uuid4(),
        canonical_key="key",
        text=text,
        kind=CommitmentKind.COMMITMENT,
        status=CommitmentStatus.CONFIRMED,
        owner_id=owner_id,
        evidence=[],
    )


def obligation(text: str, owner_id: uuid.UUID | None = RAHUL) -> ResolvedItem:
    return ResolvedItem(
        commitment=ExtractedCommitment(
            text=text,
            classification=Classification.COMMITMENT,
            reasoning="because",
            confidence=0.9,
            evidence=[Evidence(quote=text)],
        ),
        attribution=Attribution(
            person_id=owner_id,
            display_name="Rahul Menon" if owner_id else None,
            confidence=1.0,
            reason="named",
            method="exact",
        ),
        deadline=Resolved(due=None, confidence=0.0, method="none", raw=""),
    )


def verdict(restates: int | None) -> MatchVerdict:
    return MatchVerdict(mentioned=True, outcome="progress", restates=restates, reasoning="why")


def test_the_same_promise_worded_differently_is_folded_in() -> None:
    chosen = _restated_index(
        verdict(0),
        ledger_row("Resolve the authentication issues"),
        [obligation("Resolve authentication issues by Friday end of day.")],
        already=set(),
    )

    assert chosen == 0


def test_two_pieces_of_work_on_the_same_subject_stay_separate() -> None:
    """The case lexical similarity cannot decide, and the reason the judgment
    is the model's: defining the analytics events and implementing them score
    alike and are two promises."""
    chosen = _restated_index(
        verdict(None),
        ledger_row("Define the analytics events"),
        [obligation("Implement analytics events in the backend by Monday.")],
        already=set(),
    )

    assert chosen is None


def test_a_match_between_different_people_is_refused() -> None:
    """Two people can promise similar work. Only the same person restating it
    is the same promise."""
    chosen = _restated_index(
        verdict(0),
        ledger_row("Update the pricing page", owner_id=PRIYA),
        [obligation("Update the pricing page by Monday at 10 AM.", owner_id=RAHUL)],
        already=set(),
    )

    assert chosen is None


def test_an_unowned_side_does_not_block_the_match() -> None:
    """Unowned is unresolved, not a contradiction. The commitment being
    restated is often the one nobody has accepted yet."""
    chosen = _restated_index(
        verdict(0),
        ledger_row("Update the pricing page", owner_id=None),
        [obligation("Update the pricing page by Monday at 10 AM.", owner_id=RAHUL)],
        already=set(),
    )

    assert chosen == 0


def test_an_answer_with_nothing_in_common_is_refused() -> None:
    """The floor under the model. Pointing at an unrelated obligation deletes
    it, so the texts have to share something."""
    chosen = _restated_index(
        verdict(0),
        ledger_row("Send the migration plan to Meera"),
        [obligation("Prepare QA capacity for Thursday.")],
        already=set(),
    )

    assert chosen is None


def test_an_index_that_is_not_on_the_list_is_ignored() -> None:
    for guess in (5, -1):
        assert (
            _restated_index(
                verdict(guess),
                ledger_row("Resolve the authentication issues"),
                [obligation("Resolve authentication issues by Friday.")],
                already=set(),
            )
            is None
        )


def test_one_obligation_cannot_close_two_ledger_rows() -> None:
    """Two open rows both claiming the same new obligation would delete it
    once and leave the second row wrongly marked as restated."""
    items = [obligation("Resolve authentication issues by Friday.")]

    first = _restated_index(
        verdict(0), ledger_row("Resolve the authentication issues"), items, already=set()
    )
    second = _restated_index(verdict(0), ledger_row("Resolve the auth issues"), items, already={0})

    assert first == 0
    assert second is None


def test_a_duplicate_verdict_records_one_mention() -> None:
    session = RecordingSession()
    commitment = ledger_row("Resolve the authentication issues")
    commitment.id = uuid.uuid4()
    result = Slippage()
    match = MatchVerdict(mentioned=True, outcome="progress", reasoning="Discussed in the meeting.")
    meeting_id = uuid.uuid4()

    for _ in range(2):
        _apply(
            session,  # type: ignore[arg-type]
            commitment,
            match,
            result,
            meeting_id=meeting_id,
            meeting_date=date(2026, 9, 16),
            timezone="Asia/Kolkata",
        )

    mentions = [row for row in session.rows if isinstance(row, CommitmentMention)]

    assert len(mentions) == 1
    assert mentions[0].outcome is MentionOutcome.PROGRESS
    assert result.progressed == ["Resolve the authentication issues"]

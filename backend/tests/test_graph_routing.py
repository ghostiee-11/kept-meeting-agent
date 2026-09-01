"""Supervisor routing guards.

These pin the two behaviours that cost a real run 7x its budget and made it
report an empty meeting that was not empty. Both were found by running the
graph, and both are the kind of thing that silently comes back.
"""

from __future__ import annotations

from typing import Any, cast

from app.graph.meeting_graph import _already_done, _candidates_in, _completed_teams, _summarise
from app.graph.state import MeetingState


def state(**overrides: Any) -> MeetingState:
    base: dict[str, Any] = {
        "turns": [1, 2, 3],
        "roster": [1, 2],
        "progress": [],
        "replans": 0,
    }
    return cast(MeetingState, {**base, **overrides})


def test_the_summary_names_what_is_still_outstanding() -> None:
    """A supervisor left to infer what is left from a growing list of reports
    re-delegates work it has already delegated."""
    summary = _summarise(state(progress=["intelligence: 3 obligations"]))

    assert "Still to run: resolution, execution." in summary


def test_the_summary_says_to_finish_once_every_team_has_run() -> None:
    summary = _summarise(state(progress=["intelligence: ok", "resolution: ok", "execution: ok"]))

    assert "Every team has run." in summary
    assert "Call finish now." in summary


def test_completed_teams_are_read_off_the_progress_log() -> None:
    assert _completed_teams(state(progress=["intelligence: 3", "resolution: 2"])) == {
        "intelligence",
        "resolution",
    }


def test_a_team_that_has_already_run_refuses_to_run_again() -> None:
    """The guard is structural rather than a prompt, because a supervisor that
    can loop will loop, and the loop costs a multiple of the run's budget."""
    skip = _already_done(
        state(progress=["resolution: 3 of 3 owned"]), "resolution", "Delegate to execution next."
    )

    assert skip is not None
    assert skip.goto == "chief_of_staff"


def test_the_refusal_tells_the_supervisor_what_to_do_instead() -> None:
    """A bare refusal would just get retried. Naming the next step is what
    breaks the loop rather than deferring it."""
    skip = _already_done(
        state(progress=["resolution: done"]), "resolution", "Delegate to execution next."
    )

    assert skip is not None
    assert "Delegate to execution next." in skip.update["progress"][0]  # type: ignore[index]


def test_a_team_that_has_not_run_is_allowed_through() -> None:
    assert _already_done(state(progress=["intelligence: 3"]), "resolution", "next") is None


def test_candidates_are_pulled_out_of_an_abstention_reason() -> None:
    """They become the one-click options on the clarification, which is what
    makes the question answerable instead of homework."""
    reason = "Two people are equally plausible. Candidates: Priya Nair, Adit Sharma."

    assert _candidates_in(reason) == ["Priya Nair", "Adit Sharma"]


def test_a_reason_with_no_candidates_yields_none() -> None:
    assert _candidates_in("Nobody was named.") == []

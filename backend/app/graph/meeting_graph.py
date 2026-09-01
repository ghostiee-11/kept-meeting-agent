"""The meeting graph: a Chief of Staff supervising three teams.

The topology is a hybrid on purpose. An LLM supervisor routes at the top,
where the decisions are genuinely open, and the teams themselves are
deterministic subgraphs because their internal order is known.

Being honest about where the supervisor earns its keep: the happy path is
intelligence, then resolution, then execution, every time. What the Chief
actually decides is what to do when the happy path does not hold. Send
extraction back when most of a batch was rejected. Proceed or escalate when
owners could not be settled. Finish early when a meeting genuinely contains no
obligations. Those are real judgments with real consequences, and hard-coding
them would mean hard-coding a policy that should be able to change.

Full peer-to-peer handoff was rejected: with no coordinator, termination is
hard to guarantee and traces are hard to read. A fixed pipeline was rejected
too, because it cannot replan. This is the middle, and `ToolCallLimitMiddleware`
guarantees the loop ends whatever the model decides.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents import prompts
from app.agents.base import AgentSpec, build_agent
from app.agents.handoff import handoff_tools
from app.agents.intelligence import extract, review
from app.agents.resolution import enrich_decisions, resolve_deadlines, resolve_owners
from app.config import Settings
from app.graph.state import MeetingState, Question, ResolvedItem
from app.logging import get_logger
from app.services import trace
from app.services.model_router import ModelRouter, Tier
from app.services.risk import score_commitment
from app.services.roster import Attribution
from app.services.search import SearchService
from app.services.temporal import Resolved

log = get_logger(__name__)

TEAMS = {
    "intelligence": (
        "Read the transcript. Extract decisions, obligations, and blockers with "
        "verbatim evidence, then challenge each one and discard what does not hold up."
    ),
    "resolution": (
        "Work out who owns each obligation and when it is due. Abstain rather "
        "than guess, and report what could not be settled."
    ),
    "execution": ("Score risk, create tasks in the tracker, and draft the follow-up messages."),
}

# Above this share of rejected candidates, extraction went wrong rather than
# the meeting being thin, and it is worth one more attempt.
REJECTION_REPLAN_THRESHOLD = 0.6
MAX_REPLANS = 1


def _completed_teams(state: MeetingState) -> set[str]:
    return {entry.split(":", 1)[0] for entry in state.get("progress", [])}


def _summarise(state: MeetingState) -> str:
    """What the Chief sees before choosing. Outcomes only, no transcripts."""
    done = _completed_teams(state)
    remaining = [team for team in TEAMS if team not in done]

    lines = [
        f"Meeting: {len(state['turns'])} turns, "
        f"{len(state.get('roster', []))} people on the roster."
    ]
    if progress := state.get("progress"):
        lines.append("Reported so far:")
        lines.extend(f"  {entry}" for entry in progress)
    else:
        lines.append("Nothing has run yet.")

    # Stated explicitly. A supervisor left to infer what is outstanding from a
    # growing list of reports will re-delegate work it has already delegated.
    lines.append(f"Still to run: {', '.join(remaining)}." if remaining else "Every team has run.")
    if not remaining:
        lines.append("Call finish now.")
    return "\n".join(lines)


def _already_done(state: MeetingState, team: str, next_step: str) -> Command[Any] | None:
    """Refuse redundant work and point the supervisor at what is left.

    The supervisor is a language model and will occasionally re-delegate a team
    that has already reported. Left to a prompt, that costs a multiple of the
    run's budget for identical output. The guard is structural so a stray
    routing decision is cheap instead of expensive, and the refusal is worded
    so the Chief learns what to do instead.
    """
    if team not in _completed_teams(state):
        return None

    message = f"{team}: already ran, nothing to redo. {next_step}"
    trace.record(team, "artifact", payload={"skipped": True})
    return Command(goto="chief_of_staff", update={"progress": [message]})


def build_meeting_graph(
    *,
    router: ModelRouter,
    settings: Settings,
    search: SearchService | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Assemble the supervisor and its three team nodes."""

    chief = build_agent(
        AgentSpec(
            name="chief_of_staff",
            # Routing is a short, structured decision over a few lines of
            # progress, not deep reasoning. The cheap tier does it as well and
            # leaves the reasoning model's rate limit for the work that needs it.
            tier=Tier.FAST,
            purpose="Plan the run, route between teams, and decide when it is done.",
            system_prompt=prompts.CHIEF_OF_STAFF,
            # Handoffs and a terminator. Nothing else: the supervisor cannot
            # touch a transcript, the database, or the task API, so the only
            # thing it can do is decide who works next.
            tools=handoff_tools(TEAMS),
            tool_call_limit=settings.max_supervisor_steps,
        ),
        router=router,
        settings=settings,
    )

    async def chief_of_staff(state: MeetingState) -> Command[Any]:
        # Primed fresh from progress rather than from an accumulated history.
        # See app/agents/handoff.py for why: it keeps the prompt the same size
        # on the tenth hop as on the first, and avoids the orphaned tool
        # message that a shared history produces across a subgraph boundary.
        await chief.ainvoke({"messages": [{"role": "user", "content": _summarise(state)}]})

        # Reaching here means the supervisor did not call a handoff tool, so
        # there is nothing left for it to route to.
        return Command(
            goto=END,
            update={"final_summary": state.get("final_summary") or "Run ended without a summary."},
        )

    async def intelligence(state: MeetingState) -> Command[Any]:
        # A rerun is allowed only when the previous attempt asked for one and
        # the budget for reruns has not been spent. Gating on the counter alone
        # blocked the very retry it was meant to permit, because the counter is
        # incremented by the run that requests the retry.
        may_rerun = state.get("replan_wanted", False) and state.get("replans", 0) < MAX_REPLANS
        if not may_rerun and (
            skip := _already_done(state, "intelligence", "Delegate to resolution next.")
        ):
            return skip

        found = await extract(state["transcript"], state["turns"], router=router, settings=settings)
        kept, review_rejections = await review(
            found.commitments,
            state["transcript"],
            state["turns"],
            router=router,
            settings=settings,
        )

        obligations = [item for item in kept if item.classification.value in _COMMITTED]
        set_aside = [item for item in kept if item.classification.value not in _COMMITTED]
        rejections = [*found.rejections, *review_rejections]

        considered = len(kept) + len(rejections)
        rejected_share = len(rejections) / considered if considered else 0.0
        unreliable = rejected_share > REJECTION_REPLAN_THRESHOLD and considered > 2
        is_rerun = state.get("replan_wanted", False)
        replans_after = state.get("replans", 0) + (1 if is_rerun else 0)
        should_replan = (bool(found.failed_briefs) or unreliable) and replans_after < MAX_REPLANS

        summary = (
            f"intelligence: {len(found.decisions)} decisions, {len(obligations)} obligations, "
            f"{len(found.blockers)} blockers, {len(rejections)} rejected"
        )
        if found.failed_briefs:
            # Said plainly, because the alternative is the supervisor reporting
            # an empty meeting when the work was simply lost.
            summary += (
                f". WARNING: the {', '.join(found.failed_briefs)} "
                f"{'brief' if len(found.failed_briefs) == 1 else 'briefs'} failed to run, so "
                "those counts are not trustworthy and this needs running again"
            )
        elif unreliable:
            summary += ". Most candidates were thrown out, so extraction looks unreliable"
        trace.record("intelligence", "artifact", payload={"summary": summary})

        return Command(
            goto="chief_of_staff",
            update={
                "decisions": found.decisions,
                "blockers": found.blockers,
                "set_aside": set_aside,
                "rejections": rejections,
                "items": [
                    ResolvedItem(commitment=item, attribution=_UNRESOLVED, deadline=_NO_DATE)
                    for item in obligations
                ],
                "progress": [summary],
                "replans": replans_after,
                "replan_wanted": should_replan,
            },
        )

    async def resolution(state: MeetingState) -> Command[Any]:
        if skip := _already_done(state, "resolution", "Delegate to execution next."):
            return skip

        obligations = [item.commitment for item in state.get("items", [])]
        if not obligations:
            return Command(
                goto="chief_of_staff",
                update={"progress": ["resolution: nothing to resolve"]},
            )

        attributed = await resolve_owners(
            obligations, state.get("roster", []), state["turns"], router=router, settings=settings
        )
        deadlines = await resolve_deadlines(
            obligations,
            meeting_date=state["meeting_date"],
            timezone=state.get("timezone", "UTC"),
            router=router,
            settings=settings,
            search=search,
        )
        enrichments = (
            await enrich_decisions(
                state.get("decisions", []), router=router, settings=settings, search=search
            )
            if search is not None
            else {}
        )

        items: list[ResolvedItem] = []
        questions: list[Question] = []
        for index, ((commitment, attribution), deadline) in enumerate(
            zip(attributed, deadlines, strict=True)
        ):
            items.append(
                ResolvedItem(commitment=commitment, attribution=attribution, deadline=deadline)
            )
            questions.extend(_questions_for(index, commitment, attribution, deadline))

        summary = (
            f"resolution: {sum(1 for i in items if i.owner_id)} of {len(items)} owned, "
            f"{sum(1 for i in items if i.deadline.due)} dated, "
            f"{len(questions)} open questions"
        )
        trace.record("resolution", "artifact", payload={"summary": summary})

        return Command(
            goto="chief_of_staff",
            update={
                "items": items,
                "questions": questions,
                "enrichments": enrichments,
                "progress": [summary],
            },
        )

    async def execution(state: MeetingState) -> Command[Any]:
        if skip := _already_done(state, "execution", "Call finish now."):
            return skip

        today: date = state["meeting_date"]
        open_by_index = _questions_by_commitment(state.get("questions", []))

        scored = [
            ResolvedItem(
                commitment=item.commitment,
                attribution=item.attribution,
                deadline=item.deadline,
                risk=score_commitment(
                    due_date=item.deadline.due,
                    today=today,
                    owner_id=item.owner_id,
                    owner_confidence=item.attribution.confidence,
                    due_confidence=item.deadline.confidence,
                    open_questions=open_by_index.get(index, 0),
                    conditional=bool(item.commitment.conditional_on),
                ),
                external_task_id=item.external_task_id,
            )
            for index, item in enumerate(state.get("items", []))
        ]

        at_risk = sum(1 for item in scored if item.risk and item.risk.band != "low")
        summary = f"execution: {len(scored)} scored, {at_risk} at risk"
        trace.record("execution", "artifact", payload={"summary": summary})

        return Command(
            goto="chief_of_staff",
            update={
                "items": scored,
                "progress": [summary],
            },
        )

    builder = StateGraph(MeetingState)
    builder.add_node("chief_of_staff", chief_of_staff)
    builder.add_node("intelligence", intelligence)
    builder.add_node("resolution", resolution)
    builder.add_node("execution", execution)
    builder.add_edge(START, "chief_of_staff")

    return builder.compile(checkpointer=checkpointer)


_COMMITTED = {"commitment", "action_item"}

# Placeholders used between extraction and resolution, so an item always has the
# same shape and no downstream code has to handle a half-built one.
_UNRESOLVED = Attribution(None, None, 0.0, "Not resolved yet.", "pending")
_NO_DATE = Resolved(None, 0.0, "pending", "")


def _questions_by_commitment(questions: list[Question]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for question in questions:
        counts[question.commitment_index] = counts.get(question.commitment_index, 0) + 1
    return counts


def _questions_for(index: int, commitment: Any, attribution: Any, deadline: Any) -> list[Question]:
    """Turn an abstention into a question a human can answer in one click.

    A question carries the candidates the agent already inferred. "Who owns
    this?" with three names beside it is answerable; the same question with a
    blank text box is homework.
    """
    questions: list[Question] = []
    evidence = [item.model_dump(mode="json") for item in commitment.evidence[:2]]

    if attribution.person_id is None and attribution.method != "collective":
        questions.append(
            Question(
                subject=commitment.text,
                question=f"Who owns this? {attribution.reason}",
                options=[{"label": name} for name in _candidates_in(attribution.reason)],
                evidence=evidence,
                field_name="owner",
                commitment_index=index,
            )
        )
    elif attribution.method == "collective":
        questions.append(
            Question(
                subject=commitment.text,
                question=(f"Nobody specific took this on: {attribution.reason} Who should own it?"),
                evidence=evidence,
                field_name="owner",
                commitment_index=index,
            )
        )

    if deadline.due is None and commitment.due_hint:
        questions.append(
            Question(
                subject=commitment.text,
                question=f'When is this due? "{commitment.due_hint}" could not be resolved.',
                evidence=evidence,
                field_name="due_date",
                commitment_index=index,
            )
        )

    return questions


def _candidates_in(reason: str) -> list[str]:
    _, _, tail = reason.partition("Candidates: ")
    if not tail:
        return []
    return [name.strip(" .") for name in tail.split(",") if name.strip(" .")][:4]

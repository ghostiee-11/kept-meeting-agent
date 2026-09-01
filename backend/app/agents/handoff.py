"""Agent-to-agent handoff.

**The supervisor and the parent graph do not share a message history**, and
that is the important design decision here.

The obvious implementation has the handoff tool push its ToolMessage into
parent state. It does not work, and the way it fails is instructive: the
AIMessage carrying the tool call lives inside the supervisor's own subgraph, so
only the ToolMessage reaches the parent. The next turn then opens with an
orphaned tool result, which Groq rejects outright ("Tools should have a name")
and other providers quietly misread.

Rather than reassembling that pair, the supervisor keeps no history at all. It
is re-primed each turn from `progress`, a short list of what each team
reported. That fixes the malformed-history problem by removing the history, and
it has two better properties: the supervisor routes on outcomes instead of on a
transcript of its own past reasoning, and its prompt stays the same size on the
tenth hop as on the first instead of growing with every delegation.

Context isolation applies to the teams too. A handoff carries a written brief,
not the sender's reasoning. Passing every message to every agent is the
standard multi-agent failure: cost grows with the square of the team, and
agents start second-guessing each other's half-finished thoughts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.services import trace


def create_handoff_tool(
    *,
    agent_name: str,
    description: str,
    from_agent: str = "chief_of_staff",
) -> BaseTool:
    """Build a tool that transfers control to `agent_name` in the parent graph."""

    @tool(f"delegate_to_{agent_name}", description=description)
    def handoff(brief: str) -> Command[Any]:
        """Hand this piece of work to another agent.

        Args:
            brief: What that agent needs to do, and what to hand back. Written
                for someone who has not read this conversation.
        """
        trace.record(
            from_agent,
            "handoff",
            payload={"to": agent_name, "brief": brief[:500]},
        )
        return Command(
            goto=agent_name,
            # The jump happens in the supervisor's graph, not inside whichever
            # subgraph the tool was invoked from.
            graph=Command.PARENT,
            # No messages. See the module docstring: pushing the ToolMessage
            # here without its AIMessage is what produces a malformed history.
            update={"current_agent": agent_name, "current_brief": brief},
        )

    return handoff


def create_finish_tool(*, from_agent: str = "chief_of_staff") -> BaseTool:
    """End the run.

    An explicit terminator, so finishing is a decision the supervisor makes and
    records rather than something that happens when it runs out of things to
    say.
    """

    @tool("finish", description="End the run. Call this once the meeting is fully processed.")
    def finish(summary: str) -> Command[Any]:
        """Close out the run.

        Args:
            summary: One or two sentences on what was produced and what, if
                anything, is still waiting on a human.
        """
        trace.record(from_agent, "finish", payload={"summary": summary[:500]})
        return Command(
            goto="__end__",
            graph=Command.PARENT,
            update={"final_summary": summary, "current_agent": "done"},
        )

    return finish


def handoff_tools(roster: dict[str, str], *, from_agent: str = "chief_of_staff") -> list[BaseTool]:
    """One handoff tool per agent, plus the terminator.

    The supervisor is given these and nothing else. It cannot touch a
    transcript, a database, or the task API, so the only thing it can do is
    decide who works next, which is the whole of its job.
    """
    tools: list[BaseTool] = [
        create_handoff_tool(agent_name=name, description=purpose, from_agent=from_agent)
        for name, purpose in roster.items()
    ]
    tools.append(create_finish_tool(from_agent=from_agent))
    return tools


HandoffFactory = Callable[..., BaseTool]

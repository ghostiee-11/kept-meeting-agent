"""Agent-to-agent handoff.

Two details are the usual reason multi-agent systems misbehave, and both are
handled here rather than left to chance.

**The AIMessage carrying a tool call and its ToolMessage must travel together.**
Emitting the jump without the acknowledging ToolMessage leaves a dangling tool
call in history, which most providers reject outright and the rest quietly get
confused by.

**Context isolation.** A handoff carries a written brief, not the sender's
transcript of reasoning. Passing every message to every agent is the standard
multi-agent failure: cost grows with the square of the team, and agents start
second-guessing each other's half-finished thoughts instead of doing their own
job. The brief is the contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from langchain.messages import ToolMessage
from langchain.tools import InjectedToolCallId, tool
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
    def handoff(
        brief: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command[Any]:
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
            update={
                "messages": [
                    ToolMessage(
                        content=f"Handed off to {agent_name}.",
                        name=f"delegate_to_{agent_name}",
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_agent": agent_name,
                "current_brief": brief,
            },
        )

    return handoff


def create_finish_tool(*, from_agent: str = "chief_of_staff") -> BaseTool:
    """End the run.

    An explicit terminator, so finishing is a decision the supervisor makes and
    records rather than something that happens when it runs out of things to
    say.
    """

    @tool("finish", description="End the run. Call this once the meeting is fully processed.")
    def finish(
        summary: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command[Any]:
        """Close out the run.

        Args:
            summary: One or two sentences on what was produced and what, if
                anything, is still waiting on a human.
        """
        trace.record(from_agent, "finish", payload={"summary": summary[:500]})
        return Command(
            goto="__end__",
            graph=Command.PARENT,
            update={
                "messages": [
                    ToolMessage(content="Run complete.", name="finish", tool_call_id=tool_call_id)
                ],
                "final_summary": summary,
            },
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

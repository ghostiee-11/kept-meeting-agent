"""Running a meeting, and watching it happen.

The run endpoint streams. That is not decoration: a full run takes tens of
seconds across ten agents, and a request that returns nothing for forty seconds
is indistinguishable from one that has hung. Streaming turns the wait into the
most informative part of the product, because you can watch which agent is
working, which model answered, and what it cost.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.deps import SessionDep, SettingsDep
from app.graph.meeting_graph import build_meeting_graph
from app.graph.state import MeetingState
from app.logging import get_logger
from app.models.base import RunStatus
from app.models.domain import Meeting, Person, Run, Workspace
from app.services import persistence, trace
from app.services.model_router import ModelRouter
from app.services.roster import RosterEntry
from app.services.search import SearchService
from app.services.segmentation import participants, segment, turn_texts
from app.services.temporal import today_in

router = APIRouter(prefix="/meetings", tags=["meetings"])
log = get_logger(__name__)

DEMO_WORKSPACE_SLUG = "kept-demo"


class RunRequest(BaseModel):
    transcript: str = Field(min_length=1)
    title: str = Field(default="Untitled meeting", max_length=255)
    occurred_at: datetime | None = None
    timezone: str = "Asia/Kolkata"
    project: str | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _load_workspace(session: SessionDep) -> Workspace:
    workspace = await session.scalar(select(Workspace).where(Workspace.slug == DEMO_WORKSPACE_SLUG))
    if workspace is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No workspace. Run `make seed` to create the demo workspace and roster.",
        )
    return workspace


@router.post("/run")
async def run_meeting(
    payload: RunRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Process a transcript, streaming each agent's progress as it happens."""
    if len(payload.transcript.encode()) > settings.max_transcript_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Transcript exceeds {settings.max_transcript_bytes} bytes.",
        )

    workspace = await _load_workspace(session)
    roster = [
        RosterEntry(person.id, person.name, tuple(person.aliases), person.role)
        for person in (
            await session.scalars(select(Person).where(Person.workspace_id == workspace.id))
        ).all()
    ]

    turns = segment(payload.transcript)
    occurred_at = payload.occurred_at or datetime.now(UTC)
    meeting, is_new = await persistence.upsert_meeting(
        session,
        workspace_id=workspace.id,
        title=payload.title,
        transcript=payload.transcript,
        occurred_at=occurred_at,
        timezone=payload.timezone,
        participants=participants(turns),
        turns=[turn.as_dict() for turn in turns],
        project=payload.project,
    )

    thread_id = str(uuid.uuid4())
    run = Run(meeting_id=meeting.id, thread_id=thread_id, status=RunStatus.RUNNING)
    session.add(run)
    await session.commit()

    factory: async_sessionmaker[Any] = request.app.state.session_factory
    return StreamingResponse(
        _stream_run(
            factory=factory,
            settings=settings,
            run_id=run.id,
            meeting_id=meeting.id,
            thread_id=thread_id,
            transcript=payload.transcript,
            turns=turns,
            roster=roster,
            timezone=payload.timezone,
            meeting_title=payload.title,
            reused_meeting=not is_new,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Render sits behind a proxy that would otherwise buffer the whole
            # response and defeat the point of streaming.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _stream_run(
    *,
    factory: async_sessionmaker[Any],
    settings: Any,
    run_id: uuid.UUID,
    meeting_id: uuid.UUID,
    thread_id: str,
    transcript: str,
    turns: list[Any],
    roster: list[RosterEntry],
    timezone: str,
    meeting_title: str,
    reused_meeting: bool,
) -> AsyncIterator[str]:
    """Drive the graph, forwarding every agent event to the browser.

    The graph runs in a task while events are drained from a queue, so a slow
    consumer cannot stall the agents and a fast one sees each event as it
    happens rather than in batches.
    """
    router_ = ModelRouter(settings)
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    yield _sse(
        "run_started",
        {
            "run_id": str(run_id),
            "meeting_id": str(meeting_id),
            "turns": len(turns),
            "reused_meeting": reused_meeting,
            "models": router_.describe()["tiers"],
        },
    )

    async def drive() -> MeetingState:
        async with factory() as session:
            search = SearchService(settings, session=session)
            graph = build_meeting_graph(router=router_, settings=settings, search=search)
            initial = {
                "messages": [],
                "meeting_id": str(meeting_id),
                "run_id": str(run_id),
                "transcript": transcript,
                "meeting_title": meeting_title,
                "turn_texts": turn_texts(turns),
                "turns": turns,
                "roster": roster,
                "meeting_date": today_in(timezone),
                "timezone": timezone,
                "progress": [],
                "replans": 0,
                "items": [],
                "decisions": [],
                "blockers": [],
                "set_aside": [],
                "rejections": [],
                "questions": [],
                "enrichments": {},
                "communications": {},
            }
            final: dict[str, Any] = {}
            async for chunk in graph.astream(
                initial,
                {"configurable": {"thread_id": thread_id}, "recursion_limit": 40},
                stream_mode=["updates", "custom"],
                version="v2",
            ):
                if chunk["type"] == "custom":
                    await events.put(chunk["data"])
                elif chunk["type"] == "updates":
                    for node, update in (chunk["data"] or {}).items():
                        final.update(update or {})
                        for line in (update or {}).get("progress", []):
                            await events.put({"type": "team_report", "node": node, "line": line})
            return cast(MeetingState, final)

    with trace.run_trace(str(run_id)) as recorder:
        task = asyncio.create_task(drive())
        task.add_done_callback(lambda _: events.put_nowait(None))

        while True:
            event = await events.get()
            if event is None:
                break
            yield _sse(event.get("type", "event"), event)

        try:
            state = await task
        except Exception as exc:
            log.exception("run.failed", run_id=str(run_id))
            await _mark_failed(factory, run_id, str(exc)[:500])
            yield _sse("run_failed", {"error": str(exc)[:300]})
            return

    async with factory() as session:
        run = await session.get(Run, run_id)
        meeting = await session.get(Meeting, meeting_id)
        counts = await persistence.save_run_results(
            session, run=run, meeting=meeting, state=state, recorder=recorder
        )
        await session.commit()

    tokens_in, tokens_out = recorder.tokens
    yield _sse(
        "run_finished",
        {
            "run_id": str(run_id),
            "summary": state.get("final_summary", ""),
            "counts": counts,
            "cost_usd": round(recorder.cost_usd, 6),
            "tokens": {"in": tokens_in, "out": tokens_out},
            "by_agent": recorder.by_agent(),
        },
    )


async def _mark_failed(factory: async_sessionmaker[Any], run_id: uuid.UUID, error: str) -> None:
    async with factory() as session:
        if run := await session.get(Run, run_id):
            run.status = RunStatus.FAILED
            run.error = error
            run.finished_at = datetime.now(UTC)
            await session.commit()

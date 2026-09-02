"""A mock task-management system, in the shape of Linear or Jira.

This is deliberately a real HTTP boundary rather than a function call. The
Operator agent talks to it over the network with timeouts, retries, and an
idempotency key, so those behaviours are exercised for real instead of being
claimed in a README.

Two knobs make failure demonstrable rather than theoretical:

``MOCK_FAILURE_RATE``   how often a write fails.
``MOCK_FAILURE_MODE``   ``pre`` fails before any work, so a plain retry
                        recovers. ``post`` commits and *then* fails, which is
                        what a real API does when it times out on the way back.
                        Only an idempotency key saves you from a duplicate
                        task, which is precisely the case worth testing.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import date, datetime
from itertools import count
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Sequence, select

from app.deps import SessionDep, SettingsDep
from app.logging import get_logger
from app.models.base import TaskStatus
from app.models.domain import MockTask

router = APIRouter(prefix="/mock/v1", tags=["mock task api"])
log = get_logger(__name__)

TASK_NUMBER_SEQUENCE = Sequence("mock_task_number_seq")

# Counts attempts for MOCK_FAIL_FIRST_N, which is the deterministic
# counterpart to the probabilistic failure rate.
_attempts = count(1)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    assignee: str | None = Field(default=None, max_length=120)
    due_date: date | None = None
    source_commitment_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    status: TaskStatus | None = None
    assignee: str | None = Field(default=None, max_length=120)
    due_date: date | None = None


class TaskRead(BaseModel):
    id: str
    title: str
    description: str | None
    assignee: str | None
    due_date: date | None
    status: TaskStatus
    url: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, task: MockTask) -> TaskRead:
        return cls(
            id=task.external_id,
            title=task.title,
            description=task.description,
            assignee=task.assignee,
            due_date=task.due_date,
            status=task.status,
            url=f"/board/{task.external_id}",
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


async def _simulate_transport(settings: SettingsDep, *, phase: Literal["pre", "post"]) -> None:
    """Inject the latency and failures a real integration would face."""
    if phase == "pre" and settings.mock_latency_ms:
        await asyncio.sleep(settings.mock_latency_ms / 1000)

    if (
        phase == "pre"
        and settings.mock_fail_first_n > 0
        and next(_attempts) <= settings.mock_fail_first_n
    ):
        log.info("mock_task_api.injected_failure", phase=phase, mode="first_n")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Injected failure (first-n).",
        )

    if settings.mock_failure_rate <= 0:
        return
    if settings.mock_failure_mode != phase and settings.mock_failure_mode != "random":
        return
    if random.random() >= settings.mock_failure_rate:
        return

    log.info("mock_task_api.injected_failure", phase=phase)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Injected failure ({phase}-commit).",
    )


@router.post("/tasks", response_model=TaskRead)
async def create_task(
    payload: TaskCreate,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskRead:
    await _simulate_transport(settings, phase="pre")

    if idempotency_key:
        existing = await session.scalar(
            select(MockTask).where(MockTask.idempotency_key == idempotency_key)
        )
        if existing is not None:
            # A replay is a success, not a conflict. Returning the original
            # resource is what makes a retry safe after a post-commit failure.
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replay"] = "true"
            return TaskRead.of(existing)

    number = await session.scalar(TASK_NUMBER_SEQUENCE.next_value())
    task = MockTask(
        external_id=f"KPT-{number:03d}",
        title=payload.title,
        description=payload.description,
        assignee=payload.assignee,
        due_date=payload.due_date,
        status=TaskStatus.TODO,
        idempotency_key=idempotency_key,
        source_commitment_id=payload.source_commitment_id,
        history=[{"at": datetime.now().isoformat(), "change": "created"}],
    )
    session.add(task)
    await session.flush()

    # Commit before the post-phase failure, so the caller sees an error for a
    # write that actually landed. This is the case idempotency exists for.
    await session.commit()
    await _simulate_transport(settings, phase="post")

    response.status_code = status.HTTP_201_CREATED
    return TaskRead.of(task)


@router.patch("/tasks/{external_id}", response_model=TaskRead)
async def update_task(
    external_id: str,
    payload: TaskUpdate,
    session: SessionDep,
    settings: SettingsDep,
) -> TaskRead:
    await _simulate_transport(settings, phase="pre")

    task = await session.scalar(select(MockTask).where(MockTask.external_id == external_id))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No task {external_id}.")

    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(task, field, value)
    if changes:
        task.history = [
            *task.history,
            {"at": datetime.now().isoformat(), "change": changes},
        ]

    await session.flush()
    return TaskRead.of(task)


@router.get("/tasks", response_model=list[TaskRead])
async def list_tasks(
    session: SessionDep,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    assignee: str | None = None,
    limit: Annotated[int, Query(le=200)] = 100,
) -> list[TaskRead]:
    query = select(MockTask).order_by(MockTask.created_at.desc()).limit(limit)
    if task_status is not None:
        query = query.where(MockTask.status == task_status)
    if assignee is not None:
        query = query.where(MockTask.assignee == assignee)

    tasks = (await session.scalars(query)).all()
    return [TaskRead.of(task) for task in tasks]


@router.get("/tasks/{external_id}", response_model=TaskRead)
async def get_task(external_id: str, session: SessionDep) -> TaskRead:
    task = await session.scalar(select(MockTask).where(MockTask.external_id == external_id))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No task {external_id}.")
    return TaskRead.of(task)

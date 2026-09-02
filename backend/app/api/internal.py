"""The nightly sweep.

Everything else in Kept runs because a meeting happened. This runs because a
date passed, which is the failure mode meetings are worst at catching: nobody
convenes to notice that Tuesday's promise is now four days old.

Deliberately not a graph. There is no ambiguity to resolve and nothing to
route, so the work is a query, a risk computation that already exists, and one
Herald call per person who is late. An LLM supervisor here would add cost and
nondeterminism to a job whose steps are fully known.

Overdue is computed, never stored. `CommitmentStatus` has no `overdue` member
on purpose: the moment it were written down it would start going stale. So the
sweep does not mark anything overdue, it reports and nudges. What it writes is
drafts, and drafts are never sent.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.execution import draft_nudge
from app.deps import SessionDep, SettingsDep
from app.logging import get_logger
from app.models.base import CommitmentStatus, CommunicationKind
from app.models.domain import Commitment, Communication
from app.services.model_router import ModelRouter
from app.services.risk import score_commitment
from app.services.temporal import today_in

router = APIRouter(prefix="/internal", tags=["internal"])
log = get_logger(__name__)

# One meeting's worth of nudges at most. The sweep runs unattended on a free
# tier, and a workspace that has been neglected for a month should produce a
# report, not a hundred model calls.
MAX_OWNERS_NUDGED = 8


class Late(BaseModel):
    id: str
    text: str
    owner: str | None
    due_date: str
    days_late: int
    slip_count: int
    risk_band: str


class Sweep(BaseModel):
    today: str
    overdue: list[Late]
    unowned: int
    nudges_drafted: int
    owners_skipped: int


def _authorise(settings: SettingsDep, token: str | None) -> None:
    """Shared-secret auth, separate from the demo key.

    The sweep writes drafts and spends model budget on a schedule, so it is
    reachable by a cron job and by nobody else. When no token is configured the
    endpoint is closed rather than open: a job that cannot run is a missing
    feature, an endpoint anyone can trigger is a bill.
    """
    expected = settings.internal_job_token
    if expected is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No internal job token is set.")
    if token != expected.get_secret_value():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad internal job token.")


@router.post("/sweep", response_model=Sweep)
async def sweep(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_internal_token: Annotated[str | None, Header()] = None,
    dry_run: bool = False,
) -> Sweep:
    """Find what is late, and write one nudge per person who is late.

    Also the keep-warm ping. Render's free tier sleeps after fifteen minutes,
    and the nightly cron that runs this is what stops a reviewer's first
    request from being the one that pays the cold start.
    """
    _authorise(settings, x_internal_token)
    today = today_in(settings.default_timezone)

    open_items = (
        await session.scalars(
            select(Commitment)
            .options(selectinload(Commitment.owner))
            .where(
                Commitment.status.notin_([CommitmentStatus.DONE, CommitmentStatus.DROPPED]),
                Commitment.due_date.is_not(None),
                Commitment.due_date < today,
            )
            .order_by(Commitment.due_date)
        )
    ).all()

    late: list[Late] = []
    by_owner: dict[str, list[Commitment]] = defaultdict(list)
    unowned = 0

    for item in open_items:
        due = item.due_date
        if due is None:  # the query said otherwise, but the column is nullable
            continue

        risk = score_commitment(
            due_date=due,
            today=today,
            status=item.status.value,
            owner_id=item.owner_id,
            owner_confidence=item.owner_confidence,
            due_confidence=item.due_confidence,
            slip_count=item.slip_count,
            silence_streak=item.silence_streak,
            blocked=bool(item.blocked_by),
        )
        owner = item.owner.name if item.owner else None
        late.append(
            Late(
                id=str(item.id),
                text=item.text,
                owner=owner,
                due_date=due.isoformat(),
                days_late=(today - due).days,
                slip_count=item.slip_count,
                risk_band=risk.band,
            )
        )
        if owner is None:
            unowned += 1
        else:
            by_owner[owner].append(item)

    drafted = 0
    skipped = max(len(by_owner) - MAX_OWNERS_NUDGED, 0)

    if not dry_run:
        router_ = ModelRouter(settings)
        for owner, items in list(by_owner.items())[:MAX_OWNERS_NUDGED]:
            body = await draft_nudge(
                owner,
                [_line(item) for item in items],
                router=router_,
                settings=settings,
            )
            if body is None:
                continue
            session.add(
                Communication(
                    # The meeting the promise was last discussed in. A nudge is
                    # about work, and work came from somewhere.
                    meeting_id=items[0].last_seen_meeting_id or items[0].first_seen_meeting_id,
                    kind=CommunicationKind.OWNER_NUDGE,
                    recipient=owner,
                    subject=f"{len(items)} overdue with you",
                    body=body,
                )
            )
            drafted += 1

    log.info(
        "sweep.finished",
        overdue=len(late),
        unowned=unowned,
        drafted=drafted,
        dry_run=dry_run,
        origin=request.client.host if request.client else None,
    )
    return Sweep(
        today=today.isoformat(),
        overdue=late,
        unowned=unowned,
        nudges_drafted=drafted,
        owners_skipped=skipped,
    )


def _line(item: Commitment) -> str:
    """One overdue commitment, in the words the meeting used."""
    due = item.due_date.isoformat() if item.due_date else "no date"
    parts = [item.text, f"due {due}"]
    if item.slip_count:
        parts.append(f"already moved {item.slip_count} time{'s' if item.slip_count > 1 else ''}")
    if item.blocked_by:
        parts.append(f"blocked on {item.blocked_by}")
    return ", ".join(parts)

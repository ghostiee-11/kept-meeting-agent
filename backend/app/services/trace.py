"""Per-run flight recorder.

Every handoff, model call, and tool call lands here. It is what the swim-lane
console renders, what the ops panel bills against, and what makes the claim
"this is genuinely multi-agent" checkable rather than decorative.

State lives in a context variable rather than in graph state. Agents run in
parallel and in nested subgraphs, and threading a mutable accumulator through
every private state schema would put bookkeeping into contracts that should
only carry domain artifacts.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class TraceEntry:
    seq: int
    agent: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    def as_event(self) -> dict[str, Any]:
        """Shape sent to the browser over SSE."""
        return {
            "seq": self.seq,
            "agent": self.agent,
            "event": self.event,
            "payload": self.payload,
            "provider": self.provider,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "cost_usd": round(self.cost_usd, 6),
        }


class RunTrace:
    """Accumulates one run's activity, and optionally forwards it live.

    The listener is how the console gets its events. LangGraph's stream writer
    was the obvious route and does not work here: each agent is invoked as its
    own compiled graph, so `get_stream_writer` inside one resolves to that
    graph's run rather than the parent's, and the events go nowhere. The trace
    is already threaded through every agent by a context variable, so it is the
    honest place to tap.
    """

    def __init__(self, run_id: str, listener: Callable[[TraceEntry], None] | None = None) -> None:
        self.run_id = run_id
        self.entries: list[TraceEntry] = []
        self._sequence = count()
        self._listener = listener

    def record(self, agent: str, event: str, **fields: Any) -> TraceEntry:
        entry = TraceEntry(seq=next(self._sequence), agent=agent, event=event, **fields)
        self.entries.append(entry)
        if self._listener is not None:
            # Never let a failing consumer take down a run.
            with suppress(Exception):
                self._listener(entry)
        return entry

    @property
    def cost_usd(self) -> float:
        return sum(entry.cost_usd for entry in self.entries)

    @property
    def tokens(self) -> tuple[int, int]:
        return (
            sum(entry.tokens_in for entry in self.entries),
            sum(entry.tokens_out for entry in self.entries),
        )

    def by_agent(self) -> dict[str, dict[str, Any]]:
        """Per-agent totals, which is how you find the expensive one."""
        summary: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            bucket = summary.setdefault(
                entry.agent,
                {"calls": 0, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "cost_usd": 0.0},
            )
            bucket["calls"] += 1
            bucket["tokens_in"] += entry.tokens_in
            bucket["tokens_out"] += entry.tokens_out
            bucket["latency_ms"] += entry.latency_ms
            bucket["cost_usd"] = round(bucket["cost_usd"] + entry.cost_usd, 6)
        return summary


_CURRENT: ContextVar[RunTrace | None] = ContextVar("kept_run_trace", default=None)


def current_trace() -> RunTrace | None:
    return _CURRENT.get()


@contextmanager
def run_trace(
    run_id: str, listener: Callable[[TraceEntry], None] | None = None
) -> Iterator[RunTrace]:
    trace = RunTrace(run_id, listener)
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)
        entries, cost = len(trace.entries), trace.cost_usd
        log.info("trace.complete", run_id=run_id, entries=entries, cost_usd=round(cost, 6))


def record(agent: str, event: str, **fields: Any) -> TraceEntry | None:
    """Record against the active run, returning the entry, or None outside one.

    Silent when there is no run so agents stay callable from tests and the CLI
    without a trace having to be set up first.
    """
    if (trace := _CURRENT.get()) is not None:
        return trace.record(agent, event, **fields)
    return None


@contextmanager
def timed(agent: str, event: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Record an entry with the elapsed time, even when the body raises."""
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        yield extra
    finally:
        record(
            agent,
            event,
            latency_ms=int((time.perf_counter() - started) * 1000),
            **{**fields, **extra},
        )

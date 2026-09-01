"""Client for the task-management system.

The Operator agent is the only thing in Kept that writes to the outside world,
so every external-write risk lives behind this one class: timeouts, retries
with jittered backoff, a circuit breaker, and idempotency keys derived from the
commitment rather than generated per attempt.

Retries come from tenacity, which already gets full jitter right. The circuit
breaker is written here because tenacity does not do it: retrying is per-call,
but a breaker is state shared *across* calls, which is what stops a run from
spending its whole budget hammering a system that is plainly down.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.logging import get_logger

log = get_logger(__name__)

# 5xx and timeouts are worth retrying. A 4xx is a bug in our request and will
# fail identically every time, so retrying it only wastes the budget.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class TaskClientError(RuntimeError):
    """A task operation failed after exhausting retries."""


class CircuitOpenError(TaskClientError):
    """The breaker is open: the downstream system is failing, so we are not
    calling it. Raised without making a request."""


class RetryableStatusError(RuntimeError):
    """Internal: a response worth another attempt."""


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Trips after `failure_threshold` consecutive failures.

    After `reset_after_seconds` it half-opens and lets a single probe through:
    if that succeeds the circuit closes, if it fails the timer restarts. This
    is the standard shape, kept small because the fancy variants buy nothing
    at this scale.
    """

    failure_threshold: int = 5
    reset_after_seconds: float = 30.0

    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _half_open_in_flight: bool = field(default=False, init=False)

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if time.monotonic() - self._opened_at >= self.reset_after_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def before_call(self) -> None:
        state = self.state
        if state is BreakerState.OPEN:
            raise CircuitOpenError(
                f"Task API circuit is open after {self._failures} consecutive failures."
            )
        if state is BreakerState.HALF_OPEN:
            if self._half_open_in_flight:
                raise CircuitOpenError("Task API circuit is half-open; a probe is in flight.")
            self._half_open_in_flight = True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_in_flight = False

    def record_failure(self) -> None:
        self._failures += 1
        self._half_open_in_flight = False
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
            log.warning("task_client.circuit_opened", failures=self._failures)


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    status: str
    assignee: str | None
    due_date: date | None
    url: str

    @classmethod
    def of(cls, payload: dict[str, Any]) -> Task:
        raw_due = payload.get("due_date")
        return cls(
            id=payload["id"],
            title=payload["title"],
            status=payload["status"],
            assignee=payload.get("assignee"),
            due_date=date.fromisoformat(raw_due) if raw_due else None,
            url=payload.get("url", ""),
        )


def idempotency_key_for(commitment_id: uuid.UUID) -> str:
    """One commitment yields one task, however many times we retry.

    Derived from the commitment rather than generated per attempt: a fresh key
    on every retry would defeat the entire mechanism.
    """
    return f"commitment:{commitment_id}"


class TaskClient:
    """Async client for the task-management API."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_attempts: int = 3,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._max_attempts = max_attempts
        self.breaker = breaker or CircuitBreaker()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_task(
        self,
        *,
        title: str,
        commitment_id: uuid.UUID,
        description: str | None = None,
        assignee: str | None = None,
        due_date: date | None = None,
    ) -> Task:
        payload = {
            "title": title,
            "description": description,
            "assignee": assignee,
            "due_date": due_date.isoformat() if due_date else None,
            "source_commitment_id": str(commitment_id),
        }
        response = await self._request(
            "POST",
            "/mock/v1/tasks",
            json=payload,
            headers={"Idempotency-Key": idempotency_key_for(commitment_id)},
        )
        return Task.of(response.json())

    async def update_task(
        self,
        external_id: str,
        *,
        status: str | None = None,
        assignee: str | None = None,
        due_date: date | None = None,
    ) -> Task:
        payload: dict[str, Any] = {}
        if status is not None:
            payload["status"] = status
        if assignee is not None:
            payload["assignee"] = assignee
        if due_date is not None:
            payload["due_date"] = due_date.isoformat()

        response = await self._request("PATCH", f"/mock/v1/tasks/{external_id}", json=payload)
        return Task.of(response.json())

    async def find_task(self, external_id: str) -> Task | None:
        try:
            response = await self._request("GET", f"/mock/v1/tasks/{external_id}")
        except TaskClientError as exc:
            if "404" in str(exc):
                return None
            raise
        return Task.of(response.json())

    async def list_tasks(self, *, assignee: str | None = None) -> list[Task]:
        params = {"assignee": assignee} if assignee else None
        response = await self._request("GET", "/mock/v1/tasks", params=params)
        return [Task.of(item) for item in response.json()]

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self.breaker.before_call()
        url = f"{self._base_url}{path}"
        attempts = 0

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(initial=0.2, max=4.0),
                retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
                reraise=True,
            ):
                with attempt:
                    attempts += 1
                    response = await self._client.request(method, url, **kwargs)
                    if response.status_code in _RETRYABLE_STATUS:
                        raise RetryableStatusError(
                            f"{method} {path} returned {response.status_code}"
                        )
                    if response.is_error:
                        # Not retryable: fail fast and do not trip the breaker,
                        # because the downstream system is fine and we are not.
                        self.breaker.record_success()
                        raise TaskClientError(f"{method} {path} failed: {response.status_code}")
        except TaskClientError:
            raise
        except Exception as exc:
            self.breaker.record_failure()
            raise TaskClientError(
                f"{method} {path} failed after {attempts} attempts: {exc}"
            ) from exc

        self.breaker.record_success()
        if attempts > 1:
            log.info("task_client.recovered", method=method, path=path, attempts=attempts)
        return response

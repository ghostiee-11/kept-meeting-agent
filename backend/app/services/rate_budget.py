"""Staying inside a provider's tokens-per-minute budget.

Groq's free tier allows 8000 tokens a minute per model. Kept spends roughly
3500 on a single extraction brief, so two concurrent briefs fit and three do
not. Exceeding it does not produce a clean 429 either: Groq answers with an
empty generation that fails schema validation, so a budget problem arrives
looking exactly like a model problem.

Retries do not fix this. An immediate retry goes straight back into the same
exhausted window, and a longer backoff just makes the run slow *and* unreliable.
The fix is to not overspend in the first place.

This is a sliding-window token budget shared by every call to a given model.
Before a request, the caller declares roughly what it will cost; if that would
breach the window, it waits for the oldest spend to age out. Actual usage is
reconciled afterwards, so a bad estimate self-corrects within a minute.

ponytail: per-process, so it does not coordinate across instances. Correct for
the single free-tier worker this deploys to. A second worker would need Redis,
and would also need a paid tier to be worth having.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from app.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 60.0

# Aim below the real ceiling. Token estimates are approximate, other processes
# may share the account, and the cost of being slightly conservative is a short
# wait rather than a failed run.
SAFETY_FRACTION = 0.85

# Rough characters per token for English prose. Precise enough for pacing, and
# a real tokenizer here would be a dependency and a model download to decide
# whether to sleep for two seconds.
CHARS_PER_TOKEN = 4


@dataclass
class TokenBudget:
    """A sliding-window budget for one model."""

    limit_per_minute: int
    _spends: deque[tuple[float, int]] = field(default_factory=deque, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def _prune(self, now: float) -> None:
        while self._spends and now - self._spends[0][0] > WINDOW_SECONDS:
            self._spends.popleft()

    @property
    def spent(self) -> int:
        self._prune(time.monotonic())
        return sum(tokens for _, tokens in self._spends)

    async def reserve(self, estimated: int) -> float:
        """Wait until `estimated` tokens fit in the window, then record them.

        Returns how long it waited, so a caller can report the pause rather
        than looking mysteriously slow.
        """
        waited = 0.0
        ceiling = int(self.limit_per_minute * SAFETY_FRACTION)

        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                spent = sum(tokens for _, tokens in self._spends)

                if spent + estimated <= ceiling or not self._spends:
                    self._spends.append((now, estimated))
                    return waited

                # Sleep exactly as long as it takes the oldest spend to age
                # out, rather than polling on a fixed interval.
                oldest, _ = self._spends[0]
                pause = max(0.25, WINDOW_SECONDS - (now - oldest) + 0.25)
                log.info(
                    "rate_budget.waiting",
                    seconds=round(pause, 1),
                    spent=spent,
                    estimated=estimated,
                    ceiling=ceiling,
                )
                waited += pause
                await asyncio.sleep(pause)

    def reconcile(self, estimated: int, actual: int) -> None:
        """Replace an estimate with what the call really cost.

        Without this a systematically low estimate would keep the budget
        permanently over-optimistic, and the failures would come back.
        """
        if not self._spends:
            return
        for index in range(len(self._spends) - 1, -1, -1):
            timestamp, tokens = self._spends[index]
            if tokens == estimated:
                self._spends[index] = (timestamp, actual)
                return


class BudgetRegistry:
    """One budget per model, since the limits are per model."""

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._budgets: dict[str, TokenBudget] = {}

    def for_model(self, model: str) -> TokenBudget:
        if model not in self._budgets:
            self._budgets[model] = TokenBudget(self._limit)
        return self._budgets[model]


def estimate_tokens(text: str) -> int:
    """Rough input cost of a prompt, plus headroom for the response."""
    return len(text) // CHARS_PER_TOKEN + 600

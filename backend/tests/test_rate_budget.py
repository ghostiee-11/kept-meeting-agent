"""The token budget.

Pacing is the fix for the failure that produced a 0.00 recall in the first real
evaluation run: Groq answers an over-budget request with an empty generation
rather than a clean 429, so exceeding the limit looks like a broken model.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services.rate_budget import (
    SAFETY_FRACTION,
    BudgetRegistry,
    TokenBudget,
    estimate_tokens,
)


async def test_a_call_inside_the_budget_does_not_wait() -> None:
    budget = TokenBudget(limit_per_minute=8000)

    waited = await budget.reserve(3000)

    assert waited == 0.0
    assert budget.spent == 3000


async def test_spending_accumulates_across_calls() -> None:
    budget = TokenBudget(limit_per_minute=8000)

    await budget.reserve(3000)
    await budget.reserve(2000)

    assert budget.spent == 5000


async def test_a_call_that_would_breach_the_ceiling_waits() -> None:
    """The whole point. Two briefs fit in Groq's free tier and three do not,
    so the third has to wait rather than fail."""
    budget = TokenBudget(limit_per_minute=1000)
    await budget.reserve(800)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        # It would wait for the window to roll over, which is a minute. That it
        # blocks at all is the assertion; waiting out a real minute is not.
        await asyncio.wait_for(budget.reserve(800), timeout=0.6)

    assert time.monotonic() - started >= 0.5


async def test_the_ceiling_is_below_the_real_limit() -> None:
    """Estimates are approximate and other processes may share the account, so
    the budget aims under the line rather than at it."""
    budget = TokenBudget(limit_per_minute=1000)
    ceiling = int(1000 * SAFETY_FRACTION)

    await budget.reserve(ceiling)

    assert budget.spent == ceiling
    assert ceiling < 1000


async def test_a_first_call_larger_than_the_whole_budget_still_proceeds() -> None:
    """Otherwise a single oversized transcript would hang forever waiting for
    room that can never exist. Better to send it and let the provider decide."""
    budget = TokenBudget(limit_per_minute=1000)

    waited = await asyncio.wait_for(budget.reserve(50_000), timeout=1.0)

    assert waited == 0.0


async def test_reconciling_replaces_an_estimate_with_the_real_cost() -> None:
    """Without this a systematically low estimate keeps the budget permanently
    optimistic, and the failures come back."""
    budget = TokenBudget(limit_per_minute=8000)
    await budget.reserve(1000)

    budget.reconcile(1000, 4000)

    assert budget.spent == 4000


def test_budgets_are_per_model_because_the_limits_are() -> None:
    registry = BudgetRegistry(8000)

    assert registry.for_model("gpt-oss-120b") is not registry.for_model("gpt-oss-20b")
    assert registry.for_model("gpt-oss-120b") is registry.for_model("gpt-oss-120b")


def test_the_estimate_scales_with_the_prompt_and_leaves_room_for_a_reply() -> None:
    small = estimate_tokens("hello")
    large = estimate_tokens("word " * 4000)

    assert large > small
    assert small >= 600, "headroom for the response, not just the prompt"

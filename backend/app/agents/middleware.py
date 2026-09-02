"""The two middlewares LangChain does not already ship.

Everything else in the stack (fallback, call limits, PII redaction, approval
gates, summarisation) comes from `langchain.agents.middleware`. Reimplementing
those would be work with no payoff, so only genuinely missing behaviour lives
here:

`CostMeterMiddleware`   records provider, model, tokens, latency, and estimated
                        cost per call into the run trace, and streams it so the
                        console can show cost accruing live.

`GroundingMiddleware`   runs the Verifier over a structured response and, when
                        quotes cannot be found in the transcript, hands the
                        model one bounded chance to correct itself before the
                        items are dropped. Prompting for grounding is a
                        request; this is a gate.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from app.agents.contracts import Evidence, Grounded
from app.logging import get_logger
from app.services import trace
from app.services.model_router import ModelRouter
from app.services.verifier import verify_evidence

log = get_logger(__name__)


class CostMeterMiddleware(AgentMiddleware[Any, Any]):
    """Meter every model call this agent makes."""

    def __init__(self, agent: str, router: ModelRouter) -> None:
        super().__init__()
        self.agent = agent
        self._router = router

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        started = time.perf_counter()
        trace.record(self.agent, "model_call_started")

        try:
            response = await handler(request)
        except Exception as exc:
            trace.record(
                self.agent,
                "error",
                latency_ms=int((time.perf_counter() - started) * 1000),
                payload={"error": str(exc)[:500]},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        tokens_in, tokens_out, model_name = _usage_of(response)

        # The model actually used can differ from the one requested, because
        # ModelFallbackMiddleware may have moved down the chain. Reporting the
        # requested model would hide exactly the event worth seeing.
        identifier = _identifier_of(request, model_name)
        spec = self._router.spec_for(identifier)

        trace.record(
            self.agent,
            "model_call",
            provider=spec.provider.value,
            model=spec.model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=spec.cost(tokens_in, tokens_out),
        )
        return response


class GroundingMiddleware(AgentMiddleware[Any, Any]):
    """Reject structured output whose quotes are not in the transcript.

    On a failed check the model is given the specific quotes that could not be
    found and asked once more. One retry, not a loop: a model that cannot cite
    the transcript twice is not going to get there on the third attempt, and
    the budget is better spent elsewhere.
    """

    def __init__(self, agent: str, *, items_field: str = "items") -> None:
        super().__init__()
        self.agent = agent
        self._items_field = items_field

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        transcript = request.state.get("transcript")
        if not isinstance(transcript, str) or not transcript:
            return await handler(request)

        raw_turns = request.state.get("turn_texts")
        turns = raw_turns if isinstance(raw_turns, list) else None
        response = await handler(request)

        ungrounded = self._ungrounded_quotes(response, transcript, turns)
        if not ungrounded:
            return response

        trace.record(
            self.agent,
            "grounding_retry",
            payload={"quotes": ungrounded[:5], "count": len(ungrounded)},
        )

        quoted = "\n".join(f"  - {quote!r}" for quote in ungrounded[:10])
        correction = HumanMessage(
            content=(
                "These quotes do not appear in the transcript:\n"
                f"{quoted}\n\n"
                "Redo the extraction. Copy each quote character for character "
                "from the transcript. Drop any item you cannot support with a "
                "real quote rather than rewording one to fit."
            )
        )
        return await handler(replace(request, messages=[*request.messages, correction]))

    def _ungrounded_quotes(
        self, response: ModelResponse[Any], transcript: str, turns: list[str] | None
    ) -> list[str]:
        structured = getattr(response, "structured_response", None)
        if structured is None:
            return []

        items = getattr(structured, self._items_field, None)
        if items is None:
            items = [structured] if isinstance(structured, Grounded) else []

        missing: list[str] = []
        for item in items:
            evidence: list[Evidence] = getattr(item, "evidence", [])
            if not evidence:
                continue
            _, reasons = verify_evidence(transcript, evidence, turns=turns)
            missing.extend(_quote_from(reason) for reason in reasons)
        return [quote for quote in missing if quote]


def _usage_of(response: ModelResponse[Any]) -> tuple[int, int, str | None]:
    for message in reversed(getattr(response, "result", []) or []):
        usage = getattr(message, "usage_metadata", None)
        if usage:
            metadata = getattr(message, "response_metadata", {}) or {}
            return (
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                metadata.get("model_name") or metadata.get("model"),
            )
    return 0, 0, None


def _identifier_of(request: ModelRequest[Any], reported_model: str | None) -> str:
    """Best available `provider:model` for the call that actually happened."""
    model = request.model
    provider = getattr(model, "_llm_type", "") or ""
    name = reported_model or getattr(model, "model_name", None) or getattr(model, "model", "")

    prefix = (
        "groq"
        if "groq" in provider.lower()
        else "google_genai"
        if "google" in provider.lower() or "gemini" in str(name).lower()
        else "openai"
    )
    return f"{prefix}:{name}"


def _quote_from(reason: str) -> str:
    _, _, tail = reason.partition("transcript: ")
    return tail.strip().strip("'\"")

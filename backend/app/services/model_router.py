"""Model selection across Groq, Gemini, and OpenAI.

Three things this exists to do, none of which a bare ``init_chat_model`` call
would give us:

**Tiers.** Segmenting a transcript and adjudicating whether a sentence is a
commitment are not the same job and should not pay the same price. Each tier
carries an ordered chain, and the agent factory hands that chain to
``ModelFallbackMiddleware`` so a free-tier 429 degrades instead of failing.

**Provider diversity for the Skeptic.** Reviewing is only worth a second call
if the reviewer can disagree. A model grading its own output agrees with itself,
so the Skeptic tier is deliberately ordered to land on a different provider
from whatever the Analyst resolved to.

**Model identifiers that rot.** Groq retires models on a rolling schedule, and
the identifiers this project was planned around (``llama-3.3-70b-versatile``)
turned out not to exist on the account it runs under. Every identifier is
overridable by environment variable and checked against the provider's own
model list at boot, so a retirement surfaces on ``/health`` rather than as a
500 in the middle of a demo.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.logging import get_logger

log = get_logger(__name__)


class Tier(StrEnum):
    FAST = "fast"
    """Scribe, Operator, Herald. High volume, low reasoning."""

    REASON = "reason"
    """Chief of Staff, Analyst, Attributor, Chronos, Researcher, Historian."""

    SKEPTIC = "skeptic"
    """Adversarial review, pinned away from the Analyst's provider."""

    JUDGE = "judge"
    """Evaluation only, and never the model that produced the output."""

    GUARD = "guard"
    """Prompt-injection classification. A tiny classifier, not a chat model."""


class Provider(StrEnum):
    GROQ = "groq"
    GOOGLE = "google"
    OPENAI = "openai"


# LangChain's provider prefixes differ from our internal names.
_LANGCHAIN_PROVIDER = {
    Provider.GROQ: "groq",
    Provider.GOOGLE: "google_genai",
    Provider.OPENAI: "openai",
}


@dataclass(frozen=True)
class ModelSpec:
    provider: Provider
    model_id: str

    structured_output: str = "json_schema"
    """Measured, not assumed. On Groq's gpt-oss models `json_schema` recovered
    both items from a two-item transcript while `function_calling` found one,
    so the stricter method is the default and the weaker one is the fallback."""

    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    context_window: int = 131_072

    @property
    def identifier(self) -> str:
        return f"{_LANGCHAIN_PROVIDER[self.provider]}:{self.model_id}"

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (
            tokens_in * self.input_cost_per_mtok + tokens_out * self.output_cost_per_mtok
        ) / 1_000_000


# Published list prices, used for reporting a run's cost rather than billing.
# Free tiers make the real number zero; the estimate is what tells you which
# agent is expensive.
REGISTRY: dict[str, ModelSpec] = {
    spec.identifier: spec
    for spec in [
        ModelSpec(Provider.GROQ, "openai/gpt-oss-20b", "json_schema", 0.075, 0.30),
        ModelSpec(Provider.GROQ, "openai/gpt-oss-120b", "json_schema", 0.15, 0.60),
        ModelSpec(Provider.GROQ, "qwen/qwen3.8-27b", "function_calling", 0.10, 0.30),
        ModelSpec(Provider.GROQ, "meta-llama/llama-prompt-guard-2-86m", "none", 0.0, 0.0, 512),
        # Confirmed against the account's own /v1beta/models listing: the
        # generally-available "gemini-3-flash" name does not exist there, only
        # the preview identifier does. The same rot the Groq registry guards
        # against, caught the same way, by checking rather than assuming.
        ModelSpec(Provider.GOOGLE, "gemini-3-flash-preview", "json_schema", 0.30, 2.50, 1_048_576),
        ModelSpec(Provider.GOOGLE, "gemini-2.5-flash-lite", "json_schema", 0.10, 0.40, 1_048_576),
        ModelSpec(Provider.OPENAI, "gpt-5.5-mini", "json_schema", 0.25, 2.00, 400_000),
        ModelSpec(Provider.OPENAI, "gpt-5.5", "json_schema", 1.25, 10.00, 400_000),
    ]
}

# Preference order per tier. Filtered at boot to whatever has credentials.
TIER_CHAINS: dict[Tier, list[str]] = {
    Tier.FAST: [
        "groq:openai/gpt-oss-20b",
        "google_genai:gemini-2.5-flash-lite",
        "openai:gpt-5.5-mini",
    ],
    Tier.REASON: [
        "groq:openai/gpt-oss-120b",
        "google_genai:gemini-3-flash-preview",
        "openai:gpt-5.5-mini",
        # Same provider, smaller model, as a last resort. Groq's token-per-minute
        # limits are per model, so stepping down within Groq genuinely relieves
        # pressure rather than hitting the same ceiling again. It only matters
        # when no second provider is configured, which is exactly when a 429
        # would otherwise be fatal.
        "groq:openai/gpt-oss-20b",
    ],
    Tier.SKEPTIC: [
        "google_genai:gemini-3-flash-preview",
        "openai:gpt-5.5-mini",
        "groq:qwen/qwen3.8-27b",
        "groq:openai/gpt-oss-120b",
    ],
    Tier.JUDGE: ["openai:gpt-5.5", "google_genai:gemini-3-flash-preview"],
    Tier.GUARD: ["groq:meta-llama/llama-prompt-guard-2-86m"],
}

_MODELS_ENDPOINT = {
    Provider.GROQ: "https://api.groq.com/openai/v1/models",
    Provider.OPENAI: "https://api.openai.com/v1/models",
}


class NoModelAvailableError(RuntimeError):
    """No configured provider can serve this tier."""


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._groq_keys = [
            key for key in (settings.groq_api_key, settings.groq_api_key_2) if key is not None
        ]
        # A fresh ModelSpec is resolved to actual credentials once per agent
        # call (see build_spec), so a simple round-robin here lands on
        # consecutive calls rather than needing to be threaded through
        # anything else. `itertools.count` rather than `% len` at each call so
        # the counter itself never needs guarding when the key list is empty.
        self._groq_key_rotor = itertools.count()

        self._available = {
            Provider.GROQ: bool(self._groq_keys),
            Provider.GOOGLE: settings.google_api_key is not None,
            Provider.OPENAI: settings.openai_api_key is not None,
        }
        self._overrides = {
            Tier.FAST: settings.model_fast,
            Tier.REASON: settings.model_reason,
            Tier.SKEPTIC: settings.model_skeptic,
            Tier.JUDGE: settings.model_judge,
        }
        self.validation: dict[str, Any] = {}

    def chain(self, tier: Tier) -> list[ModelSpec]:
        """Ordered, credential-filtered candidates for a tier."""
        identifiers = list(TIER_CHAINS[tier])

        if override := self._overrides.get(tier):
            identifiers = [override, *(i for i in identifiers if i != override)]

        specs = [
            REGISTRY.get(identifier) or _unknown_spec(identifier) for identifier in identifiers
        ]
        usable = [spec for spec in specs if self._available[spec.provider]]

        if tier is Tier.SKEPTIC:
            usable = self._prefer_a_different_provider(usable)

        if not usable:
            raise NoModelAvailableError(
                f"No credentials for any {tier} model. Configure at least one of "
                f"GROQ_API_KEY, GOOGLE_API_KEY, or OPENAI_API_KEY."
            )
        return usable

    def _prefer_a_different_provider(self, candidates: list[ModelSpec]) -> list[ModelSpec]:
        """Push the Analyst's provider to the back of the Skeptic's chain.

        Review is only worth paying for if the reviewer can disagree, and the
        same model on the same provider mostly ratifies its own reasoning. When
        only one provider has credentials this degrades to same-provider review
        rather than failing, and `describe()` reports that it did.
        """
        try:
            analyst_provider = self.chain(Tier.REASON)[0].provider
        except NoModelAvailableError:
            return candidates

        different = [spec for spec in candidates if spec.provider is not analyst_provider]
        same = [spec for spec in candidates if spec.provider is analyst_provider]
        return [*different, *same]

    def primary(self, tier: Tier) -> ModelSpec:
        return self.chain(tier)[0]

    def identifiers(self, tier: Tier) -> list[str]:
        """Chain in the form `ModelFallbackMiddleware` expects."""
        return [spec.identifier for spec in self.chain(tier)]

    def build(self, tier: Tier, *, temperature: float = 0.0, **kwargs: Any) -> BaseChatModel:
        spec = self.primary(tier)
        return self.build_spec(spec, temperature=temperature, **kwargs)

    def _api_key(self, provider: Provider) -> str:
        if provider is Provider.GROQ:
            if not self._groq_keys:
                raise NoModelAvailableError("No API key configured for groq.")
            # Round-robin. Confirmed independently rate-limited (separate
            # x-ratelimit-remaining counts and, critically, separate daily
            # caps), so alternating calls genuinely doubles throughput rather
            # than nominally splitting one shared ceiling.
            key = self._groq_keys[next(self._groq_key_rotor) % len(self._groq_keys)]
            return key.get_secret_value()

        secret = {
            Provider.GOOGLE: self._settings.google_api_key,
            Provider.OPENAI: self._settings.openai_api_key,
        }[provider]
        if secret is None:
            raise NoModelAvailableError(f"No API key configured for {provider.value}.")
        return secret.get_secret_value()

    def build_spec(
        self, spec: ModelSpec, *, temperature: float = 0.0, **kwargs: Any
    ) -> BaseChatModel:
        # The key is passed explicitly rather than exported to os.environ.
        # Provider SDKs read the process environment by default, and settings
        # come from a .env file that pydantic-settings deliberately does not
        # leak into it. Mutating global state to bridge that gap would make
        # which credentials a call used depend on import order.
        model: BaseChatModel = init_chat_model(
            spec.model_id,
            model_provider=_LANGCHAIN_PROVIDER[spec.provider],
            temperature=temperature,
            api_key=self._api_key(spec.provider),
            **kwargs,
        )
        return model

    def spec_for(self, identifier: str) -> ModelSpec:
        return REGISTRY.get(identifier) or _unknown_spec(identifier)

    async def validate(self, *, timeout: float = 6.0) -> dict[str, Any]:
        """Check every configured identifier against the provider's model list.

        Cheap insurance against the failure that actually happened during this
        build: a planned model identifier that the account cannot serve. Results
        are advisory, and a provider that does not answer is reported as
        unchecked rather than treated as broken.
        """
        wanted: dict[Provider, set[str]] = {}
        for tier in Tier:
            try:
                for spec in self.chain(tier):
                    wanted.setdefault(spec.provider, set()).add(spec.model_id)
            except NoModelAvailableError:
                continue

        results = await asyncio.gather(
            *(self._list_models(provider, timeout) for provider in wanted),
            return_exceptions=True,
        )

        report: dict[str, Any] = {"missing": [], "unchecked": []}
        for provider, available in zip(wanted, results, strict=True):
            if isinstance(available, BaseException) or available is None:
                report["unchecked"].append(provider.value)
                continue
            missing = sorted(wanted[provider] - available)
            report["missing"].extend(f"{provider.value}:{model}" for model in missing)

        if report["missing"]:
            log.warning("model_router.identifiers_missing", missing=report["missing"])

        self.validation = report
        return report

    async def _list_models(self, provider: Provider, timeout: float) -> set[str] | None:
        endpoint = _MODELS_ENDPOINT.get(provider)
        if endpoint is None:
            # Gemini's list endpoint names models differently enough that a
            # comparison would produce false alarms. Left unchecked on purpose.
            return None

        key = {
            Provider.GROQ: self._settings.groq_api_key,
            Provider.OPENAI: self._settings.openai_api_key,
        }[provider]
        if key is None:
            return None

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                endpoint, headers={"Authorization": f"Bearer {key.get_secret_value()}"}
            )
            response.raise_for_status()
            return {item["id"] for item in response.json().get("data", [])}

    def describe(self) -> dict[str, Any]:
        """What the router resolved to, for `/health` and the ops panel."""
        summary: dict[str, Any] = {"tiers": {}, "validation": self.validation}
        for tier in Tier:
            try:
                chain = self.chain(tier)
            except NoModelAvailableError:
                summary["tiers"][tier.value] = None
                continue
            summary["tiers"][tier.value] = {
                "primary": chain[0].identifier,
                "fallbacks": [spec.identifier for spec in chain[1:]],
                "structured_output": chain[0].structured_output,
            }

        reason = summary["tiers"].get(Tier.REASON.value)
        skeptic = summary["tiers"].get(Tier.SKEPTIC.value)
        summary["independent_review"] = bool(
            reason
            and skeptic
            and self.spec_for(reason["primary"]).provider
            is not self.spec_for(skeptic["primary"]).provider
        )
        return summary


def _unknown_spec(identifier: str) -> ModelSpec:
    """An env-overridden identifier we have no pricing or capability data for."""
    provider_prefix, _, model_id = identifier.partition(":")
    provider = next(
        (p for p, prefix in _LANGCHAIN_PROVIDER.items() if prefix == provider_prefix),
        Provider.GROQ,
    )
    return ModelSpec(provider, model_id, structured_output="function_calling")

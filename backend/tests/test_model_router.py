from __future__ import annotations

import pytest

from app.config import Settings
from app.services.model_router import (
    ModelRouter,
    NoModelAvailableError,
    Provider,
    Tier,
)


def router(**env: str) -> ModelRouter:
    return ModelRouter(Settings(**env))  # type: ignore[arg-type]


def test_chains_only_include_providers_that_have_credentials() -> None:
    only_groq = router(groq_api_key="k")

    for spec in only_groq.chain(Tier.REASON):
        assert spec.provider is Provider.GROQ


def test_a_tier_with_no_credentials_says_what_to_configure() -> None:
    with pytest.raises(NoModelAvailableError, match="GROQ_API_KEY"):
        router(google_api_key="k").chain(Tier.GUARD)


def test_skeptic_lands_on_a_different_provider_from_the_analyst() -> None:
    """Review is only worth a second call if the reviewer can disagree. A model
    grading its own output agrees with itself."""
    both = router(groq_api_key="k", google_api_key="k")

    assert both.primary(Tier.REASON).provider is not both.primary(Tier.SKEPTIC).provider
    assert both.describe()["independent_review"] is True


def test_single_provider_degrades_to_same_provider_review_and_says_so() -> None:
    """Better to review with the same provider than not review at all, but the
    weakened guarantee has to be visible rather than silent."""
    only_groq = router(groq_api_key="k")

    assert only_groq.primary(Tier.SKEPTIC).provider is Provider.GROQ
    assert only_groq.describe()["independent_review"] is False


def test_an_override_takes_the_front_of_the_chain_without_dropping_fallbacks() -> None:
    overridden = router(
        groq_api_key="k",
        google_api_key="k",
        model_reason="google_genai:gemini-3-flash",
    )
    chain = overridden.identifiers(Tier.REASON)

    assert chain[0] == "google_genai:gemini-3-flash"
    assert "groq:openai/gpt-oss-120b" in chain
    assert len(chain) == len(set(chain)), "override must not duplicate an entry"


def test_an_unknown_override_still_resolves_and_assumes_the_weaker_method() -> None:
    """A model we have no capability data for might not support strict schemas,
    so assume the method that works everywhere rather than the fast one."""
    exotic = router(groq_api_key="k", model_fast="groq:some-new-model")
    spec = exotic.primary(Tier.FAST)

    assert spec.model_id == "some-new-model"
    assert spec.structured_output == "function_calling"


def test_cost_is_reported_per_million_tokens() -> None:
    spec = router(groq_api_key="k").primary(Tier.REASON)

    assert spec.cost(1_000_000, 0) == pytest.approx(spec.input_cost_per_mtok)
    assert spec.cost(0, 0) == 0.0


async def test_validate_flags_an_identifier_the_provider_cannot_serve() -> None:
    """The failure this guards against actually happened during the build:
    llama-3.3-70b-versatile was planned for and does not exist on the account."""
    checked = router(groq_api_key="k", model_fast="groq:llama-3.3-70b-versatile")

    async def only_gpt_oss(provider, timeout):  # type: ignore[no-untyped-def]
        return {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}

    checked._list_models = only_gpt_oss  # type: ignore[method-assign]
    report = await checked.validate()

    assert "groq:llama-3.3-70b-versatile" in report["missing"]


async def test_a_provider_that_does_not_answer_is_unchecked_not_broken() -> None:
    flaky = router(groq_api_key="k")

    async def unreachable(provider, timeout):  # type: ignore[no-untyped-def]
        raise TimeoutError("provider is down")

    flaky._list_models = unreachable  # type: ignore[method-assign]
    report = await flaky.validate()

    assert report["missing"] == []
    assert "groq" in report["unchecked"]

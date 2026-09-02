"""Application settings.

Every knob the system has is declared here and sourced from the environment, so a
deployment never depends on a value hardcoded somewhere in a module. Secrets use
``SecretStr`` so an accidental log or error response cannot leak them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application -------------------------------------------------------
    app_env: Environment = "local"
    log_level: str = "INFO"
    git_sha: str = "dev"
    """Set by the deploy platform. Render exposes RENDER_GIT_COMMIT."""

    # ---- API surface -------------------------------------------------------
    demo_key: SecretStr | None = None
    """Shared key required by write endpoints. Unset means auth is disabled,
    which is only acceptable locally; ``/health`` reports the difference."""

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    """NoDecode stops pydantic-settings JSON-parsing the raw env value, so the
    validator below can accept a plain comma-separated string instead."""
    max_transcript_bytes: int = 200_000
    rate_limit_per_minute: int = 20
    daily_run_quota: int = 200

    # ---- Persistence -------------------------------------------------------
    database_url: SecretStr | None = None
    database_url_unpooled: SecretStr | None = None
    """Neon's direct endpoint. Migrations use it because pgbouncer in
    transaction mode does not support the prepared statements Alembic issues."""

    db_pool_size: int = 3
    """Render free tier is 0.1 CPU / 512MB and Neon free caps connections.
    A small pool is deliberate, not an oversight."""
    db_max_overflow: int = 2

    # ---- Model providers ---------------------------------------------------
    groq_api_key: SecretStr | None = None
    groq_api_key_2: SecretStr | None = None
    """A second Groq account, optional. Confirmed by its own rate-limit headers
    to be billed independently from the first: separate x-ratelimit-remaining
    counts, and critically a separate daily token cap, which is the ceiling
    that actually stopped a run in testing (the per-minute one is already paced
    around). The router alternates between whichever keys are configured, which
    is a real doubling of throughput rather than a nominal one."""

    google_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # Per-tier model overrides, as `provider:model-id`. Groq retires models on
    # a rolling schedule, so every identifier has to be swappable without a
    # deploy. Unset means the built-in preference chain applies.
    model_fast: str | None = None
    model_reason: str | None = None
    model_skeptic: str | None = None
    model_judge: str | None = None

    # ---- Web search --------------------------------------------------------
    tavily_api_key: SecretStr | None = None
    max_searches_per_run: int = 4
    search_cache_ttl_seconds: int = 86_400

    # ---- Agent budgets -----------------------------------------------------
    max_model_calls_per_run: int = 60
    max_supervisor_steps: int = 20

    tokens_per_minute: int = 8000
    """The provider's per-model tokens-per-minute ceiling. Groq's free tier is
    8000. Calls are paced to stay under it rather than retried after breaching
    it, because exceeding it returns an empty generation rather than a clean
    429, so a budget problem arrives looking like a model problem."""

    analyst_concurrency: int = 2
    """How many extraction briefs run at once.

    Groq's free tier allows 8000 tokens per minute per model, and each brief is
    roughly 3500. Three at once exceeds that, and Groq reports the overrun as an
    empty generation rather than a clean 429, which looks like a model failure
    rather than a budget one. Two fits. Raise it on a paid tier, where the
    concurrency is the point of splitting the briefs in the first place."""

    # ---- Mock task API -----------------------------------------------------
    self_base_url: str = "http://127.0.0.1:8000"
    """Where the mock task API lives. The Operator calls it over real HTTP
    rather than in process, so timeouts, retries, and idempotency are exercised
    for real. On Render this is the service's own external URL."""

    mock_failure_rate: float = 0.0
    """Fraction of mock task API calls that fail, so retry and circuit-breaker
    behaviour is demonstrable rather than merely claimed."""

    mock_failure_mode: Literal["pre", "post", "random"] = "pre"
    """`pre` fails before any work, so a plain retry recovers. `post` commits
    and then fails, which is what a real API does when it times out on the way
    back: only an idempotency key prevents a duplicate task."""

    mock_fail_first_n: int = 0
    """Fail exactly the first N write attempts, then succeed. A probability is
    right for a demo and wrong for a test: a 50% failure rate with three
    attempts passes seven times in eight, which is a flaky test rather than a
    coverage of retries."""

    mock_latency_ms: int = 0

    # ---- Internal jobs -----------------------------------------------------
    internal_job_token: SecretStr | None = None
    """Shared secret for the nightly sweep. Unset closes the endpoint."""

    default_timezone: str = "Asia/Kolkata"
    """Whose today. Overdue is a question about a calendar, and a workspace in
    Gurugram is a day ahead of a server in Oregon for several hours every
    evening. Per-meeting timezones are stored and used where a meeting is in
    hand; this is the fallback for the views and jobs that span all of them."""

    # ---- Optional tracing --------------------------------------------------
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "kept"

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """An empty env var means "not configured", not "configured as empty".

        `.env.example` lists every key with a blank value, so without this a
        copied file would report all three model providers as available and the
        router would try to call one with no credentials.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so platform env vars stay readable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("mock_failure_rate")
    @classmethod
    def _check_failure_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("mock_failure_rate must be between 0.0 and 1.0")
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def configured_providers(self) -> dict[str, bool]:
        """Which model providers have credentials. Booleans only, never values."""
        return {
            "groq": self.groq_api_key is not None,
            "google": self.google_api_key is not None,
            "openai": self.openai_api_key is not None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

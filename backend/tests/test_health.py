from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_health_reports_configured_provider(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["providers"] == {"groq": True, "google": False, "openai": False}


def test_health_is_degraded_without_any_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    for key in ("GROQ_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    with TestClient(create_app()) as test_client:
        body = test_client.get("/health").json()

    assert body["status"] == "degraded"
    assert any("No model provider" in note for note in body["notes"])
    get_settings.cache_clear()


def test_health_never_leaks_secret_values(client: TestClient) -> None:
    """A key is reported as present, never echoed. This is the whole point of
    reporting booleans, so it gets a test rather than a comment."""
    assert "test-key" not in client.get("/health").text


def test_production_without_demo_key_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("DEMO_KEY", raising=False)

    with TestClient(create_app()) as test_client:
        body = test_client.get("/health").json()

    assert body["auth_enforced"] is False
    assert any("unprotected" in note for note in body["notes"])
    get_settings.cache_clear()


def test_cors_origins_accept_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.vercel.app, https://b.vercel.app")
    get_settings.cache_clear()

    assert Settings().cors_origins == ["https://a.vercel.app", "https://b.vercel.app"]
    get_settings.cache_clear()

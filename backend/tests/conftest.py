from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import Settings, get_settings

# Tests must not inherit the developer's backend/.env. Without this, a local
# key file silently changes what the suite asserts, and CI and a laptop
# disagree about whether a test passes.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def clean_settings() -> Iterator[None]:
    """Give every test a fresh Settings instance built only from its own env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

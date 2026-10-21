"""Settings validation: production fails fast on missing or weak secrets."""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_LLM_CHAIN, Settings

STRONG_SECRET = "s" * 48
DB_URL = "postgresql://user:pass@localhost/db"


def test_development_defaults() -> None:
    settings = Settings(_env_file=None, app_env="development")
    assert not settings.is_production
    assert settings.llm_chain == DEFAULT_LLM_CHAIN
    assert settings.ai_rate_limit_per_10min == 20


def test_production_requires_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, app_env="production", session_secret=STRONG_SECRET)


def test_production_requires_session_secret() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(_env_file=None, app_env="production", database_url=DB_URL)


def test_production_rejects_short_session_secret() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(_env_file=None, app_env="production", database_url=DB_URL, session_secret="short")


def test_production_accepts_complete_settings() -> None:
    settings = Settings(
        _env_file=None, app_env="production", database_url=DB_URL, session_secret=STRONG_SECRET
    )
    assert settings.is_production


@pytest.mark.parametrize("value", [0, -1, 1001])
def test_rate_limit_bounds(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_rate_limit_per_10min=value)


def test_secrets_are_masked_in_repr() -> None:
    settings = Settings(_env_file=None, gemini_api_key="real-key-value")
    assert "real-key-value" not in repr(settings)

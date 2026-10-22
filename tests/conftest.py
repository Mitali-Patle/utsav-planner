"""Shared test fixtures and the Hypothesis profiles."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from hypothesis import settings as hypothesis_settings
from sqlalchemy import Engine

from app.config import Settings
from app.main import create_app
from app.persistence.db import make_engine

# "ci" is the default: reproducible (derandomized) so local runs and CI never flake.
# Use HYPOTHESIS_PROFILE=explore for a long randomized search.
hypothesis_settings.register_profile("ci", derandomize=True, max_examples=300, deadline=None)
hypothesis_settings.register_profile("explore", max_examples=5_000, deadline=None)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    yield engine
    engine.dispose()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    """App with no database configured."""
    return TestClient(create_app(settings))


@pytest.fixture
def db_client(settings: Settings, sqlite_engine: Engine) -> TestClient:
    """App backed by an in-memory SQLite database."""
    return TestClient(create_app(settings, engine=sqlite_engine))

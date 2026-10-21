"""Shared test fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import Settings
from app.main import create_app
from app.persistence.db import make_engine


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

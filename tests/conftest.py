"""Shared test fixtures."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))

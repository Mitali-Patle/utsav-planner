"""Fixtures for the integration tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from app.persistence.db import make_engine
from app.persistence.tables import metadata


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()

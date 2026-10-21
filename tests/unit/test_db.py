"""Engine construction and connectivity checks."""

import pytest
from sqlalchemy import Engine
from sqlalchemy.pool import NullPool, StaticPool

from app.persistence.db import is_database_reachable, make_engine, normalize_database_url

UNREACHABLE_URL = "postgresql://user:secretpass@127.0.0.1:1/db?connect_timeout=2"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_normalize_database_url(url: str, expected: str) -> None:
    assert normalize_database_url(url) == expected


def test_postgres_engine_uses_null_pool_and_psycopg() -> None:
    engine = make_engine("postgresql://u:p@host/db")
    assert isinstance(engine.pool, NullPool)
    assert engine.dialect.driver == "psycopg"


def test_sqlite_engine_shares_one_connection(sqlite_engine: Engine) -> None:
    assert isinstance(sqlite_engine.pool, StaticPool)
    assert is_database_reachable(sqlite_engine)


def test_unreachable_database_is_reported_without_url(caplog: pytest.LogCaptureFixture) -> None:
    engine = make_engine(UNREACHABLE_URL)
    assert not is_database_reachable(engine)
    assert "secretpass" not in caplog.text
    assert "127.0.0.1" not in caplog.text

"""Database engine construction for Neon Postgres (production) and SQLite (tests)."""

import logging

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool, StaticPool

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10
_POSTGRES_PREFIXES = ("postgres://", "postgresql://")
_PSYCOPG_SCHEME = "postgresql+psycopg://"


def normalize_database_url(url: str) -> str:
    """Point plain Postgres URLs at the psycopg 3 driver.

    Neon hands out `postgresql://` URLs, which SQLAlchemy would otherwise map to psycopg2.

    Args:
        url: Database URL as configured.

    Returns:
        The URL with an explicit `postgresql+psycopg` scheme, or unchanged if not Postgres.
    """
    for prefix in _POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return _PSYCOPG_SCHEME + url.removeprefix(prefix)
    return url


def make_engine(url: str) -> Engine:
    """Create an engine suited to serverless functions.

    Postgres uses `NullPool`: each request opens a short-lived connection through Neon's
    pooler, so idle function instances hold no connections. SQLite (tests only) shares one
    connection so that an in-memory database is visible to every session.

    Args:
        url: Database URL.

    Returns:
        A configured SQLAlchemy engine. No connection is opened until first use.
    """
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        return create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    return create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    )


def is_database_reachable(engine: Engine) -> bool:
    """Check connectivity with a trivial query.

    Args:
        engine: Engine to check.

    Returns:
        True if `SELECT 1` succeeds, False otherwise. Errors are logged by type only,
        never with the connection URL.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database readiness check failed: %s", type(exc).__name__)
        return False
    return True

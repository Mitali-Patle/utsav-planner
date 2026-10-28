"""Helpers shared by the stores: time in UTC, hashed owners and dialect-aware upserts."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, Table
from sqlalchemy.dialects import postgresql, sqlite

Now = Callable[[], datetime]
"""A source of the current time; tests pass a fixed one."""


def utc_now() -> datetime:
    """Current time in UTC."""
    return datetime.now(UTC)


def owner_key(session_id: str) -> str:
    """Hash a session id before storing it, so a database leak cannot hijack sessions.

    Examples:
        >>> len(owner_key("session-123"))
        64
        >>> owner_key("a") == owner_key("a") != owner_key("b")
        True
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def as_utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; values are always stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def upsert(
    engine: Engine,
    table: Table,
    values: Mapping[str, Any],
    *,
    keys: Sequence[str],
    increment: str | None = None,
) -> postgresql.Insert | sqlite.Insert:
    """Insert, or on conflict update (or increment one column), in the engine's dialect."""
    statement: postgresql.Insert | sqlite.Insert = (
        postgresql.insert(table) if engine.dialect.name == "postgresql" else sqlite.insert(table)
    ).values(**values)
    if increment is not None:
        updates = {increment: table.c[increment] + 1}
    else:
        updates = {name: statement.excluded[name] for name in values if name not in keys}
    return statement.on_conflict_do_update(index_elements=list(keys), set_=updates)

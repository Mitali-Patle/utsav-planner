"""Repositories: the only code that reads and writes tables.

Ownership and versions are enforced inside the SQL statements themselves, so a request can
never read another session's event or overwrite a newer version of a plan. Event and
proposal repositories run inside one transaction per request (`Database.transaction`); the
AI cache and rate limiter use their own short connections, because they are used while an
AI call is in progress and a transaction must never stay open during one.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine

from app.persistence.common import Now, utc_now
from app.persistence.events import EventRepository
from app.persistence.proposals import ProposalRepository


@dataclass(frozen=True)
class Repositories:
    """Repositories sharing one connection and transaction."""

    events: EventRepository
    proposals: ProposalRepository


class Database:
    """Opens one connection and one transaction per unit of work.

    Everything done inside `transaction()` commits together, or rolls back together on any
    error, so (for example) saving a new plan version and marking its proposal approved can
    never half-happen. The connection is closed afterwards: nothing stays open to keep the
    serverless database awake.
    """

    def __init__(self, engine: Engine, now: Now = utc_now) -> None:
        """Create the unit-of-work factory."""
        self._engine = engine
        self._now = now

    @contextmanager
    def transaction(self) -> Iterator[Repositories]:
        """Repositories bound to a single new transaction."""
        with self._engine.begin() as connection:
            yield Repositories(
                EventRepository(connection, self._now), ProposalRepository(connection, self._now)
            )

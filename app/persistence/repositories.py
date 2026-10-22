"""Repositories: the only code that reads and writes tables.

Ownership and versions are enforced inside the SQL statements themselves, so a request can
never read another session's event or overwrite a newer version of a plan. Event and
proposal repositories run inside one transaction per request (`Database.transaction`); the
AI cache and rate limiter use their own short connections, because they are used while an
AI call is in progress and a transaction must never stay open during one.
"""

import hashlib
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, Engine, Table, delete, func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite

from app.domain.changes import Proposal
from app.domain.errors import NotFoundError, StaleProposalError
from app.domain.models import EventPlan
from app.persistence.tables import ai_cache, events, proposals, rate_limits

Now = Callable[[], datetime]
PURGE_EVERY_WRITES = 50
RATE_LIMIT_RETENTION = timedelta(days=2)


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


def _as_utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; values are always stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class StoredEvent:
    """An event as saved: its plan, version and AI personalization record."""

    id: str
    version: int
    plan: EventPlan
    ai: Mapping[str, Any] | None
    updated_at: datetime


class EventRepository:
    """Events, always scoped to their owner's session.

    Works inside a caller's transaction (see `Database.transaction`), so several reads and
    writes share one connection and commit or roll back together.
    """

    def __init__(self, connection: Connection, now: Now = utc_now) -> None:
        """Create the repository.

        Args:
            connection: Connection with an open transaction.
            now: Clock (injectable for tests).
        """
        self._connection = connection
        self._now = now

    def create(
        self, session_id: str, plan: EventPlan, ai: Mapping[str, Any] | None = None
    ) -> StoredEvent:
        """Save a new event at the plan's version.

        Returns:
            The stored event.
        """
        event_id = uuid.uuid4().hex
        moment = self._now()
        self._connection.execute(
            insert(events).values(
                id=event_id,
                owner=owner_key(session_id),
                version=plan.version,
                plan=plan.model_dump(mode="json"),
                ai=dict(ai) if ai is not None else None,
                created_at=moment,
                updated_at=moment,
            )
        )
        return StoredEvent(event_id, plan.version, plan, ai, moment)

    def get(self, event_id: str, session_id: str) -> StoredEvent:
        """Load an event owned by this session.

        Raises:
            NotFoundError: If it does not exist or belongs to another session.
        """
        row = self._connection.execute(
            select(*_EVENT_COLUMNS).where(
                events.c.id == event_id, events.c.owner == owner_key(session_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("This event does not exist or is not yours.")
        return _event_from_row(row)

    def list_for(self, session_id: str, limit: int = 20) -> list[StoredEvent]:
        """This session's events, most recently updated first."""
        rows = self._connection.execute(
            select(*_EVENT_COLUMNS)
            .where(events.c.owner == owner_key(session_id))
            .order_by(events.c.updated_at.desc())
            .limit(limit)
        ).all()
        return [_event_from_row(row) for row in rows]

    def save(
        self,
        event_id: str,
        session_id: str,
        plan: EventPlan,
        *,
        expected_version: int,
        ai: Mapping[str, Any] | None = None,
    ) -> StoredEvent:
        """Save a new version, but only if nobody saved since `expected_version`.

        Args:
            event_id: Event to update.
            session_id: Owner's session.
            plan: The new plan.
            expected_version: Version the change was based on.
            ai: Replacement personalization record, or None to keep the current one.

        Returns:
            The stored event at `expected_version + 1`.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
            StaleProposalError: If the event was saved since `expected_version`.
        """
        new_version = expected_version + 1
        new_plan = plan.with_changes(version=new_version)
        moment = self._now()
        values: dict[str, Any] = {
            "plan": new_plan.model_dump(mode="json"),
            "version": new_version,
            "updated_at": moment,
        }
        if ai is not None:
            values["ai"] = dict(ai)
        owner = owner_key(session_id)
        result = self._connection.execute(
            update(events)
            .where(
                events.c.id == event_id,
                events.c.owner == owner,
                events.c.version == expected_version,
            )
            .values(**values)
            .returning(events.c.ai)
        ).one_or_none()
        if result is None:
            exists = self._connection.execute(
                select(func.count()).where(events.c.id == event_id, events.c.owner == owner)
            ).scalar_one()
            if not exists:
                raise NotFoundError("This event does not exist or is not yours.")
            raise StaleProposalError(
                "The plan was changed somewhere else. Please reload and try again."
            )
        return StoredEvent(event_id, new_version, new_plan, result.ai, moment)


_EVENT_COLUMNS = (events.c.id, events.c.version, events.c.plan, events.c.ai, events.c.updated_at)


def _event_from_row(row: Any) -> StoredEvent:  # noqa: ANN401 - SQLAlchemy row
    return StoredEvent(
        row.id, row.version, EventPlan.model_validate(row.plan), row.ai, _as_utc(row.updated_at)
    )


class ProposalStatus(StrEnum):
    """Lifecycle of a change proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


@dataclass(frozen=True)
class StoredProposal:
    """A saved proposal: the change history entry."""

    id: str
    event_id: str
    base_version: int
    request_text: str
    intent: Mapping[str, Any]
    proposal: Proposal
    status: ProposalStatus
    created_at: datetime
    decided_at: datetime | None


class ProposalRepository:
    """Change proposals and their history, inside the caller's transaction.

    Callers check event ownership first (through `EventRepository.get`).
    """

    def __init__(self, connection: Connection, now: Now = utc_now) -> None:
        """Create the repository."""
        self._connection = connection
        self._now = now

    def add(
        self, event_id: str, request_text: str, intent: Mapping[str, Any], proposal: Proposal
    ) -> StoredProposal:
        """Save a pending proposal."""
        stored = StoredProposal(
            id=uuid.uuid4().hex,
            event_id=event_id,
            base_version=proposal.base_version,
            request_text=request_text[:300],
            intent=dict(intent),
            proposal=proposal,
            status=ProposalStatus.PENDING,
            created_at=self._now(),
            decided_at=None,
        )
        self._connection.execute(
            insert(proposals).values(
                id=stored.id,
                event_id=event_id,
                base_version=stored.base_version,
                request_text=stored.request_text,
                intent=stored.intent,
                proposal=proposal.model_dump(mode="json"),
                status=stored.status.value,
                created_at=stored.created_at,
            )
        )
        return stored

    def get(self, proposal_id: str, event_id: str) -> StoredProposal:
        """Load one proposal of an event.

        Raises:
            NotFoundError: If it does not belong to this event.
        """
        row = self._connection.execute(
            select(proposals).where(proposals.c.id == proposal_id, proposals.c.event_id == event_id)
        ).one_or_none()
        if row is None:
            raise NotFoundError("This change proposal does not exist.")
        return _proposal_from_row(row)

    def decide(self, proposal_id: str, event_id: str, status: ProposalStatus) -> bool:
        """Move a pending proposal to a final status.

        Returns:
            True if this call made the decision; False if it was already decided (so an
            approval can never be applied twice).
        """
        result = self._connection.execute(
            update(proposals)
            .where(
                proposals.c.id == proposal_id,
                proposals.c.event_id == event_id,
                proposals.c.status == ProposalStatus.PENDING.value,
            )
            .values(status=status.value, decided_at=self._now())
        )
        return result.rowcount == 1

    def history(self, event_id: str, limit: int = 20) -> list[StoredProposal]:
        """An event's proposals, newest first."""
        rows = self._connection.execute(
            select(proposals)
            .where(proposals.c.event_id == event_id)
            .order_by(proposals.c.created_at.desc())
            .limit(limit)
        ).all()
        return [_proposal_from_row(row) for row in rows]


def _proposal_from_row(row: Any) -> StoredProposal:  # noqa: ANN401 - SQLAlchemy row
    return StoredProposal(
        id=row.id,
        event_id=row.event_id,
        base_version=row.base_version,
        request_text=row.request_text,
        intent=row.intent,
        proposal=Proposal.model_validate(row.proposal),
        status=ProposalStatus(row.status),
        created_at=_as_utc(row.created_at),
        decided_at=_as_utc(row.decided_at) if row.decided_at else None,
    )


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


class SqlCacheStore:
    """AI answer cache in Postgres, shared by every function instance.

    Expired rows are removed every few writes, so the table stays small without a job.
    """

    def __init__(self, engine: Engine, now: Now = utc_now) -> None:
        """Create the store."""
        self._engine = engine
        self._now = now
        self._writes = 0

    def get(self, key: str) -> Mapping[str, Any] | None:
        """Return a live entry, or None."""
        with self._engine.connect() as connection:
            value: Mapping[str, Any] | None = connection.execute(
                select(ai_cache.c.value).where(
                    ai_cache.c.key == key, ai_cache.c.expires_at > self._now()
                )
            ).scalar_one_or_none()
        return value

    def set(self, key: str, value: Mapping[str, Any], ttl_s: int) -> None:
        """Insert or replace an entry."""
        moment = self._now()
        with self._engine.begin() as connection:
            connection.execute(
                _upsert(
                    self._engine,
                    ai_cache,
                    {
                        "key": key,
                        "value": dict(value),
                        "expires_at": moment + timedelta(seconds=ttl_s),
                    },
                    keys=("key",),
                )
            )
            self._writes += 1
            if self._writes % PURGE_EVERY_WRITES == 0:
                connection.execute(delete(ai_cache).where(ai_cache.c.expires_at <= moment))


class RateLimiter:
    """Fixed-window counters shared across function instances."""

    def __init__(self, engine: Engine, now: Now = utc_now) -> None:
        """Create the limiter."""
        self._engine = engine
        self._now = now
        self._writes = 0

    def hit(self, bucket: str, *, limit: int, window_s: int) -> bool:
        """Count one request in the current window.

        Args:
            bucket: Counter name, for example a hashed client address.
            limit: Requests allowed per window.
            window_s: Window length in seconds.

        Returns:
            True if the request is within the limit.
        """
        moment = self._now()
        window_start = int(moment.timestamp()) // window_s * window_s
        statement = _upsert(
            self._engine,
            rate_limits,
            {"bucket": bucket, "window_start": window_start, "count": 1},
            keys=("bucket", "window_start"),
            increment="count",
        ).returning(rate_limits.c.count)
        with self._engine.begin() as connection:
            count = connection.execute(statement).scalar_one()
            self._writes += 1
            if self._writes % PURGE_EVERY_WRITES == 0:
                oldest = int((moment - RATE_LIMIT_RETENTION).timestamp())
                connection.execute(delete(rate_limits).where(rate_limits.c.window_start < oldest))
        return bool(count <= limit)


def _upsert(
    engine: Engine,
    table: Table,
    values: Mapping[str, Any],
    *,
    keys: Sequence[str],
    increment: str | None = None,
) -> Any:  # noqa: ANN401 - dialect-specific statement
    """Insert, or on conflict update (or increment one column), in the engine's dialect."""
    dialect = postgresql if engine.dialect.name == "postgresql" else sqlite
    statement = dialect.insert(table).values(**values)
    if increment is not None:
        updates = {increment: table.c[increment] + 1}
    else:
        updates = {name: statement.excluded[name] for name in values if name not in keys}
    return statement.on_conflict_do_update(index_elements=list(keys), set_=updates)

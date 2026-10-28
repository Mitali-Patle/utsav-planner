"""Saved event plans, versioned and owned by a hashed session."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, func, insert, select, update

from app.domain.errors import NotFoundError, StaleProposalError
from app.domain.models import EventPlan
from app.persistence.common import Now, as_utc, owner_key, utc_now
from app.persistence.tables import events


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


def _event_from_row(row: Row[*tuple[Any, ...]]) -> StoredEvent:
    return StoredEvent(
        row.id, row.version, EventPlan.model_validate(row.plan), row.ai, as_utc(row.updated_at)
    )

"""Change proposals: pending previews, approvals, rejections and the history."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, Row, delete, insert, select, update

from app.domain.errors import NotFoundError
from app.domain.proposal import Proposal
from app.persistence.common import Now, as_utc, utc_now
from app.persistence.tables import proposals

MAX_PROPOSALS_PER_EVENT = 50


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
        """Save a pending proposal.

        Older pending previews of the event become stale (only the newest preview can be
        approved), and the event keeps its newest `MAX_PROPOSALS_PER_EVENT` proposals, so
        storage stays bounded however many previews are made.
        """
        self._mark_pending_stale(event_id)
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
        self._keep_newest(event_id)
        return stored

    def _mark_pending_stale(self, event_id: str) -> None:
        """Only the newest preview can be approved, so older pending ones go stale."""
        self._connection.execute(
            update(proposals)
            .where(
                proposals.c.event_id == event_id,
                proposals.c.status == ProposalStatus.PENDING.value,
            )
            .values(status=ProposalStatus.STALE.value, decided_at=self._now())
        )

    def _keep_newest(self, event_id: str) -> None:
        """Delete all but the newest `MAX_PROPOSALS_PER_EVENT` proposals of an event."""
        newest = (
            select(proposals.c.id)
            .where(proposals.c.event_id == event_id)
            .order_by(proposals.c.created_at.desc())
            .limit(MAX_PROPOSALS_PER_EVENT)
        )
        self._connection.execute(
            delete(proposals).where(proposals.c.event_id == event_id, proposals.c.id.not_in(newest))
        )

    def get(self, proposal_id: str, event_id: str, *, lock: bool = False) -> StoredProposal:
        """Load one proposal of an event.

        Args:
            proposal_id: The proposal.
            event_id: The event it must belong to.
            lock: Lock the row until the transaction ends (Postgres `FOR UPDATE`), so a
                concurrent decision waits instead of racing. SQLite ignores it.

        Raises:
            NotFoundError: If it does not belong to this event.
        """
        query = select(proposals).where(
            proposals.c.id == proposal_id, proposals.c.event_id == event_id
        )
        row = self._connection.execute(query.with_for_update() if lock else query).one_or_none()
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
        """An event's approved and rejected changes, newest first."""
        rows = self._connection.execute(
            select(proposals)
            .where(
                proposals.c.event_id == event_id,
                proposals.c.status.in_(
                    [ProposalStatus.APPROVED.value, ProposalStatus.REJECTED.value]
                ),
            )
            .order_by(proposals.c.created_at.desc())
            .limit(limit)
        ).all()
        return [_proposal_from_row(row) for row in rows]


def _proposal_from_row(row: Row[*tuple[Any, ...]]) -> StoredProposal:
    return StoredProposal(
        id=row.id,
        event_id=row.event_id,
        base_version=row.base_version,
        request_text=row.request_text,
        intent=row.intent,
        proposal=Proposal.model_validate(row.proposal),
        status=ProposalStatus(row.status),
        created_at=as_utc(row.created_at),
        decided_at=as_utc(row.decided_at) if row.decided_at else None,
    )

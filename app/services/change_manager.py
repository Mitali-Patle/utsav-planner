"""Use case: the Smart Change Manager — request, preview, approve or reject.

AI interprets a request (or a form supplies operations directly); the domain computes a
Proposal; it is saved as pending. Approval saves the new plan version and marks the
proposal approved in one transaction, so the two can never get out of step.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.ai.chain import FallbackChain
from app.ai.client import Provenance
from app.ai.contexts import ChangeContext
from app.ai.guardrails import MAX_REQUEST_CHARS, clean_user_text, intent_to_change_set
from app.ai.schemas import IntentStatus
from app.ai.tasks import INTERPRET_CHANGE
from app.domain.changes import ChangeSet, Proposal, accept, propose
from app.domain.clock import Clock
from app.domain.errors import (
    InfeasibleChangeError,
    InvalidInputError,
    NotFoundError,
    StaleProposalError,
)
from app.persistence.repositories import (
    Database,
    ProposalStatus,
    StoredEvent,
    StoredProposal,
)
from app.services.planning import ai_record

OFF_TOPIC_MESSAGE = "I can only help with changes to this event plan."
EMPTY_REQUEST_MESSAGE = "Please describe the change you want to make."
NOTHING_TO_CHANGE_MESSAGE = "That is already in your plan, so there is nothing to change."


class OutcomeKind(StrEnum):
    """What came of a change request."""

    PROPOSAL = "proposal"
    CLARIFICATION = "clarification"
    OFF_TOPIC = "off_topic"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    NOTHING_TO_CHANGE = "nothing_to_change"


@dataclass(frozen=True)
class ChangeOutcome:
    """Result of a change request, ready for the web layer to render."""

    kind: OutcomeKind
    message: str | None = None
    proposal: StoredProposal | None = None
    unsaved: Proposal | None = None
    provenance: Provenance | None = None


class ApprovalStatus(StrEnum):
    """What came of an approval."""

    APPROVED = "approved"
    STALE = "stale"
    ALREADY_DECIDED = "already_decided"
    INFEASIBLE = "infeasible"


@dataclass(frozen=True)
class ApprovalOutcome:
    """Result of approving a proposal."""

    status: ApprovalStatus
    event: StoredEvent | None = None
    message: str | None = None


class ChangeManager:
    """Turns change requests into approved plan versions."""

    def __init__(self, db: Database, ai: FallbackChain, clock: Clock) -> None:
        """Wire the service."""
        self._db = db
        self._ai = ai
        self._clock = clock

    def request_change(self, session_id: str, event_id: str, text: str) -> ChangeOutcome:
        """Interpret a plain-language request and save the resulting proposal.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        request = clean_user_text(text, MAX_REQUEST_CHARS)
        stored = self._load(session_id, event_id)
        if not request:
            return ChangeOutcome(OutcomeKind.INVALID, EMPTY_REQUEST_MESSAGE)

        today = self._clock.today()
        result = self._ai.run(
            INTERPRET_CHANGE, ChangeContext(plan=stored.plan, request=request, today=today)
        )
        intent, provenance = result.value, result.provenance
        if provenance.fallback:
            return ChangeOutcome(
                OutcomeKind.UNAVAILABLE, intent.clarification_question, provenance=provenance
            )
        if intent.status is IntentStatus.OFF_TOPIC:
            return ChangeOutcome(OutcomeKind.OFF_TOPIC, OFF_TOPIC_MESSAGE, provenance=provenance)
        if intent.status is IntentStatus.NEEDS_CLARIFICATION:
            return ChangeOutcome(
                OutcomeKind.CLARIFICATION, intent.clarification_question, provenance=provenance
            )
        return self._propose(
            stored, request, intent_to_change_set(intent), ai_record(result), provenance
        )

    def propose_from_form(
        self, session_id: str, event_id: str, change_set: ChangeSet, summary: str
    ) -> ChangeOutcome:
        """Create a proposal from the structured form, without AI (the fallback path).

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        stored = self._load(session_id, event_id)
        return self._propose(stored, summary, change_set, {"source": "form"}, None)

    def approve(self, session_id: str, event_id: str, proposal_id: str) -> ApprovalOutcome:
        """Apply a pending proposal: new plan version and approved status, in one transaction.

        Raises:
            NotFoundError: If the event or proposal does not exist or is not this session's.
        """
        with self._db.transaction() as repos:
            stored = repos.events.get(event_id, session_id)
            pending = repos.proposals.get(proposal_id, event_id)
            if pending.status is not ProposalStatus.PENDING:
                return ApprovalOutcome(
                    ApprovalStatus.ALREADY_DECIDED,
                    message=f"This change was already {pending.status}.",
                )
            try:
                new_plan = accept(pending.proposal, stored.plan)
                saved = repos.events.save(
                    event_id, session_id, new_plan, expected_version=stored.version
                )
            except StaleProposalError as error:
                repos.proposals.decide(proposal_id, event_id, ProposalStatus.STALE)
                return ApprovalOutcome(ApprovalStatus.STALE, message=error.message)
            except InfeasibleChangeError as error:
                return ApprovalOutcome(ApprovalStatus.INFEASIBLE, message=error.message)
            repos.proposals.decide(proposal_id, event_id, ProposalStatus.APPROVED)
            return ApprovalOutcome(ApprovalStatus.APPROVED, event=saved)

    def reject(self, session_id: str, event_id: str, proposal_id: str) -> bool:
        """Reject a pending proposal. The plan is untouched.

        Returns:
            True if this call rejected it; False if it was already decided.

        Raises:
            NotFoundError: If the event or proposal does not exist or is not this session's.
        """
        with self._db.transaction() as repos:
            repos.events.get(event_id, session_id)
            repos.proposals.get(proposal_id, event_id)
            return repos.proposals.decide(proposal_id, event_id, ProposalStatus.REJECTED)

    def get_proposal(self, session_id: str, event_id: str, proposal_id: str) -> StoredProposal:
        """Load one proposal of this session's event.

        Raises:
            NotFoundError: If the event or proposal does not exist or is not this session's.
        """
        with self._db.transaction() as repos:
            repos.events.get(event_id, session_id)
            return repos.proposals.get(proposal_id, event_id)

    def history(self, session_id: str, event_id: str) -> list[StoredProposal]:
        """The event's change history, newest first.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        with self._db.transaction() as repos:
            repos.events.get(event_id, session_id)
            return repos.proposals.history(event_id)

    def _load(self, session_id: str, event_id: str) -> StoredEvent:
        with self._db.transaction() as repos:
            return repos.events.get(event_id, session_id)

    def _propose(
        self,
        stored: StoredEvent,
        request: str,
        change_set: ChangeSet,
        intent: dict[str, Any],
        provenance: Provenance | None,
    ) -> ChangeOutcome:
        try:
            proposal = propose(stored.plan, change_set, self._clock.today())
        except (InvalidInputError, NotFoundError, InfeasibleChangeError) as error:
            return ChangeOutcome(OutcomeKind.INVALID, error.message, provenance=provenance)
        if proposal.feasible and not proposal.has_changes:
            return ChangeOutcome(
                OutcomeKind.NOTHING_TO_CHANGE,
                NOTHING_TO_CHANGE_MESSAGE,
                unsaved=proposal,
                provenance=provenance,
            )
        with self._db.transaction() as repos:
            saved = repos.proposals.add(stored.id, request, intent, proposal)
        return ChangeOutcome(OutcomeKind.PROPOSAL, proposal=saved, provenance=provenance)

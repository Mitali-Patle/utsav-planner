"""Use case: create an event — a code-built draft first, then AI personalization."""

import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.ai.chain import FallbackChain
from app.ai.client import AIResult
from app.ai.contexts import DEFAULT_TONE, PersonalizeContext
from app.ai.schemas import PlanSuggestions
from app.ai.tasks import PERSONALIZE
from app.domain.budget import Emphasis
from app.domain.catalog import IdeaSpec, TaskSpec
from app.domain.clock import Clock
from app.domain.errors import StaleProposalError
from app.domain.models import CategoryId, EventPlan
from app.domain.planning import (
    EventDetails,
    Personalization,
    add_ideas,
    draft_plan,
    is_untouched,
    personalize_plan,
)
from app.persistence.repositories import Database, StoredEvent

logger = logging.getLogger(__name__)

SAVE_ATTEMPTS = 2


class PlanningService:
    """Creates, lists and loads events."""

    def __init__(
        self, db: Database, ai: FallbackChain, clock: Clock, *, tone: str = DEFAULT_TONE
    ) -> None:
        """Wire the service.

        Args:
            db: Unit-of-work factory.
            ai: Fallback chain for AI calls.
            clock: Source of today's date in India.
            tone: Persona tone passed to the personalization prompt.
        """
        self._db = db
        self._ai = ai
        self._clock = clock
        self._tone = tone

    def create_draft(self, session_id: str, details: EventDetails) -> StoredEvent:
        """Save the complete, code-built opening plan straight away (no AI call).

        Raises:
            InvalidInputError: If the event date or sub-events are invalid.
        """
        draft = draft_plan(details, self._clock.today())
        with self._db.transaction() as repos:
            return repos.events.create(session_id, draft)

    def personalize(
        self,
        session_id: str,
        event_id: str,
        *,
        ai_allowed: Callable[[], bool] = lambda: True,
    ) -> StoredEvent:
        """Tailor a saved plan with AI.

        Idempotent once a model has answered. A plan that got the labelled standard ideas
        (every model down, or the client over its AI limit) can be personalized again: the
        standard ideas are replaced, and nothing is saved unless a model actually answers.
        While the plan is untouched, the AI may re-split the budget and add tasks, vendor
        notes and ideas; once the user has started editing, only ideas are added, so the AI
        never overwrites the user's work. The AI call happens outside any transaction.

        Args:
            session_id: Owner's session.
            event_id: Event to personalize.
            ai_allowed: Asked only when a model would be called; False (the client is over
                its AI rate limit) gives the labelled fallback without calling a model.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        stored = self.get(session_id, event_id)
        if _personalized_by_ai(stored):
            return stored
        retry = stored.ai is not None
        use_ai = ai_allowed()
        if retry and not use_ai:
            return stored
        today = self._clock.today()
        context = PersonalizeContext(
            plan=_without_standard_ideas(stored), today=today, tone=self._tone
        )
        result = (
            self._ai.run(PERSONALIZE, context) if use_ai else self._ai.offline(PERSONALIZE, context)
        )
        if retry and result.provenance.fallback:
            return stored
        personalization = (
            Personalization() if result.value.off_topic else to_personalization(result.value)
        )
        for _ in range(SAVE_ATTEMPTS):
            base = _without_standard_ideas(stored)
            plan = (
                personalize_plan(base, personalization, today)
                if is_untouched(base)
                else add_ideas(base, personalization.ideas)
            )
            try:
                with self._db.transaction() as repos:
                    return repos.events.save(
                        event_id,
                        session_id,
                        plan,
                        expected_version=stored.version,
                        ai=ai_record(result),
                    )
            except StaleProposalError:
                stored = self.get(session_id, event_id)
                if _personalized_by_ai(stored):
                    return stored
        raise StaleProposalError("The plan kept changing while it was being personalized.")

    def get(self, session_id: str, event_id: str) -> StoredEvent:
        """Load one of this session's events.

        Raises:
            NotFoundError: If it does not exist or belongs to another session.
        """
        with self._db.transaction() as repos:
            return repos.events.get(event_id, session_id)

    def list_events(self, session_id: str) -> list[StoredEvent]:
        """This session's events, most recently updated first."""
        with self._db.transaction() as repos:
            return repos.events.list_for(session_id)


def to_personalization(suggestions: PlanSuggestions) -> Personalization:
    """Convert checked AI suggestions into domain inputs, skipping any item that still fails.

    The guardrails have already dropped unknown ids and trimmed text; this is a last line of
    defence so one odd item can never stop an event from being created.
    """
    tasks: list[TaskSpec] = []
    for item in suggestions.extra_tasks:
        try:
            tasks.append(
                TaskSpec(
                    key="ai-task",
                    title=item.title,
                    category_id=CategoryId(item.category_id) if item.category_id else None,
                    offset_days=item.days_before_event,
                    why=item.why,
                )
            )
        except ValidationError:
            logger.warning("skipped an AI task that failed validation")
    ideas: list[IdeaSpec] = []
    for idea in suggestions.ideas:
        try:
            ideas.append(
                IdeaSpec(
                    title=idea.title,
                    description=idea.description,
                    category_id=CategoryId(idea.category_id) if idea.category_id else None,
                    budget_impact=idea.budget_impact,
                    why=idea.why,
                )
            )
        except ValidationError:
            logger.warning("skipped an AI idea that failed validation")
    return Personalization(
        emphasis={
            CategoryId(e.category_id): Emphasis(direction=e.direction, strength=e.strength)
            for e in suggestions.budget_emphasis
        },
        tasks=tuple(tasks),
        ideas=tuple(ideas),
        vendor_notes={note.vendor_type: note.why for note in suggestions.vendor_notes if note.why},
    )


def ai_record(result: AIResult[Any]) -> dict[str, Any]:
    """What is stored for the "How this was generated" panel: answer and provenance."""
    return {
        "result": result.value.model_dump(mode="json"),
        "provenance": result.provenance.model_dump(mode="json"),
    }


def _personalized_by_ai(stored: StoredEvent) -> bool:
    return stored.ai is not None and not stored.ai["provenance"]["fallback"]


def _without_standard_ideas(stored: StoredEvent) -> EventPlan:
    """The plan without its fallback ideas, which are the only thing a fallback adds."""
    if stored.ai is None:
        return stored.plan
    return stored.plan.with_changes(ideas=())

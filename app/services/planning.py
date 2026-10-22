"""Use case: create an event — draft by code, personalize by AI, save as version 1."""

import logging
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
from app.domain.models import CategoryId
from app.domain.planning import EventDetails, Personalization, draft_plan, personalize_plan
from app.persistence.repositories import Database, StoredEvent

logger = logging.getLogger(__name__)


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

    def create_event(self, session_id: str, details: EventDetails) -> StoredEvent:
        """Draft a plan, personalize it with AI (or the labelled fallback) and save it.

        The AI call happens before the database transaction opens, so no connection is
        held while waiting for a model.

        Raises:
            InvalidInputError: If the event date or sub-events are invalid.
        """
        today = self._clock.today()
        draft = draft_plan(details, today)
        result = self._ai.run(
            PERSONALIZE, PersonalizeContext(plan=draft, today=today, tone=self._tone)
        )
        plan = (
            draft
            if result.value.off_topic
            else personalize_plan(draft, to_personalization(result.value), today)
        )
        with self._db.transaction() as repos:
            return repos.events.create(session_id, plan, ai=ai_record(result))

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

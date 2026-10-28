"""Use case: suggest ways to close a budget shortfall. AI suggests; code prices each option."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.ai.chain import FallbackChain
from app.ai.client import Provenance
from app.ai.contexts import AlternativesContext
from app.ai.schemas import AIAlternative
from app.ai.tasks import SUGGEST_ALTERNATIVES
from app.domain.budget import SavingAction, SavingLevel, summarize
from app.domain.changes import propose
from app.domain.clock import Clock
from app.domain.errors import DomainError
from app.domain.models import CategoryId, EventPlan
from app.domain.savings import saving_change
from app.persistence.repositories import Database
from app.services.planning import ai_record

WITHIN_BUDGET_MESSAGE = "Your plan is within budget, so there is nothing to close."


@dataclass(frozen=True)
class AlternativeOption:
    """One priced way to save money.

    `saving_inr` and `shortfall_after_inr` come from the same preview that applying the
    option creates; `applicable` is False when the line has nothing left to free.
    """

    category_id: str
    category_name: str
    action: SavingAction
    level: SavingLevel
    title: str
    why: str
    saving_inr: int
    shortfall_after_inr: int
    applicable: bool


@dataclass(frozen=True)
class AlternativesView:
    """Alternatives for a shortfall, with the AI provenance when the AI was asked."""

    shortfall_inr: int
    summary: str
    options: tuple[AlternativeOption, ...]
    provenance: Provenance | None
    ai: Mapping[str, Any] | None = None


class AlternativesService:
    """Suggests and prices alternatives."""

    def __init__(self, db: Database, ai: FallbackChain, clock: Clock) -> None:
        """Wire the service."""
        self._db = db
        self._ai = ai
        self._clock = clock

    def suggest(
        self,
        session_id: str,
        event_id: str,
        *,
        ai_allowed: Callable[[], bool] = lambda: True,
    ) -> AlternativesView:
        """Suggest ways to close the plan's shortfall.

        Args:
            session_id: Owner's session.
            event_id: Event with the shortfall.
            ai_allowed: Asked only when a model would be called; False (the client is over
                its AI rate limit) gives the labelled fallback.

        Returns:
            Priced options. No AI call is made when there is nothing to close.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        with self._db.transaction() as repos:
            plan = repos.events.get(event_id, session_id).plan
        shortfall = summarize(plan).shortfall_inr
        if shortfall <= 0:
            return AlternativesView(0, WITHIN_BUDGET_MESSAGE, (), None)

        context = AlternativesContext(plan=plan, shortfall_inr=shortfall)
        result = (
            self._ai.run(SUGGEST_ALTERNATIVES, context)
            if ai_allowed()
            else self._ai.offline(SUGGEST_ALTERNATIVES, context)
        )
        today = self._clock.today()
        options = [_price(plan, item, shortfall, today) for item in result.value.items]
        return AlternativesView(
            shortfall, result.value.summary, tuple(options), result.provenance, ai_record(result)
        )


def _price(plan: EventPlan, item: AIAlternative, shortfall: int, today: date) -> AlternativeOption:
    """Price an option by previewing the change that applying it would make."""
    category = plan.category(CategoryId(item.category_id))
    change_set = saving_change(plan, category.id, item.action, item.level)
    try:
        after = propose(plan, change_set, today).after if change_set else None
    except DomainError:
        after = None
    return AlternativeOption(
        category_id=category.id,
        category_name=category.name,
        action=item.action,
        level=item.level,
        title=item.title,
        why=item.why,
        saving_inr=category.allocated_inr - after.category(category.id).allocated_inr
        if after
        else 0,
        shortfall_after_inr=summarize(after).shortfall_inr if after else shortfall,
        applicable=after is not None,
    )

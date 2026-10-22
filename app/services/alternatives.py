"""Use case: suggest ways to close a budget shortfall. AI suggests; code prices each option."""

from dataclasses import dataclass

from app.ai.chain import FallbackChain
from app.ai.client import Provenance
from app.ai.contexts import AlternativesContext
from app.ai.tasks import SUGGEST_ALTERNATIVES
from app.domain.budget import SavingAction, SavingLevel, estimate_saving, summarize
from app.domain.models import CategoryId
from app.persistence.repositories import Database

WITHIN_BUDGET_MESSAGE = "Your plan is within budget, so there is nothing to close."


@dataclass(frozen=True)
class AlternativeOption:
    """One priced way to save money."""

    category_id: str
    category_name: str
    action: SavingAction
    level: SavingLevel
    title: str
    why: str
    saving_inr: int
    shortfall_after_inr: int


@dataclass(frozen=True)
class AlternativesView:
    """Alternatives for a shortfall, with the AI provenance when the AI was asked."""

    shortfall_inr: int
    summary: str
    options: tuple[AlternativeOption, ...]
    provenance: Provenance | None


class AlternativesService:
    """Suggests and prices alternatives."""

    def __init__(self, db: Database, ai: FallbackChain) -> None:
        """Wire the service."""
        self._db = db
        self._ai = ai

    def suggest(
        self, session_id: str, event_id: str, shortfall_inr: int | None = None
    ) -> AlternativesView:
        """Suggest ways to close a shortfall.

        Args:
            session_id: Owner's session.
            event_id: Event with the shortfall.
            shortfall_inr: Shortfall of a proposed change; defaults to the plan's own
                overspending.

        Returns:
            Priced options. No AI call is made when there is nothing to close.

        Raises:
            NotFoundError: If the event does not exist or belongs to another session.
        """
        with self._db.transaction() as repos:
            plan = repos.events.get(event_id, session_id).plan
        shortfall = summarize(plan).shortfall_inr if shortfall_inr is None else shortfall_inr
        if shortfall <= 0:
            return AlternativesView(0, WITHIN_BUDGET_MESSAGE, (), None)

        result = self._ai.run(
            SUGGEST_ALTERNATIVES, AlternativesContext(plan=plan, shortfall_inr=shortfall)
        )
        options = []
        for item in result.value.items:
            category = plan.category(CategoryId(item.category_id))
            saving = estimate_saving(category.allocated_inr, item.action, item.level)
            options.append(
                AlternativeOption(
                    category_id=category.id,
                    category_name=category.name,
                    action=item.action,
                    level=item.level,
                    title=item.title,
                    why=item.why,
                    saving_inr=saving,
                    shortfall_after_inr=max(0, shortfall - saving),
                )
            )
        return AlternativesView(shortfall, result.value.summary, tuple(options), result.provenance)

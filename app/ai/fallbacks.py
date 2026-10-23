"""Deterministic answers used when every provider fails. Always labelled as not AI-generated."""

from app.ai.contexts import AlternativesContext, ChangeContext, PersonalizeContext
from app.ai.schemas import (
    AIAlternative,
    AIIdea,
    Alternatives,
    ChangeIntent,
    IntentStatus,
    PlanSuggestions,
)
from app.domain.budget import SavingAction, SavingLevel
from app.domain.catalog import spec_for

NOT_AI = "not AI-generated"
FALLBACK_ALTERNATIVES = 3
NOTHING_TO_TRIM = (
    "Every unprotected budget line is already spent, so trimming cannot close this gap; "
    "raise the total budget or release a protected line"
)


def personalize_fallback(context: PersonalizeContext) -> PlanSuggestions:
    """Standard ideas for the event type from the catalog."""
    spec = spec_for(context.plan.event_type)
    return PlanSuggestions(
        summary_why=f"Standard ideas for this kind of event ({NOT_AI}).",
        ideas=[
            AIIdea(
                title=idea.title,
                description=idea.description,
                category_id=idea.category_id,
                budget_impact=idea.budget_impact,
                why=idea.why,
            )
            for idea in spec.fallback_ideas
        ],
    )


def interpret_fallback(context: ChangeContext) -> ChangeIntent:
    """Ask the user to use the structured change form instead."""
    return ChangeIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        clarification_question=(
            "The assistant can't process requests right now. Please use the change form below."
        ),
        summary=f"The request was not interpreted ({NOT_AI}).",
    )


def alternatives_fallback(context: AlternativesContext) -> Alternatives:
    """Point to the flexible budget lines with the most unspent money."""
    plan = context.plan
    flexible = sorted(
        (c for c in plan.categories if not c.protected and plan.unspent_inr(c.id) > 0),
        key=lambda c: (-plan.unspent_inr(c.id), c.id),
    )
    if not flexible:
        return Alternatives(summary=f"{NOTHING_TO_TRIM} ({NOT_AI}).", items=[])
    return Alternatives(
        summary=f"Your largest flexible budget lines, where savings are easiest ({NOT_AI}).",
        items=[
            AIAlternative(
                category_id=c.id,
                action=SavingAction.REDUCE,
                level=SavingLevel.MEDIUM,
                title=f"Trim {c.name.lower()}",
                why=f"One of the largest flexible lines in this plan ({NOT_AI}).",
            )
            for c in flexible[:FALLBACK_ALTERNATIVES]
        ],
    )

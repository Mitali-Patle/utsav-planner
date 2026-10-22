"""Opening plans: drafted from the catalog without AI, then personalized.

`draft_plan` builds a complete, usable plan on its own. `personalize_plan` applies AI
suggestions that the services layer has already validated and converted to domain types,
so this module never depends on the AI layer.
"""

from collections.abc import Sequence
from datetime import date

from pydantic import Field

from app.domain.baseline import baseline_categories
from app.domain.budget import Emphasis, allocate, apply_emphasis
from app.domain.catalog import IdeaSpec, TaskSpec, tasks_for, validate_sub_events, vendors_for
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    CategoryRef,
    CityName,
    DomainModel,
    EventPlan,
    EventType,
    Idea,
    IdeaId,
    LongText,
    Rationale,
    ShortText,
    Slug,
    TaskSource,
    VendorStatus,
    evolve,
    id_sequence,
    validate_event_date,
)
from app.domain.timeline import build_tasks
from app.domain.vendors import build_vendors


class EventDetails(DomainModel):
    """What the user tells us about the event."""

    event_type: EventType
    title: ShortText
    city: CityName
    event_date: date
    guest_count: int = Field(ge=1, le=MAX_GUESTS)
    total_budget_inr: int = Field(ge=MIN_BUDGET_INR, le=MAX_BUDGET_INR)
    preferences: LongText = ""
    sub_events: tuple[Slug, ...] = ()


def draft_plan(details: EventDetails, today: date) -> EventPlan:
    """Build the opening plan: budget, timeline and vendor checklist.

    Args:
        details: Validated event details.
        today: Current date in India.

    Returns:
        A complete plan at version 1.

    Raises:
        InvalidInputError: If the date is outside the planning window or a sub-event is
            unknown for this event type.
    """
    validate_event_date(details.event_date, today)
    sub_events = validate_sub_events(details.event_type, details.sub_events)
    return EventPlan(
        **details.model_dump(exclude={"sub_events"}),
        sub_events=sub_events,
        categories=baseline_categories(
            details.event_type, sub_events, details.guest_count, details.total_budget_inr
        ),
        tasks=build_tasks(tasks_for(details.event_type, sub_events), details.event_date, today),
        vendors=build_vendors(
            vendors_for(details.event_type, sub_events), details.event_date, today
        ),
    )


class Personalization(DomainModel):
    """Validated suggestions for a plan, already converted to domain types."""

    emphasis: dict[CategoryRef, Emphasis] = Field(default_factory=dict)
    tasks: tuple[TaskSpec, ...] = ()
    ideas: tuple[IdeaSpec, ...] = ()
    vendor_notes: dict[Slug, Rationale] = Field(default_factory=dict)


def personalize_plan(plan: EventPlan, personalization: Personalization, today: date) -> EventPlan:
    """Apply validated personalization to a freshly drafted plan.

    Budget emphasis changes category weights by at most 20 % and the total is re-split
    exactly; extra tasks are dated and marked as AI-suggested; ideas and vendor notes keep
    their reasons.

    Args:
        plan: A plan from `draft_plan`, with no expenses or protected categories yet.
        personalization: Emphasis, extra tasks, ideas and vendor notes.
        today: Current date in India.

    Returns:
        The personalized plan (same version; it has not been saved yet).

    Raises:
        ValueError: If the plan is no longer untouched (see `is_untouched`).
        NotFoundError: If an emphasis refers to an unknown category.
    """
    if not is_untouched(plan):
        raise ValueError("personalize_plan expects a freshly drafted plan")
    categories = allocate(
        apply_emphasis(plan.categories, personalization.emphasis), plan.total_budget_inr
    )
    new_tasks = build_tasks(
        personalization.tasks,
        plan.event_date,
        today,
        existing_ids=[t.id for t in plan.tasks],
        source=TaskSource.AI,
    )
    vendors = tuple(
        evolve(v, why=personalization.vendor_notes[v.vendor_type])
        if v.vendor_type in personalization.vendor_notes
        else v
        for v in plan.vendors
    )
    return add_ideas(
        plan.with_changes(categories=categories, tasks=(*plan.tasks, *new_tasks), vendors=vendors),
        personalization.ideas,
    )


def is_untouched(plan: EventPlan) -> bool:
    """Whether a plan is still exactly as drafted: no expenses, nothing protected, no ticks.

    Personalization may only re-split the budget and add tasks on an untouched plan, so the
    AI can never overwrite the user's own work.
    """
    return not (
        plan.expenses
        or any(c.protected for c in plan.categories)
        or any(t.done for t in plan.tasks)
        or any(v.status is not VendorStatus.TODO for v in plan.vendors)
    )


def add_ideas(plan: EventPlan, ideas: Sequence[IdeaSpec]) -> EventPlan:
    """Append ideas with fresh ids, leaving everything else as it is."""
    idea_ids = id_sequence("idea", (i.id for i in plan.ideas))
    new_ideas = tuple(
        Idea(
            id=IdeaId(next(idea_ids)),
            title=idea.title,
            description=idea.description,
            category_id=idea.category_id,
            budget_impact=idea.budget_impact,
            why=idea.why,
        )
        for idea in ideas
    )
    return plan.with_changes(ideas=(*plan.ideas, *new_ideas))

"""Draft a complete opening plan from the user's event details, without any AI.

The services layer adds AI personalization on top of this draft. If the AI is
unavailable, this draft alone is still a complete, usable plan.
"""

from datetime import date

from pydantic import Field

from app.domain.baseline import baseline_categories
from app.domain.catalog import tasks_for, validate_sub_events, vendors_for
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    CityName,
    DomainModel,
    EventPlan,
    EventType,
    LongText,
    ShortText,
    Slug,
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

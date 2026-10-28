"""Planning catalog for Indian weddings, birthdays and community events.

This is the expert baseline the app starts from: budget categories with relative weights,
standard tasks with deadlines counted back from the event, vendor types with booking lead
times, optional sub-events that shift the budget, and non-AI fallback ideas. The AI
personalizes on top of it; code turns it into rupees and dates.

The numbers are planning heuristics, not facts. Weights use the midpoints of published
ranges where they exist; users can change every amount and date. Sources (mostly
planning-industry guides, whose ranges vary):

- Wedding budget split (venue 20-30 %, catering 20-30 %, decor 10-16 %, photo 5-10 %):
  https://samaro.ai/blogs/how-to-plan-wedding-budget-india-2025 ,
  https://www.manyavar.com/en-in/blog-wedding-budget-planning-guide-for-indian-couples.html ,
  https://sum.money/in/marriage-budget-calculator/ (based on WeddingWire India and
  WedMeGood data), https://www.bajajfinserv.in/average-indian-wedding-cost
- Wedding timeline (venue and date first, vendors 9-12 months, invitations 3-6 months):
  https://www.manyavar.com/en-in/blog-wedding-planning-checklist-guide.html ,
  https://venuelook.com/blog/plan-like-a-pro-the-essential-indian-wedding-checklist-from-start-to-finish
- Birthday timeline (venue and entertainers 4-6 weeks, invitations 3-4 weeks, cake about a
  week or more for custom cakes): https://www.cozi.com/blog/complete-kids-birthday-party-checklist ,
  https://patisserie-valerie.co.uk/blogs/news/how-far-in-advance-should-you-order-a-celebration-cake
- Community-event permissions (police, municipal, fire and loudspeaker NOCs; windows from
  about 20 days to 2 months depending on the city):
  https://hindutone.com/festivals/ganesh-chaturthi/a-complete-guide-to-permissions-for-setting-up-ganesh-mandapams-in-india/
  and
  https://www.freepressjournal.in/mumbai/get-permission-for-ganesh-pandals-online

Wedding lead times start at about six months rather than the 9-12 months some guides
suggest, because many families plan within a few months. Tasks whose deadline has already
passed are flagged as compressed rather than dropped.
"""

from collections.abc import Sequence
from types import MappingProxyType

from app.domain.catalog.birthday import BIRTHDAY
from app.domain.catalog.community import COMMUNITY
from app.domain.catalog.specs import (
    CONTINGENCY_ID,
    CategorySpec,
    EventTypeSpec,
    IdeaSpec,
    SubEventSpec,
    TaskSpec,
    VendorSpec,
    WeightBonus,
)
from app.domain.catalog.vendor_questions import VENDOR_QUESTIONS
from app.domain.catalog.wedding import WEDDING
from app.domain.models import EventType

__all__ = [
    "CATALOG",
    "CONTINGENCY_ID",
    "ESSENTIAL_CATEGORIES",
    "VENDOR_QUESTIONS",
    "CategorySpec",
    "EventTypeSpec",
    "IdeaSpec",
    "SubEventSpec",
    "TaskSpec",
    "VendorSpec",
    "WeightBonus",
    "spec_for",
    "tasks_for",
    "validate_sub_events",
    "vendors_for",
]

CATALOG = MappingProxyType({spec.event_type: spec for spec in (WEDDING, BIRTHDAY, COMMUNITY)})

ESSENTIAL_CATEGORIES: frozenset[str] = frozenset(
    {
        "venue",
        "catering",
        "cake",
        "rituals",
        "pandal_venue",
        "prasad_catering",
        "puja_rituals",
        "permits",
    }
)
"""Budget lines a celebration cannot do without: a saving may cut them, never remove them."""


def spec_for(event_type: EventType) -> EventTypeSpec:
    """Return the catalog entry for an event type."""
    return CATALOG[event_type]


def validate_sub_events(event_type: EventType, sub_events: Sequence[str]) -> tuple[str, ...]:
    """Check selected sub-events and return them once each, in catalog order.

    Args:
        event_type: The event type.
        sub_events: Sub-event ids chosen by the user.

    Returns:
        The selected ids without duplicates, ordered as in the catalog.

    Raises:
        InvalidInputError: If an id is not a sub-event of this event type.
    """
    spec = spec_for(event_type)
    for sub_event_id in sub_events:
        spec.sub_event(sub_event_id)
    chosen = set(sub_events)
    return tuple(s.id for s in spec.sub_events if s.id in chosen)


def tasks_for(event_type: EventType, sub_events: Sequence[str]) -> tuple[TaskSpec, ...]:
    """Return the core tasks plus those of the selected sub-events."""
    spec = spec_for(event_type)
    selected = validate_sub_events(event_type, sub_events)
    extra = [task for s in spec.sub_events if s.id in selected for task in s.tasks]
    return (*spec.tasks, *extra)


def vendors_for(event_type: EventType, sub_events: Sequence[str]) -> tuple[VendorSpec, ...]:
    """Return the core vendors plus those of the selected sub-events."""
    spec = spec_for(event_type)
    selected = validate_sub_events(event_type, sub_events)
    extra = [vendor for s in spec.sub_events if s.id in selected for vendor in s.vendors]
    return (*spec.vendors, *extra)

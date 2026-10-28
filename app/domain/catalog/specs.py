"""The catalog's building blocks: specs for lines, tasks, vendors, ideas and event types."""

from typing import Self

from pydantic import Field, model_validator

from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_DAYS_AFTER,
    MAX_DAYS_AHEAD,
    MAX_LEAD_DAYS,
    BudgetImpact,
    CategoryId,
    CategoryRef,
    Description,
    DomainModel,
    EventType,
    Rationale,
    ShortText,
    Slug,
)

CONTINGENCY_ID = CategoryId("contingency")


class CategorySpec(DomainModel):
    """A baseline budget category."""

    id: CategoryRef
    name: ShortText
    weight: int = Field(ge=1)
    scales_with_guests: bool = False


class TaskSpec(DomainModel):
    """A standard task; `offset_days` is days before the event (negative: after it)."""

    key: Slug
    title: ShortText
    category_id: CategoryRef | None = None
    offset_days: int = Field(ge=-MAX_DAYS_AFTER, le=MAX_DAYS_AHEAD)
    why: Rationale | None = None


class VendorSpec(DomainModel):
    """A vendor type and how many days ahead it should be booked."""

    vendor_type: Slug
    name: ShortText
    category_id: CategoryRef
    lead_days: int = Field(ge=0, le=MAX_LEAD_DAYS)


class WeightBonus(DomainModel):
    """Extra relative weight a sub-event adds to a category."""

    category_id: CategoryRef
    weight: int = Field(ge=1)


class IdeaSpec(DomainModel):
    """An idea before it is added to a plan: from the catalog (fallback) or from the AI."""

    title: ShortText
    description: Description
    category_id: CategoryRef | None = None
    budget_impact: BudgetImpact
    why: Rationale


class SubEventSpec(DomainModel):
    """An optional function within an event, such as a sangeet or a community meal."""

    id: Slug
    name: ShortText
    weight_bonus: tuple[WeightBonus, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
    vendors: tuple[VendorSpec, ...] = ()


class EventTypeSpec(DomainModel):
    """Everything the planner knows about one event type."""

    event_type: EventType
    categories: tuple[CategorySpec, ...] = Field(min_length=1)
    tasks: tuple[TaskSpec, ...]
    vendors: tuple[VendorSpec, ...]
    sub_events: tuple[SubEventSpec, ...]
    fallback_ideas: tuple[IdeaSpec, ...]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        """Ensure ids are unique and every reference points to a category of this type.

        Returns:
            The validated spec.

        Raises:
            ValueError: On a duplicate id, an unknown category or a missing contingency line.
        """
        category_ids = [c.id for c in self.categories]
        all_tasks = [*self.tasks, *(t for s in self.sub_events for t in s.tasks)]
        all_vendors = [*self.vendors, *(v for s in self.sub_events for v in s.vendors)]
        for label, ids in (
            ("category", category_ids),
            ("task", [t.key for t in all_tasks]),
            ("vendor", [v.vendor_type for v in all_vendors]),
            ("sub-event", [s.id for s in self.sub_events]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{self.event_type}: duplicate {label} id")

        known = set(category_ids)
        references = [
            *(t.category_id for t in all_tasks if t.category_id is not None),
            *(v.category_id for v in all_vendors),
            *(b.category_id for s in self.sub_events for b in s.weight_bonus),
            *(i.category_id for i in self.fallback_ideas if i.category_id is not None),
        ]
        unknown = sorted(set(references) - known)
        if unknown:
            raise ValueError(f"{self.event_type}: unknown category {unknown[0]}")
        if CONTINGENCY_ID not in known:
            raise ValueError(f"{self.event_type}: a contingency category is required")
        return self

    def sub_event(self, sub_event_id: str) -> SubEventSpec:
        """Return a sub-event by id.

        Raises:
            InvalidInputError: If this event type has no such sub-event.
        """
        found = next((s for s in self.sub_events if s.id == sub_event_id), None)
        if found is None:
            raise InvalidInputError(
                f"'{sub_event_id}' is not a function of a {self.event_type} event.",
                code="unknown_sub_event",
            )
        return found


def category(category_id: str, name: str, weight: int, *, scales: bool = False) -> CategorySpec:
    """A budget line with its relative weight; `scales` lines grow with the guest count."""
    return CategorySpec(
        id=CategoryId(category_id), name=name, weight=weight, scales_with_guests=scales
    )


def task(key: str, title: str, category_id: str | None, offset_days: int) -> TaskSpec:
    """A standard task due `offset_days` before the event (negative means after it)."""
    category = CategoryId(category_id) if category_id else None
    return TaskSpec(key=key, title=title, category_id=category, offset_days=offset_days)


def vendor(vendor_type: str, name: str, category_id: str, lead_days: int) -> VendorSpec:
    """A vendor to book `lead_days` before the event."""
    return VendorSpec(
        vendor_type=vendor_type, name=name, category_id=CategoryId(category_id), lead_days=lead_days
    )


def bonus(category_id: str, weight: int) -> WeightBonus:
    """Extra weight a sub-event adds to a budget line."""
    return WeightBonus(category_id=CategoryId(category_id), weight=weight)


def idea(
    title: str, description: str, category_id: str | None, impact: BudgetImpact, why: str
) -> IdeaSpec:
    """A standard idea (labelled not AI-generated when shown)."""
    category = CategoryId(category_id) if category_id else None
    return IdeaSpec(
        title=title, description=description, category_id=category, budget_impact=impact, why=why
    )

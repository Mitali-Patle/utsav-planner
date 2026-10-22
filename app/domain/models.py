"""Domain models for an event plan.

Models are frozen and collections are tuples, so every operation returns a new plan and a
saved plan can never be changed in place. Amounts are whole rupees. An `EventPlan` checks
its own invariants whenever it is constructed: unique ids, valid category references and
category allocations that add up exactly to the total budget.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated, NewType, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.text import normalize_text

MIN_BUDGET_INR = 1_000
MAX_BUDGET_INR = 100_000_000  # ₹10,00,00,000
MAX_GUESTS = 5_000
MAX_DAYS_AHEAD = 3 * 365
MAX_DAYS_AFTER = 30
MAX_LEAD_DAYS = 730
MAX_PRIORITY = 50
SLUG_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"

CategoryId = NewType("CategoryId", str)
ExpenseId = NewType("ExpenseId", str)
TaskId = NewType("TaskId", str)
VendorId = NewType("VendorId", str)
IdeaId = NewType("IdeaId", str)


def _normalize_if_text(value: object) -> object:
    return normalize_text(value) if isinstance(value, str) else value


_Normalized = BeforeValidator(_normalize_if_text)
ShortText = Annotated[str, _Normalized, StringConstraints(min_length=1, max_length=80)]
CityName = Annotated[str, _Normalized, StringConstraints(min_length=1, max_length=60)]
LongText = Annotated[str, _Normalized, StringConstraints(max_length=500)]
Rationale = Annotated[str, _Normalized, StringConstraints(min_length=1, max_length=280)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]

CategoryRef = Annotated[CategoryId, StringConstraints(pattern=SLUG_PATTERN)]
ExpenseRef = Annotated[ExpenseId, StringConstraints(pattern=SLUG_PATTERN)]
TaskRef = Annotated[TaskId, StringConstraints(pattern=SLUG_PATTERN)]
VendorRef = Annotated[VendorId, StringConstraints(pattern=SLUG_PATTERN)]
IdeaRef = Annotated[IdeaId, StringConstraints(pattern=SLUG_PATTERN)]
Rupees = Annotated[int, Field(ge=0, le=MAX_BUDGET_INR)]


class EventType(StrEnum):
    """The three event types the planner supports."""

    WEDDING = "wedding"
    BIRTHDAY = "birthday"
    COMMUNITY = "community"


class ExpenseSource(StrEnum):
    """Where an expense came from."""

    USER = "user"
    CHANGE = "change"


class TaskSource(StrEnum):
    """Where a task came from."""

    CATALOG = "catalog"
    AI = "ai"
    USER = "user"


class VendorStatus(StrEnum):
    """Booking progress for a vendor."""

    TODO = "todo"
    CONTACTED = "contacted"
    BOOKED = "booked"


class BudgetImpact(StrEnum):
    """How an idea affects spending."""

    SAVES = "saves"
    NEUTRAL = "neutral"
    ADDS = "adds"


class DomainModel(BaseModel):
    """Base for all domain models: immutable and strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def evolve[M: BaseModel](model: M, **changes: object) -> M:
    """Return a validated copy of a model with some fields replaced.

    Unlike `model_copy(update=...)`, this re-runs validation, so invariants always hold.

    Args:
        model: The model to copy.
        **changes: Field values to replace.

    Returns:
        A new, fully validated model.
    """
    return type(model).model_validate({**dict(model), **changes})


def next_id(prefix: str, existing: Iterable[str]) -> str:
    """Return the next sequential id for a prefix.

    Ids are short and deterministic, which keeps tests readable and lets the LLM quote them.

    Args:
        prefix: Id prefix, for example `exp`.
        existing: Ids already in use (any prefixes).

    Returns:
        The next free id with this prefix.

    Examples:
        >>> next_id("exp", ["exp-1", "exp-7", "task-9"])
        'exp-8'
        >>> next_id("task", [])
        'task-1'
    """
    marker = f"{prefix}-"
    numbers = [
        int(item.removeprefix(marker))
        for item in existing
        if item.startswith(marker) and item.removeprefix(marker).isdecimal()
    ]
    return f"{marker}{max(numbers, default=0) + 1}"


class BudgetCategory(DomainModel):
    """A budget line such as catering or decor.

    `weight` is a relative weight used for the initial allocation only; later changes
    keep the user's current proportions instead.
    """

    id: CategoryRef
    name: ShortText
    weight: int = Field(ge=0, le=10**12)
    allocated_inr: Rupees
    scales_with_guests: bool = False
    protected: bool = False


class Expense(DomainModel):
    """A committed or paid amount within a category."""

    id: ExpenseRef
    category_id: CategoryRef
    label: ShortText
    amount_inr: int = Field(ge=1, le=2 * MAX_BUDGET_INR)
    protected: bool = False
    source: ExpenseSource = ExpenseSource.USER


class Task(DomainModel):
    """A to-do item with a deadline relative to the event date.

    `offset_days` counts days before the event; negative values are after it (for example,
    cleanup or settling accounts).
    """

    id: TaskRef
    title: ShortText
    category_id: CategoryRef | None = None
    offset_days: int = Field(ge=-MAX_DAYS_AFTER, le=MAX_DAYS_AHEAD)
    due_date: date
    done: bool = False
    compressed: bool = False
    source: TaskSource = TaskSource.CATALOG
    why: Rationale | None = None


class VendorItem(DomainModel):
    """A vendor to book, with a book-by date derived from its lead time."""

    id: VendorRef
    vendor_type: Slug
    name: ShortText
    category_id: CategoryRef | None = None
    status: VendorStatus = VendorStatus.TODO
    lead_days: int = Field(ge=0, le=MAX_LEAD_DAYS)
    book_by: date
    priority: int = Field(ge=1, le=MAX_PRIORITY)
    why: Rationale | None = None
    notes: LongText = ""


class Idea(DomainModel):
    """A personalized suggestion with its reasoning."""

    id: IdeaRef
    title: ShortText
    description: Annotated[str, _Normalized, StringConstraints(min_length=1, max_length=500)]
    category_id: CategoryRef | None = None
    budget_impact: BudgetImpact = BudgetImpact.NEUTRAL
    why: Rationale


class EventPlan(DomainModel):
    """A complete plan: event details, budget, timeline, vendors and ideas."""

    event_type: EventType
    title: ShortText
    city: CityName
    event_date: date
    guest_count: int = Field(ge=1, le=MAX_GUESTS)
    total_budget_inr: int = Field(ge=MIN_BUDGET_INR, le=MAX_BUDGET_INR)
    preferences: LongText = ""
    sub_events: tuple[Slug, ...] = ()
    categories: tuple[BudgetCategory, ...] = Field(min_length=1)
    expenses: tuple[Expense, ...] = ()
    tasks: tuple[Task, ...] = ()
    vendors: tuple[VendorItem, ...] = ()
    ideas: tuple[Idea, ...] = ()
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_invariants(self) -> Self:
        """Validate cross-field rules.

        Returns:
            The validated plan.

        Raises:
            ValueError: If ids repeat, a reference points to an unknown category, or the
                allocations do not add up to the total budget.
        """
        collections: dict[str, Iterable[str]] = {
            "category": (c.id for c in self.categories),
            "expense": (e.id for e in self.expenses),
            "task": (t.id for t in self.tasks),
            "vendor": (v.id for v in self.vendors),
            "idea": (i.id for i in self.ideas),
            "sub-event": self.sub_events,
        }
        for name, ids in collections.items():
            duplicate = _first_duplicate(ids)
            if duplicate is not None:
                raise ValueError(f"duplicate {name} id: {duplicate}")

        known = {c.id for c in self.categories}
        optional_refs = [
            *(t.category_id for t in self.tasks),
            *(v.category_id for v in self.vendors),
            *(i.category_id for i in self.ideas),
        ]
        references = [e.category_id for e in self.expenses] + [
            ref for ref in optional_refs if ref is not None
        ]
        unknown = next((ref for ref in references if ref not in known), None)
        if unknown is not None:
            raise ValueError(f"unknown category id: {unknown}")

        allocated = sum(c.allocated_inr for c in self.categories)
        if allocated != self.total_budget_inr:
            raise ValueError(
                f"category allocations ({allocated}) must equal the total budget "
                f"({self.total_budget_inr})"
            )
        return self

    def category(self, category_id: CategoryId) -> BudgetCategory:
        """Return a category by id.

        Raises:
            NotFoundError: If no category has this id.
        """
        found = next((c for c in self.categories if c.id == category_id), None)
        if found is None:
            raise NotFoundError(f"No budget category '{category_id}' in this plan.")
        return found

    def expense(self, expense_id: ExpenseId) -> Expense:
        """Return an expense by id.

        Raises:
            NotFoundError: If no expense has this id.
        """
        found = next((e for e in self.expenses if e.id == expense_id), None)
        if found is None:
            raise NotFoundError(f"No expense '{expense_id}' in this plan.")
        return found

    def committed_inr(self, category_id: CategoryId) -> int:
        """Total of all expenses in a category."""
        return sum(e.amount_inr for e in self.expenses if e.category_id == category_id)

    def protected_floor_inr(self, category_id: CategoryId) -> int:
        """Total of protected expenses in a category: its allocation may not go below this."""
        return sum(
            e.amount_inr for e in self.expenses if e.category_id == category_id and e.protected
        )

    def with_changes(self, **changes: object) -> "EventPlan":
        """Return a validated copy of the plan with some fields replaced."""
        return evolve(self, **changes)


def validate_event_date(event_date: date, today: date) -> None:
    """Check that an event date is between today and three years ahead.

    Args:
        event_date: Proposed event date.
        today: Current date in India, from a `Clock`.

    Raises:
        InvalidInputError: If the date is in the past or too far ahead.
    """
    if event_date < today:
        raise InvalidInputError("The event date can't be in the past.", code="event_date_in_past")
    if event_date > today + timedelta(days=MAX_DAYS_AHEAD):
        raise InvalidInputError(
            "The event date must be within three years from today.", code="event_date_too_far"
        )


def _first_duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None

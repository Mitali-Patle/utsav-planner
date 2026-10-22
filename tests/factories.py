"""Small builders that keep tests short and readable."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from app.domain.models import (
    BudgetCategory,
    CategoryId,
    EventPlan,
    EventType,
    Expense,
    ExpenseId,
)

BIRTHDAY_ALLOCATIONS: Mapping[str, int] = {
    "catering": 5_000,
    "decor": 3_000,
    "cake": 2_000,
    "other": 5_000,
}


def make_plan(
    *,
    allocations: Mapping[str, int] = BIRTHDAY_ALLOCATIONS,
    expenses: Sequence[tuple[str, str, int, bool]] = (),
    protected: Iterable[str] = (),
    scales_with_guests: Iterable[str] = (),
    guest_count: int = 30,
    event_type: EventType = EventType.BIRTHDAY,
    event_date: date = date(2026, 12, 12),
) -> EventPlan:
    """Build a valid plan whose total is the sum of the allocations.

    `expenses` items are (category_id, label, amount_inr, protected).
    """
    protected_ids = set(protected)
    scaled_ids = set(scales_with_guests)
    categories = tuple(
        BudgetCategory(
            id=CategoryId(category_id),
            name=category_id.replace("_", " ").title(),
            weight=max(amount, 1),
            allocated_inr=amount,
            protected=category_id in protected_ids,
            scales_with_guests=category_id in scaled_ids,
        )
        for category_id, amount in allocations.items()
    )
    expense_models = tuple(
        Expense(
            id=ExpenseId(f"exp-{index}"),
            category_id=CategoryId(category_id),
            label=label,
            amount_inr=amount,
            protected=is_protected,
        )
        for index, (category_id, label, amount, is_protected) in enumerate(expenses, start=1)
    )
    return EventPlan(
        event_type=event_type,
        title="Aarav's 8th Birthday",
        city="Pune",
        event_date=event_date,
        guest_count=guest_count,
        total_budget_inr=sum(allocations.values()),
        categories=categories,
        expenses=expense_models,
    )

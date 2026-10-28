"""Budget allocation and summaries: splitting a total across lines and reporting spending.

The AI may suggest *which* categories deserve more or less; code decides the exact rupees.
Expenses live in `expenses`, re-splitting to a new total in `rebalance`, and ways to save
in `savings`.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import Field

from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.models import (
    MAX_BUDGET_INR,
    MIN_BUDGET_INR,
    BudgetCategory,
    CategoryId,
    DomainModel,
    EventPlan,
    evolve,
)
from app.domain.money import format_inr, largest_remainder_split


class EmphasisDirection(StrEnum):
    """Whether a category should get more or less than its baseline share."""

    INCREASE = "increase"
    DECREASE = "decrease"


class EmphasisStrength(StrEnum):
    """How strong an emphasis is. Bounded so a suggestion can never distort the plan."""

    SMALL = "small"
    MEDIUM = "medium"


_EMPHASIS_PERCENT: Mapping[tuple[EmphasisDirection, EmphasisStrength], int] = {
    (EmphasisDirection.INCREASE, EmphasisStrength.SMALL): 110,
    (EmphasisDirection.INCREASE, EmphasisStrength.MEDIUM): 120,
    (EmphasisDirection.DECREASE, EmphasisStrength.SMALL): 90,
    (EmphasisDirection.DECREASE, EmphasisStrength.MEDIUM): 80,
}
_NEUTRAL_PERCENT = 100


class Emphasis(DomainModel):
    """A bounded adjustment to a category's weight, for example from AI personalization."""

    direction: EmphasisDirection
    strength: EmphasisStrength

    @property
    def percent(self) -> int:
        """Weight multiplier in percent."""
        return _EMPHASIS_PERCENT[(self.direction, self.strength)]


class CategorySummary(DomainModel):
    """Allocation and spending for one category."""

    category_id: CategoryId
    name: str
    allocated_inr: int
    spent_inr: int
    remaining_inr: int = Field(ge=0)
    overspent_by_inr: int = Field(ge=0)
    protected: bool


class BudgetSummary(DomainModel):
    """Totals for the whole plan.

    `remaining_inr` is the budget not yet spent. `shortfall_inr` is what the plan needs
    beyond its total: each line needs the larger of its plan and what is already committed
    to it, so a line spent past its plan still leaves the other lines needing theirs.
    """

    total_inr: int
    spent_inr: int
    remaining_inr: int = Field(ge=0)
    shortfall_inr: int = Field(ge=0)
    per_guest_inr: int
    categories: tuple[CategorySummary, ...]

    @property
    def is_over_budget(self) -> bool:
        """Whether the plan needs more money than its total budget."""
        return self.shortfall_inr > 0


def allocate(categories: Sequence[BudgetCategory], total_inr: int) -> tuple[BudgetCategory, ...]:
    """Allocate a total across categories by their weights.

    Args:
        categories: Categories with relative weights. If every weight is zero, the total
            is split equally.
        total_inr: Amount to allocate.

    Returns:
        Categories with `allocated_inr` set; the allocations sum exactly to the total.
    """
    weights = [c.weight for c in categories]
    if sum(weights) == 0:
        weights = [1] * len(categories)
    amounts = largest_remainder_split(total_inr, weights)
    return tuple(
        evolve(category, allocated_inr=amount)
        for category, amount in zip(categories, amounts, strict=True)
    )


def apply_emphasis(
    categories: Sequence[BudgetCategory], emphasis: Mapping[CategoryId, Emphasis]
) -> tuple[BudgetCategory, ...]:
    """Adjust category weights by bounded emphasis (at most ±20 %).

    Args:
        categories: Categories with baseline weights.
        emphasis: Adjustments by category id.

    Returns:
        Categories with adjusted weights. Weights are scaled by 100 so they stay integers.

    Raises:
        NotFoundError: If an emphasis refers to an unknown category.
    """
    unknown = set(emphasis) - {c.id for c in categories}
    if unknown:
        raise NotFoundError(f"No budget category '{sorted(unknown)[0]}' to adjust.")
    return tuple(
        evolve(
            category,
            weight=category.weight
            * (emphasis[category.id].percent if category.id in emphasis else _NEUTRAL_PERCENT),
        )
        for category in categories
    )


def summarize(plan: EventPlan) -> BudgetSummary:
    """Compute totals, remaining balance and overspending.

    Args:
        plan: The plan to summarize.

    Returns:
        The budget summary.

    """
    categories = tuple(
        _summarize_category(category, plan.committed_inr(category.id))
        for category in plan.categories
    )
    spent = sum(e.amount_inr for e in plan.expenses)
    total = plan.total_budget_inr
    return BudgetSummary(
        total_inr=total,
        spent_inr=spent,
        remaining_inr=max(0, total - spent),
        shortfall_inr=max(0, sum(max(c.allocated_inr, c.spent_inr) for c in categories) - total),
        per_guest_inr=total // plan.guest_count,
        categories=categories,
    )


def _summarize_category(category: BudgetCategory, spent: int) -> CategorySummary:
    return CategorySummary(
        category_id=category.id,
        name=category.name,
        allocated_inr=category.allocated_inr,
        spent_inr=spent,
        remaining_inr=max(0, category.allocated_inr - spent),
        overspent_by_inr=max(0, spent - category.allocated_inr),
        protected=category.protected,
    )


def validate_total_budget(total_inr: int) -> None:
    """Check that a total budget is within the allowed range.

    Raises:
        InvalidInputError: If the total is out of range.
    """
    if not MIN_BUDGET_INR <= total_inr <= MAX_BUDGET_INR:
        raise InvalidInputError(
            f"The budget must be between {format_inr(MIN_BUDGET_INR)} and "
            f"{format_inr(MAX_BUDGET_INR)}.",
            code="budget_out_of_range",
        )

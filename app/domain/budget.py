"""Budget engine: allocation, summaries, expenses and rebalancing.

All budget arithmetic lives here. The AI may suggest *which* categories deserve more or
less, or what to cut first; this module decides the exact rupees and explains every
change with a reason code.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import Field

from app.domain.errors import InfeasibleChangeError, InvalidInputError, NotFoundError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_EXPENSES,
    MIN_BUDGET_INR,
    BudgetCategory,
    CategoryId,
    CategoryRef,
    DomainModel,
    EventPlan,
    Expense,
    ExpenseId,
    ExpenseSource,
    Rupees,
    ShortText,
    evolve,
    next_id,
)
from app.domain.money import format_inr, largest_remainder_split
from app.domain.text import normalize_text

EXPENSE_LIMIT_MULTIPLIER = 2


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


class SavingAction(StrEnum):
    """Kind of saving an alternative proposes."""

    REDUCE = "reduce"
    REPLACE = "replace"
    REMOVE = "remove"


class SavingLevel(StrEnum):
    """How much of a category an alternative would free up."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


SAVING_PERCENT: Mapping[SavingLevel, int] = {
    SavingLevel.SMALL: 10,
    SavingLevel.MEDIUM: 25,
    SavingLevel.LARGE: 50,
}
_WHOLE = 100


def estimate_saving(allocated_inr: int, action: SavingAction, level: SavingLevel) -> int:
    """Rupees an alternative would free from a category, rounded down.

    Removing a category frees all of it; reducing or replacing frees 10, 25 or 50 % for a
    small, medium or large change.

    Examples:
        >>> estimate_saving(3_000, SavingAction.REDUCE, SavingLevel.MEDIUM)
        750
        >>> estimate_saving(3_000, SavingAction.REMOVE, SavingLevel.SMALL)
        3000
    """
    percent = _WHOLE if action is SavingAction.REMOVE else SAVING_PERCENT[level]
    return allocated_inr * percent // _WHOLE


class ChangeReason(StrEnum):
    """Why a category's allocation ended up where it did."""

    UNCHANGED = "unchanged"
    ADDED = "added"
    PROTECTED = "protected"
    GUEST_SCALED = "guest_scaled"
    CUT_FIRST = "cut_first"
    PROPORTIONAL = "proportional"
    FLOOR_REACHED = "floor_reached"
    SET = "set"


class Pin(DomainModel):
    """A fixed target amount for a category during a rebalance."""

    amount_inr: Rupees
    reason: ChangeReason


class CategoryChange(DomainModel):
    """Before and after allocation for one category, with the reason."""

    category_id: CategoryId
    before_inr: Rupees
    after_inr: Rupees
    reason: ChangeReason
    committed_inr: int = Field(ge=0)

    @property
    def delta_inr(self) -> int:
        """Change in allocation (negative for a cut)."""
        return self.after_inr - self.before_inr

    @property
    def overcommitted_by_inr(self) -> int:
        """How much existing spending exceeds the new allocation."""
        return max(0, self.committed_inr - self.after_inr)


class Rebalance(DomainModel):
    """Result of rebalancing a plan to a new total.

    When infeasible, `changes` is empty and `shortfall_inr` says exactly how many rupees
    are missing.
    """

    total_inr: int
    shortfall_inr: int = Field(ge=0)
    changes: tuple[CategoryChange, ...]

    @property
    def feasible(self) -> bool:
        """Whether the new total can be met."""
        return self.shortfall_inr == 0

    def apply(self, plan: EventPlan) -> EventPlan:
        """Apply the new allocations to the plan they were computed from.

        Args:
            plan: The plan passed to `rebalance`.

        Returns:
            A new plan with updated allocations and total.

        Raises:
            InfeasibleChangeError: If the rebalance is infeasible.
            ValueError: If the plan's categories differ from the rebalanced ones.
        """
        if not self.feasible:
            raise InfeasibleChangeError(
                f"This change needs {format_inr(self.shortfall_inr)} more than the budget allows.",
                code="budget_shortfall",
                shortfall_inr=self.shortfall_inr,
            )
        after = {change.category_id: change.after_inr for change in self.changes}
        if set(after) != {c.id for c in plan.categories}:
            raise ValueError("rebalance was computed for a different plan")
        categories = tuple(evolve(c, allocated_inr=after[c.id]) for c in plan.categories)
        return plan.with_changes(categories=categories, total_budget_inr=self.total_inr)


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


class NewExpense(DomainModel):
    """Validated input for adding an expense."""

    category_id: CategoryRef
    label: ShortText
    amount_inr: int = Field(ge=1, le=2 * MAX_BUDGET_INR)
    protected: bool = False
    source: ExpenseSource = ExpenseSource.USER


def add_expense(plan: EventPlan, draft: NewExpense) -> tuple[EventPlan, Expense]:
    """Add an expense to a category.

    Going over budget is allowed; the summary then shows the shortfall.

    Args:
        plan: Current plan.
        draft: The expense to add. Its amount must be from 1 to twice the total budget.

    Returns:
        The new plan and the added expense.

    Raises:
        NotFoundError: If the category does not exist.
        InvalidInputError: If the amount is out of range or the plan has the most expenses
            allowed.
    """
    plan.category(draft.category_id)
    _check_expense_amount(draft.amount_inr, plan.total_budget_inr)
    if len(plan.expenses) >= MAX_EXPENSES:
        raise InvalidInputError(
            f"A plan can have at most {MAX_EXPENSES} expenses.", code="too_many_expenses"
        )
    expense = Expense(
        id=ExpenseId(next_id("exp", (e.id for e in plan.expenses))),
        **draft.model_dump(),
    )
    return plan.with_changes(expenses=(*plan.expenses, expense)), expense


def update_expense(
    plan: EventPlan,
    expense_id: ExpenseId,
    *,
    amount_inr: int | None = None,
    label: str | None = None,
) -> EventPlan:
    """Change an unprotected expense's amount or label.

    Raises:
        NotFoundError: If the expense does not exist.
        InvalidInputError: If it is protected or the amount is out of range.
    """
    expense = _unprotected_expense(plan, expense_id)
    changes: dict[str, object] = {}
    if amount_inr is not None:
        _check_expense_amount(amount_inr, plan.total_budget_inr)
        changes["amount_inr"] = amount_inr
    if label is not None:
        changes["label"] = label
    updated = evolve(expense, **changes)
    return plan.with_changes(
        expenses=tuple(updated if e.id == expense_id else e for e in plan.expenses)
    )


def remove_expense(plan: EventPlan, expense_id: ExpenseId) -> EventPlan:
    """Remove an unprotected expense.

    Raises:
        NotFoundError: If the expense does not exist.
        InvalidInputError: If it is protected.
    """
    _unprotected_expense(plan, expense_id)
    return plan.with_changes(expenses=tuple(e for e in plan.expenses if e.id != expense_id))


def set_category_protection(
    plan: EventPlan, category_id: CategoryId, *, protected: bool
) -> EventPlan:
    """Protect or unprotect a category's allocation.

    Raises:
        NotFoundError: If the category does not exist.
    """
    plan.category(category_id)
    return plan.with_changes(
        categories=tuple(
            evolve(c, protected=protected) if c.id == category_id else c for c in plan.categories
        )
    )


def set_expense_protection(plan: EventPlan, expense_id: ExpenseId, *, protected: bool) -> EventPlan:
    """Protect or unprotect an expense.

    Raises:
        NotFoundError: If the expense does not exist.
    """
    plan.expense(expense_id)
    return plan.with_changes(
        expenses=tuple(
            evolve(e, protected=protected) if e.id == expense_id else e for e in plan.expenses
        )
    )


def find_duplicate_expense(
    plan: EventPlan, *, category_id: CategoryId, label: str, amount_inr: int
) -> Expense | None:
    """Find an existing expense with the same category, label and amount.

    Labels are compared case-insensitively after normalization, so repeating the same
    request never adds the same expense twice.
    """
    wanted_label = normalize_text(label).casefold()
    return next(
        (
            e
            for e in plan.expenses
            if e.category_id == category_id
            and e.amount_inr == amount_inr
            and e.label.casefold() == wanted_label
        ),
        None,
    )


def rebalance(
    plan: EventPlan,
    *,
    total_inr: int,
    pins: Mapping[CategoryId, Pin] | None = None,
    cut_priority: Sequence[CategoryId] = (),
) -> Rebalance:
    """Reallocate the plan to a new total while respecting protections.

    Rules, in order:

    1. Protected categories keep their allocation exactly.
    2. Pinned categories (for example, scaled for a new guest count) get their pinned
       amount, raised to their protected-expense floor if needed.
    3. No other category drops below the total of its protected expenses.
    4. Extra money is shared in proportion to current allocations.
    5. Cuts come first from `cut_priority` categories (in order, down to their floor), then
       in proportion to what each remaining category can give.

    Runs in O(n log n) for n categories.

    Args:
        plan: Current plan.
        total_inr: New total budget.
        pins: Fixed amounts for some categories.
        cut_priority: Categories to cut first, most preferred first.

    Returns:
        Per-category before/after amounts with reasons, or the exact shortfall.

    Raises:
        InvalidInputError: If the new total is out of range.
        NotFoundError: If a pin or priority refers to an unknown category.
        InfeasibleChangeError: If extra money must be added but every category is fixed.
    """
    validate_total_budget(total_inr)
    pins = dict(pins or {})
    known = {c.id for c in plan.categories}
    unknown = (set(pins) | set(cut_priority)) - known
    if unknown:
        raise NotFoundError(f"No budget category '{sorted(unknown)[0]}' in this plan.")

    floors = {c.id: plan.protected_floor_inr(c.id) for c in plan.categories}
    fixed = _effective_pins(plan.categories, pins, floors)
    adjustable = [c for c in plan.categories if c.id not in fixed]
    base = {c.id: max(c.allocated_inr, floors[c.id]) for c in adjustable}
    need = total_inr - sum(p.amount_inr for p in fixed.values()) - sum(base.values())

    if need >= 0:
        if need > 0 and not adjustable:
            raise InfeasibleChangeError(
                "Every category is fixed, so the extra budget has nowhere to go.",
                code="no_adjustable_category",
            )
        after = _share_increase(adjustable, base, need)
        cut_first: set[CategoryId] = set()
    else:
        capacity = {cid: base[cid] - floors[cid] for cid in base}
        missing = -need - sum(capacity.values())
        if missing > 0:
            return Rebalance(total_inr=total_inr, shortfall_inr=missing, changes=())
        after, cut_first = _share_cut(adjustable, base, capacity, -need, cut_priority)

    changes: list[CategoryChange] = []
    for category in plan.categories:
        pin = fixed.get(category.id)
        new_amount = pin.amount_inr if pin else after[category.id]
        reason = (
            pin.reason
            if pin
            else _change_reason(
                category.allocated_inr,
                new_amount,
                floor_inr=floors[category.id],
                cut_first=category.id in cut_first,
            )
        )
        changes.append(
            CategoryChange(
                category_id=category.id,
                before_inr=category.allocated_inr,
                after_inr=new_amount,
                reason=reason,
                committed_inr=plan.committed_inr(category.id),
            )
        )
    return Rebalance(total_inr=total_inr, shortfall_inr=0, changes=tuple(changes))


def _effective_pins(
    categories: Sequence[BudgetCategory],
    pins: Mapping[CategoryId, Pin],
    floors: Mapping[CategoryId, int],
) -> dict[CategoryId, Pin]:
    effective: dict[CategoryId, Pin] = {}
    for category in categories:
        if category.protected:
            effective[category.id] = Pin(
                amount_inr=category.allocated_inr, reason=ChangeReason.PROTECTED
            )
        elif category.id in pins:
            pin = pins[category.id]
            floor = floors[category.id]
            effective[category.id] = (
                pin
                if pin.amount_inr >= floor
                else Pin(amount_inr=floor, reason=ChangeReason.FLOOR_REACHED)
            )
    return effective


def _share_increase(
    adjustable: Sequence[BudgetCategory], base: Mapping[CategoryId, int], increase: int
) -> dict[CategoryId, int]:
    if not adjustable:
        return {}
    weights = [base[c.id] for c in adjustable]
    if sum(weights) == 0:
        weights = [c.weight for c in adjustable]
    if sum(weights) == 0:
        weights = [1] * len(adjustable)
    shares = largest_remainder_split(increase, weights)
    return {c.id: base[c.id] + share for c, share in zip(adjustable, shares, strict=True)}


def _share_cut(
    adjustable: Sequence[BudgetCategory],
    base: Mapping[CategoryId, int],
    capacity: Mapping[CategoryId, int],
    cut: int,
    cut_priority: Sequence[CategoryId],
) -> tuple[dict[CategoryId, int], set[CategoryId]]:
    cuts = dict.fromkeys(base, 0)
    remaining = cut
    cut_first: set[CategoryId] = set()
    for category_id in dict.fromkeys(cut_priority):
        if remaining == 0 or category_id not in capacity:
            continue
        take = min(capacity[category_id], remaining)
        if take > 0:
            cuts[category_id] = take
            cut_first.add(category_id)
            remaining -= take

    if remaining > 0:
        ids = [c.id for c in adjustable]
        spare = [capacity[cid] - cuts[cid] for cid in ids]
        for category_id, share in zip(ids, largest_remainder_split(remaining, spare), strict=True):
            cuts[category_id] += share

    return {cid: base[cid] - cuts[cid] for cid in base}, cut_first


def _change_reason(before: int, after: int, *, floor_inr: int, cut_first: bool) -> ChangeReason:
    """Explain an adjustable category's outcome.

    A category that ends exactly at a positive protected-expense floor (cut down to it,
    raised up to it, or held at it) is reported as `FLOOR_REACHED`.
    """
    at_floor = floor_inr > 0 and after == floor_inr
    if after < before:
        if at_floor:
            return ChangeReason.FLOOR_REACHED
        return ChangeReason.CUT_FIRST if cut_first else ChangeReason.PROPORTIONAL
    if at_floor:
        return ChangeReason.FLOOR_REACHED
    return ChangeReason.PROPORTIONAL if after > before else ChangeReason.UNCHANGED


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


def _check_expense_amount(amount_inr: int, total_inr: int) -> None:
    limit = EXPENSE_LIMIT_MULTIPLIER * total_inr
    if not 1 <= amount_inr <= limit:
        raise InvalidInputError(
            f"An expense must be between ₹1 and {format_inr(limit)} (twice the budget).",
            code="expense_amount_out_of_range",
        )


def _unprotected_expense(plan: EventPlan, expense_id: ExpenseId) -> Expense:
    expense = plan.expense(expense_id)
    if expense.protected:
        raise InvalidInputError(
            f"'{expense.label}' is protected. Unprotect it before changing it.",
            code="item_protected",
        )
    return expense

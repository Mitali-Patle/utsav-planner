"""Expenses and protection: what the user records and what changes must keep as it is."""

from pydantic import Field

from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_EXPENSES,
    CategoryId,
    CategoryRef,
    DomainModel,
    EventPlan,
    Expense,
    ExpenseId,
    ExpenseSource,
    ShortText,
    evolve,
    next_id,
)
from app.domain.money import format_inr
from app.domain.text import normalize_text

EXPENSE_LIMIT_MULTIPLIER = 2


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

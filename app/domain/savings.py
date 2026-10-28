"""Ways to save on one budget line, priced exactly as they would be applied.

Essential lines (food, venue, cake, rituals, permits) are cut, never removed.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from app.domain.catalog import ESSENTIAL_CATEGORIES
from app.domain.change_set import ChangeKind, ChangeOperation, ChangeSet, required
from app.domain.models import (
    CategoryId,
    EventPlan,
)


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


def saving_option(
    category_id: str, action: SavingAction, level: SavingLevel
) -> tuple[SavingAction, SavingLevel]:
    """The saving that is actually offered and applied: essential lines are never removed.

    Removing the food or the venue is never a sensible way to close a gap, so a removal
    of an essential line becomes its deepest cut.

    Examples:
        >>> saving_option("catering", SavingAction.REMOVE, SavingLevel.SMALL)
        (<SavingAction.REDUCE: 'reduce'>, <SavingLevel.LARGE: 'large'>)
        >>> saving_option("decor", SavingAction.REMOVE, SavingLevel.SMALL)
        (<SavingAction.REMOVE: 'remove'>, <SavingLevel.SMALL: 'small'>)
    """
    if action is SavingAction.REMOVE and category_id in ESSENTIAL_CATEGORIES:
        return SavingAction.REDUCE, SavingLevel.LARGE
    return action, level


_SAVING_WORDS: Mapping[SavingAction, Mapping[SavingLevel, str]] = MappingProxyType(
    {
        SavingAction.REDUCE: {
            SavingLevel.SMALL: "trim it a little",
            SavingLevel.MEDIUM: "cut it back",
            SavingLevel.LARGE: "cut it back sharply",
        },
        SavingAction.REPLACE: {
            SavingLevel.SMALL: "swap for something a little cheaper",
            SavingLevel.MEDIUM: "swap for a cheaper option",
            SavingLevel.LARGE: "swap for a much cheaper option",
        },
    }
)


def describe_saving(action: SavingAction, level: SavingLevel) -> str:
    """Plain words for what a saving does to its line.

    Examples:
        >>> describe_saving(SavingAction.REDUCE, SavingLevel.LARGE)
        'cut it back sharply'
        >>> describe_saving(SavingAction.REMOVE, SavingLevel.SMALL)
        'drop it entirely'
    """
    if action is SavingAction.REMOVE:
        return "drop it entirely"
    return _SAVING_WORDS[action][level]


def saving_change(
    plan: EventPlan, category_id: CategoryId, action: SavingAction, level: SavingLevel
) -> ChangeSet | None:
    """The change that frees an alternative's saving from one budget line.

    The line is set to its plan minus `estimate_saving`, but never below what is already
    spent on it: cutting the plan for money already committed frees nothing. The rebalance
    then gives the freed money first to lines spent past their plan.

    Args:
        plan: The saved plan.
        category_id: Line to save on.
        action: Reduce, replace or remove.
        level: Size of a reduction or replacement.

    Returns:
        A one-operation change set, or None if the line is protected or has nothing to free.

    Raises:
        NotFoundError: If the plan has no such line.
    """
    category = plan.category(category_id)
    action, level = saving_option(category_id, action, level)
    target = max(
        category.allocated_inr - estimate_saving(category.allocated_inr, action, level),
        plan.committed_inr(category.id),
    )
    if category.protected or target >= category.allocated_inr:
        return None
    operation = ChangeOperation(
        kind=ChangeKind.SET_CATEGORY_BUDGET, category_id=category.id, amount_inr=target
    )
    return ChangeSet(operations=(operation,))


def freed_inr(
    plan: EventPlan, category_id: CategoryId, action: SavingAction, level: SavingLevel
) -> int:
    """Rupees `saving_change` would take from a line's plan, or 0 if it changes nothing.

    Raises:
        NotFoundError: If the plan has no such line.
    """
    change_set = saving_change(plan, category_id, action, level)
    if change_set is None:
        return 0
    return plan.category(category_id).allocated_inr - required(change_set.operations[0].amount_inr)

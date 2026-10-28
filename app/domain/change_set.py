"""The language of plan changes: operation kinds, one operation and a validated set.

The AI (or the change form) produces a `ChangeSet`; `app.domain.changes.propose` applies it.
"""

from collections import Counter
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import Field, model_validator

from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_DAYS_AFTER,
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    CategoryRef,
    DomainModel,
    ExpenseRef,
    ShortText,
)

MAX_OPERATIONS = 5


class ChangeKind(StrEnum):
    """The operations a change request can contain."""

    SET_TOTAL_BUDGET = "set_total_budget"
    ADJUST_TOTAL_BUDGET = "adjust_total_budget"
    ADD_EXPENSE = "add_expense"
    UPDATE_EXPENSE = "update_expense"
    REMOVE_EXPENSE = "remove_expense"
    PROTECT = "protect"
    UNPROTECT = "unprotect"
    SET_GUEST_COUNT = "set_guest_count"
    ADJUST_GUEST_COUNT = "adjust_guest_count"
    SHIFT_DATE_DAYS = "shift_date_days"
    SET_DATE = "set_date"
    ADD_TASK = "add_task"
    ADD_CATEGORY = "add_category"
    SET_CATEGORY_BUDGET = "set_category_budget"


_PROTECTION_KINDS = frozenset({ChangeKind.PROTECT, ChangeKind.UNPROTECT})


_ADJUST_KINDS = frozenset({ChangeKind.ADJUST_TOTAL_BUDGET, ChangeKind.ADJUST_GUEST_COUNT})


_FIELDS: Mapping[ChangeKind, tuple[frozenset[str], frozenset[str]]] = MappingProxyType(
    {
        # kind: (required fields, optional fields)
        ChangeKind.SET_TOTAL_BUDGET: (frozenset({"amount_inr"}), frozenset()),
        ChangeKind.ADJUST_TOTAL_BUDGET: (frozenset({"delta"}), frozenset()),
        ChangeKind.ADD_EXPENSE: (frozenset({"category_id", "label", "amount_inr"}), frozenset()),
        ChangeKind.UPDATE_EXPENSE: (frozenset({"expense_id", "amount_inr"}), frozenset()),
        ChangeKind.REMOVE_EXPENSE: (frozenset({"expense_id"}), frozenset()),
        ChangeKind.PROTECT: (frozenset(), frozenset({"category_id", "expense_id"})),
        ChangeKind.UNPROTECT: (frozenset(), frozenset({"category_id", "expense_id"})),
        ChangeKind.SET_GUEST_COUNT: (frozenset({"guest_count"}), frozenset()),
        ChangeKind.ADJUST_GUEST_COUNT: (frozenset({"delta"}), frozenset()),
        ChangeKind.SHIFT_DATE_DAYS: (frozenset({"days"}), frozenset()),
        ChangeKind.SET_DATE: (frozenset({"new_date"}), frozenset()),
        ChangeKind.ADD_TASK: (frozenset({"label", "days"}), frozenset({"category_id"})),
        ChangeKind.ADD_CATEGORY: (frozenset({"label", "amount_inr"}), frozenset()),
        ChangeKind.SET_CATEGORY_BUDGET: (frozenset({"category_id", "amount_inr"}), frozenset()),
    }
)


_OPTIONAL_FIELDS = (
    "amount_inr",
    "category_id",
    "expense_id",
    "label",
    "guest_count",
    "days",
    "delta",
    "new_date",
)


_SINGLETON_GROUPS: Mapping[str, frozenset[ChangeKind]] = MappingProxyType(
    {
        "budget total": frozenset({ChangeKind.SET_TOTAL_BUDGET, ChangeKind.ADJUST_TOTAL_BUDGET}),
        "guest count": frozenset({ChangeKind.SET_GUEST_COUNT, ChangeKind.ADJUST_GUEST_COUNT}),
        "event date": frozenset({ChangeKind.SHIFT_DATE_DAYS, ChangeKind.SET_DATE}),
    }
)


class ChangeOperation(DomainModel):
    """One structured change. Only the fields its `kind` needs may be set.

    `days` means days to move the event for `shift_date_days` (negative is earlier) and days
    before the event for `add_task` (negative is after it). `protect` and `unprotect` take
    exactly one of `category_id` or `expense_id`, the fields models naturally use for them.
    """

    kind: ChangeKind
    amount_inr: int | None = Field(default=None, ge=0, le=2 * MAX_BUDGET_INR)
    category_id: CategoryRef | None = None
    expense_id: ExpenseRef | None = None
    label: ShortText | None = None
    guest_count: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    days: int | None = Field(default=None, ge=-MAX_DAYS_AHEAD, le=MAX_DAYS_AHEAD)
    delta: int | None = Field(default=None, ge=-MAX_BUDGET_INR, le=MAX_BUDGET_INR)
    new_date: date | None = None

    @model_validator(mode="after")
    def check_fields_for_kind(self) -> Self:
        """Require exactly the fields this kind uses.

        Raises:
            ValueError: If a required field is missing or an unrelated one is set.
        """
        required, optional = _FIELDS[self.kind]
        present = {name for name in _OPTIONAL_FIELDS if getattr(self, name) is not None}
        missing = sorted(required - present)
        if missing:
            raise ValueError(f"{self.kind} needs {', '.join(missing)}")
        unexpected = sorted(present - required - optional)
        if unexpected:
            raise ValueError(f"{self.kind} does not use {', '.join(unexpected)}")
        if self.kind in _PROTECTION_KINDS and (self.category_id is None) == (
            self.expense_id is None
        ):
            raise ValueError(f"{self.kind} needs exactly one of category_id or expense_id")
        return self

    @model_validator(mode="after")
    def check_values_for_kind(self) -> Self:
        """Reject values that are valid numbers but mean nothing for this kind (a zero shift).

        Raises:
            ValueError: Naming the value and what it needs to be.
        """
        if self.amount_inr == 0 and self.kind is not ChangeKind.SET_CATEGORY_BUDGET:
            raise ValueError(f"{self.kind} needs an amount of at least ₹1")
        if self.kind is ChangeKind.SHIFT_DATE_DAYS and self.days == 0:
            raise ValueError("shift_date_days needs a non-zero number of days")
        if self.kind in _ADJUST_KINDS and self.delta == 0:
            raise ValueError(f"{self.kind} needs a non-zero delta")
        if self.kind is ChangeKind.ADD_TASK and (self.days or 0) < -MAX_DAYS_AFTER:
            raise ValueError(f"tasks can be at most {MAX_DAYS_AFTER} days after the event")
        return self

    @property
    def protection_target(self) -> str | None:
        """The category or expense id a protect/unprotect operation refers to."""
        return self.expense_id or self.category_id


class ChangeSet(DomainModel):
    """A complete change request: up to five operations and an optional cut order."""

    operations: tuple[ChangeOperation, ...] = Field(min_length=1, max_length=MAX_OPERATIONS)
    cut_priority: tuple[CategoryRef, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def check_conflicts(self) -> Self:
        """Reject requests that set the same thing twice or protect and unprotect one item.

        Returns:
            The validated change set.

        Raises:
            ValueError: On conflicting operations.
        """
        kinds = Counter(op.kind for op in self.operations)
        for name, group in _SINGLETON_GROUPS.items():
            if sum(kinds[kind] for kind in group) > 1:
                raise ValueError(f"conflicting operations: the {name} is set more than once")
        protected = {op.protection_target for op in self.of_kind(ChangeKind.PROTECT)}
        unprotected = {op.protection_target for op in self.of_kind(ChangeKind.UNPROTECT)}
        both = sorted(t for t in protected & unprotected if t is not None)
        if both:
            raise ValueError(f"conflicting operations: {both[0]} is protected and unprotected")
        return self

    def of_kind(self, *kinds: ChangeKind) -> tuple[ChangeOperation, ...]:
        """Operations of the given kinds, in request order."""
        return tuple(op for op in self.operations if op.kind in kinds)


def required[T](value: T | None) -> T:
    """Narrow an optional field that `ChangeOperation` validation guarantees is set."""
    if value is None:  # pragma: no cover - unreachable after model validation
        raise ValueError("operation is missing a required field")
    return value

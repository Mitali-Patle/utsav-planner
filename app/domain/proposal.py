"""What a proposed change would do, for the user to approve or reject."""

from datetime import date
from enum import StrEnum

from pydantic import Field

from app.domain.budget import BudgetSummary
from app.domain.change_set import ChangeKind
from app.domain.models import (
    CategoryId,
    DomainModel,
    EventPlan,
    ExpenseId,
    Task,
)
from app.domain.rebalance import CategoryChange


class FieldChange(DomainModel):
    """A changed event-level value."""

    field: str
    before: int | date
    after: int | date


class ExpenseAction(StrEnum):
    """What happened to an expense."""

    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"


class ExpenseChange(DomainModel):
    """An applied expense change."""

    action: ExpenseAction
    expense_id: ExpenseId
    label: str
    category_id: CategoryId
    before_inr: int | None
    after_inr: int | None


class SkipReason(StrEnum):
    """Why an operation was not applied."""

    DUPLICATE = "duplicate"
    PROTECTED = "protected"


class SkippedOperation(DomainModel):
    """An operation left out of the proposal, with the reason."""

    kind: ChangeKind
    label: str
    reason: SkipReason


class ScheduleChange(DomainModel):
    """A task deadline or vendor book-by date that moved."""

    item_id: str
    title: str
    before: date
    after: date
    flagged: bool = Field(description="Compressed task or urgent vendor after the change.")


class Proposal(DomainModel):
    """Everything a change would do, for the user to approve or reject.

    `after` is None when the change cannot fit the budget; `shortfall_inr` then says by
    how much.
    """

    base_version: int
    after: EventPlan | None
    shortfall_inr: int = Field(ge=0)
    field_changes: tuple[FieldChange, ...] = ()
    budget_changes: tuple[CategoryChange, ...] = ()
    expense_changes: tuple[ExpenseChange, ...] = ()
    task_changes: tuple[ScheduleChange, ...] = ()
    vendor_changes: tuple[ScheduleChange, ...] = ()
    new_tasks: tuple[Task, ...] = ()
    skipped: tuple[SkippedOperation, ...] = ()
    protected_kept: tuple[str, ...] = ()
    summary_before: BudgetSummary
    summary_after: BudgetSummary | None

    @property
    def feasible(self) -> bool:
        """Whether the change fits and can be approved."""
        return self.after is not None

    @property
    def has_changes(self) -> bool:
        """Whether approving would change anything."""
        return bool(
            self.field_changes
            or any(c.delta_inr for c in self.budget_changes)
            or self.expense_changes
            or self.task_changes
            or self.vendor_changes
            or self.new_tasks
        )

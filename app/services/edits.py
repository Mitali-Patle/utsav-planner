"""Use case: direct edits the user makes in forms, saved immediately.

These are the user's own explicit actions, so they need no approval step. Each edit loads
the plan, applies one domain function and saves the next version in a single transaction.
"""

from collections.abc import Callable

from app.domain.budget import (
    NewExpense,
    add_expense,
    remove_expense,
    set_category_protection,
    set_expense_protection,
    update_expense,
)
from app.domain.models import CategoryId, EventPlan, ExpenseId, TaskId, VendorId, VendorStatus
from app.domain.timeline import set_task_done
from app.domain.vendors import set_vendor_status
from app.persistence.repositories import Database, StoredEvent


class PlanEditor:
    """Direct, versioned edits to a saved plan."""

    def __init__(self, db: Database) -> None:
        """Wire the service."""
        self._db = db

    def add_expense(self, session_id: str, event_id: str, draft: NewExpense) -> StoredEvent:
        """Add an expense (going over budget is allowed and shown, not blocked)."""
        return self._edit(session_id, event_id, lambda plan: add_expense(plan, draft)[0])

    def update_expense(
        self, session_id: str, event_id: str, expense_id: ExpenseId, amount_inr: int
    ) -> StoredEvent:
        """Change an unprotected expense's amount."""
        return self._edit(
            session_id,
            event_id,
            lambda plan: update_expense(plan, expense_id, amount_inr=amount_inr),
        )

    def remove_expense(self, session_id: str, event_id: str, expense_id: ExpenseId) -> StoredEvent:
        """Remove an unprotected expense."""
        return self._edit(session_id, event_id, lambda plan: remove_expense(plan, expense_id))

    def protect_category(
        self, session_id: str, event_id: str, category_id: CategoryId, *, protected: bool
    ) -> StoredEvent:
        """Protect or unprotect a budget category."""
        return self._edit(
            session_id,
            event_id,
            lambda plan: set_category_protection(plan, category_id, protected=protected),
        )

    def protect_expense(
        self, session_id: str, event_id: str, expense_id: ExpenseId, *, protected: bool
    ) -> StoredEvent:
        """Protect or unprotect an expense."""
        return self._edit(
            session_id,
            event_id,
            lambda plan: set_expense_protection(plan, expense_id, protected=protected),
        )

    def set_task_done(
        self, session_id: str, event_id: str, task_id: TaskId, *, done: bool
    ) -> StoredEvent:
        """Tick or untick a task."""
        return self._edit(
            session_id, event_id, lambda plan: set_task_done(plan, task_id, done=done)
        )

    def set_vendor_status(
        self, session_id: str, event_id: str, vendor_id: VendorId, status: VendorStatus
    ) -> StoredEvent:
        """Move a vendor along to-do, contacted and booked."""
        return self._edit(
            session_id, event_id, lambda plan: set_vendor_status(plan, vendor_id, status)
        )

    def _edit(
        self, session_id: str, event_id: str, change: Callable[[EventPlan], EventPlan]
    ) -> StoredEvent:
        """Load, change and save in one transaction.

        Raises:
            NotFoundError: If the event or item does not exist or is not this session's.
            InvalidInputError: If the domain rejects the change.
        """
        with self._db.transaction() as repos:
            stored = repos.events.get(event_id, session_id)
            return repos.events.save(
                event_id, session_id, change(stored.plan), expected_version=stored.version
            )

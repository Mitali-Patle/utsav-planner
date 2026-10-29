"""Smart Change Manager core: structured changes become before/after proposals.

The AI (or a form, when the AI is unavailable) produces a `ChangeSet`. `propose` applies it
to a copy of the plan in a fixed order, so the result never depends on how the request was
phrased, and returns a `Proposal` describing every effect. Nothing is saved until the user
approves and `accept` is called; rejecting needs no code because the plan is never
modified.

Order of application:

1. protect / unprotect, so protections hold before anything is cut;
2. date change, then deadlines and book-by dates move with it;
3. guest count, which pins guest-scaled categories at their new amounts;
4. expenses, where duplicates and protected items are skipped and reported;
5. custom categories;
6. one rebalance when the total, guests, custom categories or cut priority require it;
7. new tasks, plus follow-up tasks for vendors who are already booked.

The steps themselves are in `change_steps`; this module runs them and describes the result.
"""

from collections.abc import Mapping, Sequence
from datetime import date

from app.domain.budget import summarize
from app.domain.catalog import TaskSpec
from app.domain.change_set import ChangeKind, ChangeSet, required
from app.domain.change_steps import (
    add_tasks,
    apply_custom_categories,
    apply_date,
    apply_expenses,
    apply_guests,
    apply_line_amounts,
    apply_protection,
    target_total,
)
from app.domain.errors import InfeasibleChangeError, StaleProposalError
from app.domain.expenses import set_category_protection
from app.domain.models import CategoryId, EventPlan
from app.domain.proposal import (
    ExpenseAction,
    ExpenseChange,
    FieldChange,
    Proposal,
    ScheduleChange,
    SkippedOperation,
)
from app.domain.rebalance import CategoryChange, Pin, Rebalance, rebalance


def propose(plan: EventPlan, change_set: ChangeSet, today: date) -> Proposal:
    """Work out the full effect of a change without modifying the plan.

    Args:
        plan: The saved plan.
        change_set: Validated operations.
        today: Current date in India.

    Returns:
        A proposal with before/after details, or the shortfall if it cannot fit.

    Raises:
        NotFoundError: If an operation refers to an unknown item.
        InvalidInputError: If a value breaks a rule (for example, a date in the past).
        InfeasibleChangeError: If extra budget has nowhere to go because everything is fixed.
    """
    deferred = _protections_after_rebalance(change_set)
    working = apply_protection(plan, change_set, defer=deferred)
    follow_ups: list[TaskSpec] = []
    skipped: list[SkippedOperation] = []

    working = apply_date(working, change_set, today, follow_ups)
    working, pins = apply_guests(working, change_set, today, follow_ups)
    working = apply_expenses(working, change_set, skipped)
    working, pins = apply_custom_categories(working, change_set, pins)
    pins = apply_line_amounts(working, change_set, pins, skipped)

    budget_changes: tuple[CategoryChange, ...] = ()
    result = _rebalance_if_needed(working, change_set, pins)
    if result is not None:
        if not result.feasible:
            return _infeasible(plan, working, result, skipped)
        working = result.apply(working)
        budget_changes = result.changes
    for category_id in deferred:
        working = set_category_protection(working, category_id, protected=True)

    working = add_tasks(working, change_set, follow_ups, today, skipped)
    return _build_proposal(plan, working, budget_changes, skipped)


def _protections_after_rebalance(change_set: ChangeSet) -> frozenset[CategoryId]:
    """Lines both set and protected in one request.

    They are protected after the rebalance, so they keep their new amount; protecting them
    first would pin the old one.
    """
    set_lines = {
        required(op.category_id) for op in change_set.of_kind(ChangeKind.SET_CATEGORY_BUDGET)
    }
    return frozenset(
        op.category_id
        for op in change_set.of_kind(ChangeKind.PROTECT)
        if op.category_id is not None and op.category_id in set_lines
    )


def _rebalance_if_needed(
    plan: EventPlan, change_set: ChangeSet, pins: Mapping[CategoryId, Pin]
) -> Rebalance | None:
    """Re-split the budget when the total, pinned lines or cut priority require it."""
    target = target_total(plan, change_set)
    if target == plan.total_budget_inr and not pins and not change_set.cut_priority:
        return None
    return rebalance(plan, total_inr=target, pins=pins, cut_priority=change_set.cut_priority)


def _infeasible(
    plan: EventPlan, working: EventPlan, result: Rebalance, skipped: Sequence[SkippedOperation]
) -> Proposal:
    """A proposal that cannot be approved, stating the shortfall."""
    return Proposal(
        base_version=plan.version,
        after=None,
        shortfall_inr=result.shortfall_inr,
        field_changes=_field_changes(plan, working),
        skipped=tuple(skipped),
        summary_before=summarize(plan),
        summary_after=None,
    )


def accept(proposal: Proposal, current: EventPlan) -> EventPlan:
    """Approve a proposal against the plan as it is now.

    Args:
        proposal: The proposal the user approved.
        current: The currently saved plan.

    Returns:
        The new plan, one version higher.

    Raises:
        StaleProposalError: If the plan changed after the proposal was made.
        InfeasibleChangeError: If the proposal has a shortfall.
    """
    if current.version != proposal.base_version:
        raise StaleProposalError(
            "The plan changed after this preview was made. Please review the change again."
        )
    if proposal.after is None:
        raise InfeasibleChangeError(
            "This change doesn't fit the budget.",
            code="budget_shortfall",
            shortfall_inr=proposal.shortfall_inr,
        )
    return proposal.after.with_changes(version=current.version + 1)


def _build_proposal(
    before: EventPlan,
    after: EventPlan,
    budget_changes: tuple[CategoryChange, ...],
    skipped: list[SkippedOperation],
) -> Proposal:
    old_task_ids = {t.id for t in before.tasks}
    return Proposal(
        base_version=before.version,
        after=after,
        shortfall_inr=0,
        field_changes=_field_changes(before, after),
        budget_changes=budget_changes,
        expense_changes=_expense_changes(before, after),
        task_changes=_task_changes(before, after),
        vendor_changes=_vendor_changes(before, after),
        new_tasks=tuple(t for t in after.tasks if t.id not in old_task_ids),
        skipped=tuple(skipped),
        protected_kept=_protected_names(after),
        summary_before=summarize(before),
        summary_after=summarize(after),
    )


def _task_changes(before: EventPlan, after: EventPlan) -> tuple[ScheduleChange, ...]:
    """Existing tasks whose deadline moved; new tasks are listed separately."""
    old = {t.id: t for t in before.tasks}
    return tuple(
        ScheduleChange(
            item_id=t.id,
            title=t.title,
            before=old[t.id].due_date,
            after=t.due_date,
            flagged=t.compressed,
        )
        for t in after.tasks
        if t.id in old and old[t.id].due_date != t.due_date
    )


def _vendor_changes(before: EventPlan, after: EventPlan) -> tuple[ScheduleChange, ...]:
    """Vendors whose book-by date moved (a change never adds or removes vendors)."""
    old = {v.id: v for v in before.vendors}
    return tuple(
        ScheduleChange(
            item_id=v.id,
            title=v.name,
            before=old[v.id].book_by,
            after=v.book_by,
            flagged=v.urgent,
        )
        for v in after.vendors
        if old[v.id].book_by != v.book_by
    )


def _protected_names(plan: EventPlan) -> tuple[str, ...]:
    """Budget lines and expenses the change kept exactly as they were."""
    return (
        *(c.name for c in plan.categories if c.protected),
        *(e.label for e in plan.expenses if e.protected),
    )


def _field_changes(before: EventPlan, after: EventPlan) -> tuple[FieldChange, ...]:
    pairs: list[tuple[str, int | date, int | date]] = [
        ("total_budget_inr", before.total_budget_inr, after.total_budget_inr),
        ("guest_count", before.guest_count, after.guest_count),
        ("event_date", before.event_date, after.event_date),
    ]
    return tuple(
        FieldChange(field=name, before=old, after=new) for name, old, new in pairs if old != new
    )


def _expense_changes(before: EventPlan, after: EventPlan) -> tuple[ExpenseChange, ...]:
    old = {e.id: e for e in before.expenses}
    new = {e.id: e for e in after.expenses}
    changes = [
        ExpenseChange(
            action=ExpenseAction.ADDED if expense_id not in old else ExpenseAction.UPDATED,
            expense_id=expense_id,
            label=expense.label,
            category_id=expense.category_id,
            before_inr=old[expense_id].amount_inr if expense_id in old else None,
            after_inr=expense.amount_inr,
        )
        for expense_id, expense in new.items()
        if expense_id not in old or old[expense_id] != expense
    ]
    changes += [
        ExpenseChange(
            action=ExpenseAction.REMOVED,
            expense_id=expense_id,
            label=expense.label,
            category_id=expense.category_id,
            before_inr=expense.amount_inr,
            after_inr=None,
        )
        for expense_id, expense in old.items()
        if expense_id not in new
    ]
    return tuple(changes)

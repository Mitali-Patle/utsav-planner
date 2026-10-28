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
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from app.domain.budget import summarize
from app.domain.catalog import CONTINGENCY_ID, TaskSpec
from app.domain.change_set import ChangeKind, ChangeOperation, ChangeSet, required
from app.domain.errors import InfeasibleChangeError, InvalidInputError, StaleProposalError
from app.domain.expenses import (
    NewExpense,
    add_expense,
    find_duplicate_expense,
    remove_expense,
    set_category_protection,
    set_expense_protection,
    update_expense,
)
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_CUSTOM_CATEGORIES,
    MAX_GUESTS,
    MAX_TASKS,
    BudgetCategory,
    CategoryId,
    EventPlan,
    ExpenseSource,
    TaskSource,
    VendorItem,
    VendorStatus,
    id_sequence,
    validate_event_date,
)
from app.domain.proposal import (
    ExpenseAction,
    ExpenseChange,
    FieldChange,
    Proposal,
    ScheduleChange,
    SkippedOperation,
    SkipReason,
)
from app.domain.rebalance import CategoryChange, ChangeReason, Pin, Rebalance, rebalance
from app.domain.text import normalize_text
from app.domain.timeline import build_tasks, reschedule_tasks
from app.domain.vendors import reschedule_vendors


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
    working = _apply_protection(plan, change_set, defer=deferred)
    follow_ups: list[TaskSpec] = []
    skipped: list[SkippedOperation] = []

    working = _apply_date(working, change_set, today, follow_ups)
    working, pins = _apply_guests(working, change_set, today, follow_ups)
    working = _apply_expenses(working, change_set, skipped)
    working, pins = _apply_custom_categories(working, change_set, pins)
    pins = _apply_line_amounts(working, change_set, pins, skipped)

    budget_changes: tuple[CategoryChange, ...] = ()
    result = _rebalance_if_needed(working, change_set, pins)
    if result is not None:
        if not result.feasible:
            return _infeasible(plan, working, result, skipped)
        working = result.apply(working)
        budget_changes = result.changes
    for category_id in deferred:
        working = set_category_protection(working, category_id, protected=True)

    working = _add_tasks(working, change_set, follow_ups, today, skipped)
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
    target = _target_total(plan, change_set)
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


def _apply_protection(
    plan: EventPlan, change_set: ChangeSet, *, defer: frozenset[CategoryId] = frozenset()
) -> EventPlan:
    for op in change_set.of_kind(ChangeKind.PROTECT, ChangeKind.UNPROTECT):
        protected = op.kind is ChangeKind.PROTECT
        if protected and op.category_id in defer:
            continue
        if op.expense_id is not None:
            plan = set_expense_protection(plan, op.expense_id, protected=protected)
        else:
            plan = set_category_protection(plan, required(op.category_id), protected=protected)
    return plan


def _apply_date(
    plan: EventPlan, change_set: ChangeSet, today: date, follow_ups: list[TaskSpec]
) -> EventPlan:
    date_ops = change_set.of_kind(ChangeKind.SHIFT_DATE_DAYS, ChangeKind.SET_DATE)
    if not date_ops:
        return plan
    op = date_ops[0]
    new_date = op.new_date or plan.event_date + timedelta(days=op.days or 0)
    validate_event_date(new_date, today)
    if new_date == plan.event_date:
        return plan
    for vendor in _booked(plan.vendors):
        follow_ups.append(
            _follow_up(
                f"Confirm the new date {new_date:%d %b} with {vendor.name}", vendor, new_date, today
            )
        )
    return plan.with_changes(
        event_date=new_date,
        tasks=reschedule_tasks(plan.tasks, new_date, today),
        vendors=reschedule_vendors(plan.vendors, new_date, today),
    )


def _apply_guests(
    plan: EventPlan, change_set: ChangeSet, today: date, follow_ups: list[TaskSpec]
) -> tuple[EventPlan, dict[CategoryId, Pin]]:
    new_count = _target_guest_count(plan, change_set)
    if new_count == plan.guest_count:
        return plan, {}
    old_count = plan.guest_count
    pins = {
        c.id: Pin(
            amount_inr=min(MAX_BUDGET_INR, -(-c.allocated_inr * new_count // old_count)),
            reason=ChangeReason.GUEST_SCALED,
        )
        for c in plan.categories
        if c.scales_with_guests and not c.protected
    }
    scaled = set(pins)
    if new_count < old_count and _only_guests_affect_budget(change_set):
        pins |= _hold_all_but_contingency(plan.categories, pins)

    for vendor in _booked(plan.vendors):
        if vendor.category_id in scaled:
            follow_ups.append(
                _follow_up(
                    f"Update {vendor.name}: new guest count {new_count}",
                    vendor,
                    plan.event_date,
                    today,
                )
            )
    return plan.with_changes(guest_count=new_count), pins


def _only_guests_affect_budget(change_set: ChangeSet) -> bool:
    return not change_set.cut_priority and not change_set.of_kind(
        ChangeKind.SET_TOTAL_BUDGET, ChangeKind.ADJUST_TOTAL_BUDGET, ChangeKind.ADD_CATEGORY
    )


def _target_total(plan: EventPlan, change_set: ChangeSet) -> int:
    """New total budget: an absolute amount, or the current total plus a stated change."""
    for op in change_set.of_kind(ChangeKind.SET_TOTAL_BUDGET, ChangeKind.ADJUST_TOTAL_BUDGET):
        if op.kind is ChangeKind.SET_TOTAL_BUDGET:
            return required(op.amount_inr)
        return plan.total_budget_inr + required(op.delta)
    return plan.total_budget_inr


def _target_guest_count(plan: EventPlan, change_set: ChangeSet) -> int:
    """New guest count, computed by code for relative requests such as "10 more guests".

    Raises:
        InvalidInputError: If the result is outside 1 to the maximum.
    """
    for op in change_set.of_kind(ChangeKind.SET_GUEST_COUNT, ChangeKind.ADJUST_GUEST_COUNT):
        count = (
            required(op.guest_count)
            if op.kind is ChangeKind.SET_GUEST_COUNT
            else plan.guest_count + required(op.delta)
        )
        if not 1 <= count <= MAX_GUESTS:
            raise InvalidInputError(
                f"The guest count would become {count}; it must be between 1 and {MAX_GUESTS}.",
                code="guest_count_out_of_range",
            )
        return count
    return plan.guest_count


def _hold_all_but_contingency(
    categories: Iterable[BudgetCategory], scaled: Mapping[CategoryId, Pin]
) -> dict[CategoryId, Pin]:
    """Pin every other adjustable category so freed money goes to contingency.

    Falls back to proportional sharing (no extra pins) when contingency is missing or
    protected.
    """
    by_id = {c.id: c for c in categories}
    contingency = by_id.get(CONTINGENCY_ID)
    if contingency is None or contingency.protected or contingency.id in scaled:
        return {}
    return {
        c.id: Pin(amount_inr=c.allocated_inr, reason=ChangeReason.UNCHANGED)
        for c in by_id.values()
        if c.id not in scaled and c.id != CONTINGENCY_ID and not c.protected
    }


def _apply_expenses(
    plan: EventPlan, change_set: ChangeSet, skipped: list[SkippedOperation]
) -> EventPlan:
    expense_kinds = (ChangeKind.ADD_EXPENSE, ChangeKind.UPDATE_EXPENSE, ChangeKind.REMOVE_EXPENSE)
    for op in change_set.of_kind(*expense_kinds):
        if op.kind is ChangeKind.ADD_EXPENSE:
            plan = _add_expense(plan, op, skipped)
            continue
        expense = plan.expense(required(op.expense_id))
        if expense.protected:
            skipped.append(
                SkippedOperation(kind=op.kind, label=expense.label, reason=SkipReason.PROTECTED)
            )
        elif op.kind is ChangeKind.UPDATE_EXPENSE:
            plan = update_expense(plan, expense.id, amount_inr=op.amount_inr)
        else:
            plan = remove_expense(plan, expense.id)
    return plan


def _add_expense(
    plan: EventPlan, op: ChangeOperation, skipped: list[SkippedOperation]
) -> EventPlan:
    category_id, label, amount = (
        required(op.category_id),
        required(op.label),
        required(op.amount_inr),
    )
    if find_duplicate_expense(plan, category_id=category_id, label=label, amount_inr=amount):
        skipped.append(SkippedOperation(kind=op.kind, label=label, reason=SkipReason.DUPLICATE))
        return plan
    draft = NewExpense(
        category_id=category_id,
        label=label,
        amount_inr=amount,
        source=ExpenseSource.CHANGE,
    )
    plan, _ = add_expense(plan, draft)
    return plan


def _apply_custom_categories(
    plan: EventPlan, change_set: ChangeSet, pins: dict[CategoryId, Pin]
) -> tuple[EventPlan, dict[CategoryId, Pin]]:
    ops = change_set.of_kind(ChangeKind.ADD_CATEGORY)
    if not ops:
        return plan, pins
    if sum(c.custom for c in plan.categories) + len(ops) > MAX_CUSTOM_CATEGORIES:
        raise InvalidInputError(
            f"A plan can have at most {MAX_CUSTOM_CATEGORIES} custom budget lines.",
            code="too_many_custom_categories",
        )
    names = {c.name.casefold() for c in plan.categories}
    ids = id_sequence("custom", (c.id for c in plan.categories))
    added = []
    for op in ops:
        label, amount = required(op.label), required(op.amount_inr)
        if label.casefold() in names:
            raise InvalidInputError(
                f"There is already a budget line called '{label}'.", code="duplicate_category"
            )
        names.add(label.casefold())
        category = BudgetCategory(
            id=CategoryId(next(ids)), name=label, weight=1, allocated_inr=0, custom=True
        )
        added.append(category)
        pins = pins | {
            category.id: Pin(amount_inr=min(amount, MAX_BUDGET_INR), reason=ChangeReason.ADDED)
        }
    return plan.with_changes(categories=(*plan.categories, *added)), pins


def _apply_line_amounts(
    plan: EventPlan,
    change_set: ChangeSet,
    pins: dict[CategoryId, Pin],
    skipped: list[SkippedOperation],
) -> dict[CategoryId, Pin]:
    """Pin each line the user set to its new amount; the rebalance moves the rest.

    A line that was already protected is skipped and reported: protection means "keep it as
    it is" until the user removes it.
    """
    for op in change_set.of_kind(ChangeKind.SET_CATEGORY_BUDGET):
        category = plan.category(required(op.category_id))
        if category.protected:
            skipped.append(
                SkippedOperation(kind=op.kind, label=category.name, reason=SkipReason.PROTECTED)
            )
            continue
        pins = pins | {
            category.id: Pin(amount_inr=required(op.amount_inr), reason=ChangeReason.SET)
        }
    return pins


def _add_tasks(
    plan: EventPlan,
    change_set: ChangeSet,
    follow_ups: Sequence[TaskSpec],
    today: date,
    skipped: list[SkippedOperation],
) -> EventPlan:
    titles = {t.title.casefold() for t in plan.tasks}
    specs: list[TaskSpec] = []
    for op in change_set.of_kind(ChangeKind.ADD_TASK):
        label, days = required(op.label), required(op.days)
        if op.category_id is not None:
            plan.category(op.category_id)
        if normalize_text(label).casefold() in titles:
            skipped.append(SkippedOperation(kind=op.kind, label=label, reason=SkipReason.DUPLICATE))
            continue
        titles.add(normalize_text(label).casefold())
        specs.append(
            TaskSpec(key="user-task", title=label, category_id=op.category_id, offset_days=days)
        )

    existing = [t.id for t in plan.tasks]
    user_tasks = build_tasks(
        specs, plan.event_date, today, existing_ids=existing, source=TaskSource.USER
    )
    fresh_follow_ups = [f for f in follow_ups if f.title.casefold() not in titles]
    change_tasks = build_tasks(
        fresh_follow_ups,
        plan.event_date,
        today,
        existing_ids=[*existing, *(t.id for t in user_tasks)],
        source=TaskSource.CHANGE,
    )
    if not user_tasks and not change_tasks:
        return plan
    if len(plan.tasks) + len(user_tasks) + len(change_tasks) > MAX_TASKS:
        raise InvalidInputError(
            f"A plan can have at most {MAX_TASKS} tasks.", code="too_many_tasks"
        )
    return plan.with_changes(tasks=(*plan.tasks, *user_tasks, *change_tasks))


def _booked(vendors: Iterable[VendorItem]) -> list[VendorItem]:
    return [v for v in vendors if v.status is VendorStatus.BOOKED]


def _follow_up(title: str, vendor: VendorItem, event_date: date, today: date) -> TaskSpec:
    """A task due today about a booked vendor."""
    return TaskSpec(
        key="follow-up",
        title=title[:80],
        category_id=vendor.category_id,
        offset_days=(event_date - today).days,
    )


def _build_proposal(
    before: EventPlan,
    after: EventPlan,
    budget_changes: tuple[CategoryChange, ...],
    skipped: list[SkippedOperation],
) -> Proposal:
    old_tasks = {t.id: t for t in before.tasks}
    old_vendors = {v.id: v for v in before.vendors}
    return Proposal(
        base_version=before.version,
        after=after,
        shortfall_inr=0,
        field_changes=_field_changes(before, after),
        budget_changes=budget_changes,
        expense_changes=_expense_changes(before, after),
        task_changes=tuple(
            ScheduleChange(
                item_id=t.id,
                title=t.title,
                before=old_tasks[t.id].due_date,
                after=t.due_date,
                flagged=t.compressed,
            )
            for t in after.tasks
            if t.id in old_tasks and old_tasks[t.id].due_date != t.due_date
        ),
        vendor_changes=tuple(
            ScheduleChange(
                item_id=v.id,
                title=v.name,
                before=old_vendors[v.id].book_by,
                after=v.book_by,
                flagged=v.urgent,
            )
            for v in after.vendors
            if old_vendors[v.id].book_by != v.book_by
        ),
        new_tasks=tuple(t for t in after.tasks if t.id not in old_tasks),
        skipped=tuple(skipped),
        protected_kept=tuple(
            [c.name for c in after.categories if c.protected]
            + [e.label for e in after.expenses if e.protected]
        ),
        summary_before=summarize(before),
        summary_after=summarize(after),
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

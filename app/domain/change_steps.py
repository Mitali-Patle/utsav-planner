"""The steps `changes.propose` applies, in its fixed order, to a working copy of the plan.

Each step takes the plan and the change set and returns a new plan; none modifies its input.
Steps record what they leave out in `skipped`, and the date and guest steps collect follow-up
tasks for vendors who are already booked.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from app.domain.catalog import CONTINGENCY_ID, TaskSpec
from app.domain.change_set import ChangeKind, ChangeOperation, ChangeSet, required
from app.domain.errors import InvalidInputError
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
from app.domain.proposal import SkippedOperation, SkipReason
from app.domain.rebalance import ChangeReason, Pin
from app.domain.text import normalize_text
from app.domain.timeline import build_tasks, reschedule_tasks
from app.domain.vendors import reschedule_vendors


def apply_protection(
    plan: EventPlan, change_set: ChangeSet, *, defer: frozenset[CategoryId] = frozenset()
) -> EventPlan:
    """Protect or unprotect lines and expenses.

    Lines in `defer` are protected after the rebalance instead, so they keep their new amount.
    """
    for op in change_set.of_kind(ChangeKind.PROTECT, ChangeKind.UNPROTECT):
        protected = op.kind is ChangeKind.PROTECT
        if protected and op.category_id in defer:
            continue
        if op.expense_id is not None:
            plan = set_expense_protection(plan, op.expense_id, protected=protected)
        else:
            plan = set_category_protection(plan, required(op.category_id), protected=protected)
    return plan


def apply_date(
    plan: EventPlan, change_set: ChangeSet, today: date, follow_ups: list[TaskSpec]
) -> EventPlan:
    """Move the event date; deadlines and book-by dates move with it.

    Booked vendors get a follow-up task to confirm the new date.

    Raises:
        InvalidInputError: If the new date is outside the planning window.
    """
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


def apply_guests(
    plan: EventPlan, change_set: ChangeSet, today: date, follow_ups: list[TaskSpec]
) -> tuple[EventPlan, dict[CategoryId, Pin]]:
    """Set the guest count and pin guest-scaled lines at their new amounts, rounded up.

    When only the guest count falls, the other lines are held so the money goes to
    contingency. Booked vendors on scaled lines get a follow-up task.

    Returns:
        The plan and the pins for the rebalance.
    """
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


def target_total(plan: EventPlan, change_set: ChangeSet) -> int:
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


def apply_expenses(
    plan: EventPlan, change_set: ChangeSet, skipped: list[SkippedOperation]
) -> EventPlan:
    """Add, update and remove expenses; duplicates and protected expenses are skipped."""
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


def apply_custom_categories(
    plan: EventPlan, change_set: ChangeSet, pins: dict[CategoryId, Pin]
) -> tuple[EventPlan, dict[CategoryId, Pin]]:
    """Add the user's own budget lines, each pinned at the amount asked for.

    Raises:
        InvalidInputError: If a name is taken or there would be too many custom lines.
    """
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


def apply_line_amounts(
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


def add_tasks(
    plan: EventPlan,
    change_set: ChangeSet,
    follow_ups: Sequence[TaskSpec],
    today: date,
    skipped: list[SkippedOperation],
) -> EventPlan:
    """Add the user's new tasks and the follow-ups, leaving out titles the plan already has.

    Raises:
        InvalidInputError: If the plan would have more than the maximum number of tasks.
    """
    titles = {t.title.casefold() for t in plan.tasks}
    specs = _user_task_specs(plan, change_set, titles, skipped)
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


def _user_task_specs(
    plan: EventPlan, change_set: ChangeSet, titles: set[str], skipped: list[SkippedOperation]
) -> list[TaskSpec]:
    """Specs for the tasks the user asked to add, skipping titles the plan already has.

    Adds each accepted title to `titles`, so later follow-ups do not repeat it.
    """
    specs: list[TaskSpec] = []
    for op in change_set.of_kind(ChangeKind.ADD_TASK):
        label, days = required(op.label), required(op.days)
        if op.category_id is not None:
            plan.category(op.category_id)  # raises NotFoundError for an unknown line
        title = normalize_text(label).casefold()
        if title in titles:
            skipped.append(SkippedOperation(kind=op.kind, label=label, reason=SkipReason.DUPLICATE))
            continue
        titles.add(title)
        specs.append(
            TaskSpec(key="user-task", title=label, category_id=op.category_id, offset_days=days)
        )
    return specs


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

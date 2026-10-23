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

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import Field, model_validator

from app.domain.budget import (
    BudgetSummary,
    CategoryChange,
    ChangeReason,
    NewExpense,
    Pin,
    SavingAction,
    SavingLevel,
    add_expense,
    estimate_saving,
    find_duplicate_expense,
    rebalance,
    remove_expense,
    set_category_protection,
    set_expense_protection,
    summarize,
    update_expense,
)
from app.domain.catalog import CONTINGENCY_ID, TaskSpec
from app.domain.errors import InfeasibleChangeError, InvalidInputError, StaleProposalError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_CUSTOM_CATEGORIES,
    MAX_DAYS_AFTER,
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    MAX_TASKS,
    BudgetCategory,
    CategoryId,
    CategoryRef,
    DomainModel,
    EventPlan,
    ExpenseId,
    ExpenseRef,
    ExpenseSource,
    ShortText,
    Task,
    TaskSource,
    VendorItem,
    VendorStatus,
    id_sequence,
    validate_event_date,
)
from app.domain.text import normalize_text
from app.domain.timeline import build_tasks, reschedule_tasks
from app.domain.vendors import reschedule_vendors

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

        Returns:
            The validated operation.

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
        if self.amount_inr == 0 and self.kind is not ChangeKind.SET_CATEGORY_BUDGET:
            raise ValueError(f"{self.kind} needs an amount of at least ₹1")
        if self.kind is ChangeKind.SHIFT_DATE_DAYS and self.days == 0:
            raise ValueError("shift_date_days needs a non-zero number of days")
        if self.kind in _ADJUST_KINDS and self.delta == 0:
            raise ValueError(f"{self.kind} needs a non-zero delta")
        if (
            self.kind is ChangeKind.ADD_TASK
            and self.days is not None
            and self.days < -MAX_DAYS_AFTER
        ):
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
    # A line set and protected in the same request is protected after the rebalance, so it
    # keeps its new amount; protecting it first would pin the old one.
    set_lines = {
        _require(op.category_id) for op in change_set.of_kind(ChangeKind.SET_CATEGORY_BUDGET)
    }
    deferred = frozenset(
        op.category_id
        for op in change_set.of_kind(ChangeKind.PROTECT)
        if op.category_id is not None and op.category_id in set_lines
    )
    working = _apply_protection(plan, change_set, defer=deferred)
    follow_ups: list[TaskSpec] = []
    skipped: list[SkippedOperation] = []

    working = _apply_date(working, change_set, today, follow_ups)
    working, pins = _apply_guests(working, change_set, today, follow_ups)
    working = _apply_expenses(working, change_set, skipped)
    working, pins = _apply_custom_categories(working, change_set, pins)
    pins = _apply_line_amounts(working, change_set, pins, skipped)

    budget_changes: tuple[CategoryChange, ...] = ()
    target = _target_total(working, change_set)
    if target != working.total_budget_inr or pins or change_set.cut_priority:
        result = rebalance(
            working, total_inr=target, pins=pins, cut_priority=change_set.cut_priority
        )
        if not result.feasible:
            return Proposal(
                base_version=plan.version,
                after=None,
                shortfall_inr=result.shortfall_inr,
                field_changes=_field_changes(plan, working),
                skipped=tuple(skipped),
                summary_before=summarize(plan),
                summary_after=None,
            )
        working = result.apply(working)
        budget_changes = result.changes
    for category_id in deferred:
        working = set_category_protection(working, category_id, protected=True)

    working = _add_tasks(working, change_set, follow_ups, today, skipped)
    return _build_proposal(plan, working, budget_changes, skipped)


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


def _require[T](value: T | None) -> T:
    """Narrow an optional field that `ChangeOperation` validation guarantees is set."""
    if value is None:  # pragma: no cover - unreachable after model validation
        raise ValueError("operation is missing a required field")
    return value


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
            plan = set_category_protection(plan, _require(op.category_id), protected=protected)
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
            return _require(op.amount_inr)
        return plan.total_budget_inr + _require(op.delta)
    return plan.total_budget_inr


def _target_guest_count(plan: EventPlan, change_set: ChangeSet) -> int:
    """New guest count, computed by code for relative requests such as "10 more guests".

    Raises:
        InvalidInputError: If the result is outside 1 to the maximum.
    """
    for op in change_set.of_kind(ChangeKind.SET_GUEST_COUNT, ChangeKind.ADJUST_GUEST_COUNT):
        count = (
            _require(op.guest_count)
            if op.kind is ChangeKind.SET_GUEST_COUNT
            else plan.guest_count + _require(op.delta)
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
        expense = plan.expense(_require(op.expense_id))
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
        _require(op.category_id),
        _require(op.label),
        _require(op.amount_inr),
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
        label, amount = _require(op.label), _require(op.amount_inr)
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
        category = plan.category(_require(op.category_id))
        if category.protected:
            skipped.append(
                SkippedOperation(kind=op.kind, label=category.name, reason=SkipReason.PROTECTED)
            )
            continue
        pins = pins | {
            category.id: Pin(amount_inr=_require(op.amount_inr), reason=ChangeReason.SET)
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
        label, days = _require(op.label), _require(op.days)
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

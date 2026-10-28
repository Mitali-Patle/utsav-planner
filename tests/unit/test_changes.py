"""Change engine: SPEC §6 behaviours and must-pass scenarios 3-7, 10 and 13."""

from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.budget import ChangeReason, SavingAction, SavingLevel, summarize
from app.domain.change_set import ChangeKind, ChangeOperation, ChangeSet
from app.domain.changes import accept, propose
from app.domain.errors import (
    InfeasibleChangeError,
    InvalidInputError,
    NotFoundError,
    StaleProposalError,
)
from app.domain.models import CategoryId, EventPlan, EventType, TaskSource, VendorStatus
from app.domain.planning import EventDetails, draft_plan
from app.domain.proposal import ExpenseAction, Proposal, SkipReason
from app.domain.savings import saving_change
from app.domain.vendors import set_vendor_status
from tests.factories import make_plan

TODAY = date(2026, 10, 22)


def op(kind: ChangeKind, **fields: Any) -> ChangeOperation:
    return ChangeOperation.model_validate({"kind": kind, **fields})


def changes(*operations: ChangeOperation, cut_priority: tuple[str, ...] = ()) -> ChangeSet:
    return ChangeSet.model_validate({"operations": operations, "cut_priority": cut_priority})


def after_of(proposal: Proposal) -> EventPlan:
    assert proposal.after is not None
    return proposal.after


def allocation(plan: EventPlan, category_id: str) -> int:
    return next(c.allocated_inr for c in plan.categories if c.id == category_id)


def birthday(days_ahead: int = 40, guests: int = 30) -> EventPlan:
    details = EventDetails(
        event_type=EventType.BIRTHDAY,
        title="Aarav's 8th birthday",
        city="Pune",
        event_date=TODAY + timedelta(days=days_ahead),
        guest_count=guests,
        total_budget_inr=15_000,
    )
    return draft_plan(details, TODAY)


# Validation of operations and change sets


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"kind": "set_total_budget"}, "needs amount_inr"),
        ({"kind": "set_total_budget", "amount_inr": 9_000, "label": "x"}, "does not use label"),
        ({"kind": "shift_date_days", "days": 0}, "non-zero"),
        ({"kind": "add_task", "label": "Tidy up", "days": -31}, "at most 30 days after"),
        ({"kind": "set_guest_count", "guest_count": 0}, "greater than or equal to 1"),
        ({"kind": "fly_to_moon"}, "kind"),
        ({"kind": "protect"}, "exactly one of category_id or expense_id"),
        ({"kind": "protect", "category_id": "cake", "expense_id": "exp-1"}, "exactly one"),
    ],
)
def test_operation_fields_are_checked_per_kind(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ChangeOperation.model_validate(fields)


@pytest.mark.parametrize(
    ("operations", "message"),
    [
        (
            [
                op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=9_000),
                op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=8_000),
            ],
            "budget total is set more than once",
        ),
        (
            [
                op(ChangeKind.SHIFT_DATE_DAYS, days=-3),
                op(ChangeKind.SET_DATE, new_date=date(2026, 12, 1)),
            ],
            "event date is set more than once",
        ),
        (
            [
                op(ChangeKind.PROTECT, category_id="cake"),
                op(ChangeKind.UNPROTECT, category_id="cake"),
            ],
            "protected and unprotected",
        ),
        ([], "at least 1"),
        ([op(ChangeKind.PROTECT, category_id="cake")] * 6, "at most 5"),
    ],
)
def test_conflicting_or_oversized_change_sets_are_rejected(
    operations: list[ChangeOperation], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ChangeSet(operations=tuple(operations))


# SPEC §6 behaviours


def test_budget_drop_keeping_the_cake() -> None:
    """'My budget dropped to ₹12,000 but keep the cake as is' (scenario 3)."""
    plan = make_plan()
    proposal = propose(
        plan,
        changes(
            op(ChangeKind.PROTECT, category_id="cake"),
            op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=12_000),
        ),
        TODAY,
    )
    after = after_of(proposal)
    assert after.total_budget_inr == 12_000
    assert allocation(after, "cake") == 2_000
    reasons: dict[str, ChangeReason] = {c.category_id: c.reason for c in proposal.budget_changes}
    assert reasons["cake"] is ChangeReason.PROTECTED
    assert reasons["decor"] is ChangeReason.PROPORTIONAL
    assert "Cake" in proposal.protected_kept
    assert [(f.field, f.before, f.after) for f in proposal.field_changes] == [
        ("total_budget_inr", 15_000, 12_000)
    ]
    assert proposal.summary_after is not None
    assert proposal.summary_after.total_inr == 12_000


def test_adding_an_expense_over_budget_shows_the_shortfall() -> None:
    """'Add a photographer for ₹2,000' when the budget is already used (scenario 2)."""
    plan = make_plan(expenses=[("catering", "Caterer", 14_000, False)])
    proposal = propose(
        plan,
        changes(
            op(ChangeKind.ADD_EXPENSE, category_id="decor", label="Photographer", amount_inr=2_000)
        ),
        TODAY,
    )
    assert proposal.feasible, "expenses are recorded; overspending is shown, not blocked"
    assert [(c.action, c.after_inr) for c in proposal.expense_changes] == [
        (ExpenseAction.ADDED, 2_000)
    ]
    assert proposal.summary_after is not None
    assert proposal.summary_after.shortfall_inr == 9_000, "catering is ₹9,000 past its plan"
    assert proposal.budget_changes == (), "no money is moved between categories automatically"


def test_guest_increase_scales_per_guest_categories() -> None:
    """'Guest count goes from 30 to 40' (scenario 4)."""
    plan = make_plan(scales_with_guests=["catering"])
    proposal = propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=40)), TODAY)
    after = after_of(proposal)
    catering = next(c for c in proposal.budget_changes if c.category_id == "catering")
    assert (catering.before_inr, catering.after_inr, catering.reason) == (
        5_000,
        6_667,
        ChangeReason.GUEST_SCALED,
    )
    assert after.total_budget_inr == 15_000
    assert after.guest_count == 40


def test_guest_increase_that_cannot_fit_reports_the_shortfall() -> None:
    plan = make_plan(scales_with_guests=["catering"], protected=["decor", "cake", "other"])
    proposal = propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=60)), TODAY)
    assert not proposal.feasible
    assert proposal.shortfall_inr == 5_000
    assert proposal.summary_after is None
    with pytest.raises(InfeasibleChangeError):
        accept(proposal, plan)


def test_guest_decrease_moves_freed_money_to_contingency() -> None:
    plan = make_plan(
        allocations={"catering": 6_000, "decor": 3_000, "contingency": 1_000},
        scales_with_guests=["catering"],
    )
    after = after_of(propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=20)), TODAY))
    assert (
        allocation(after, "catering"),
        allocation(after, "decor"),
        allocation(after, "contingency"),
    ) == (
        4_000,
        3_000,
        3_000,
    )


def test_guest_decrease_without_contingency_is_shared_proportionally() -> None:
    plan = make_plan(
        allocations={"catering": 6_000, "decor": 3_000, "music": 3_000},
        scales_with_guests=["catering"],
    )
    after = after_of(propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=20)), TODAY))
    assert (
        allocation(after, "catering"),
        allocation(after, "decor"),
        allocation(after, "music"),
    ) == (
        4_000,
        4_000,
        4_000,
    )


def test_moving_the_event_earlier_recalculates_deadlines() -> None:
    """'Move the event 3 days earlier' (scenario 5)."""
    plan = birthday(days_ahead=40)
    proposal = propose(plan, changes(op(ChangeKind.SHIFT_DATE_DAYS, days=-3)), TODAY)
    after = after_of(proposal)
    assert after.event_date == plan.event_date - timedelta(days=3)
    assert proposal.task_changes
    assert all(
        change.after == change.before - timedelta(days=3)
        for change in proposal.task_changes
        if not change.flagged
    )
    assert proposal.vendor_changes


def test_moving_the_event_into_the_past_is_rejected() -> None:
    plan = birthday(days_ahead=2)
    with pytest.raises(InvalidInputError) as error:
        propose(plan, changes(op(ChangeKind.SHIFT_DATE_DAYS, days=-5)), TODAY)
    assert error.value.code == "event_date_in_past"


def test_setting_the_same_date_changes_nothing() -> None:
    plan = birthday()
    proposal = propose(plan, changes(op(ChangeKind.SET_DATE, new_date=plan.event_date)), TODAY)
    assert not proposal.has_changes


def test_cut_decoration_first_keeping_the_cake() -> None:
    plan = make_plan()
    proposal = propose(
        plan,
        changes(
            op(ChangeKind.PROTECT, category_id="cake"),
            op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=13_000),
            cut_priority=("decor",),
        ),
        TODAY,
    )
    after = after_of(proposal)
    assert (
        allocation(after, "decor"),
        allocation(after, "cake"),
        allocation(after, "catering"),
    ) == (1_000, 2_000, 5_000)


# Follow-up tasks for booked vendors (addition 7)


def test_date_change_creates_follow_ups_for_booked_vendors() -> None:
    plan = birthday(days_ahead=40)
    venue = next(v for v in plan.vendors if v.vendor_type == "venue")
    plan = set_vendor_status(plan, venue.id, VendorStatus.BOOKED)
    proposal = propose(plan, changes(op(ChangeKind.SHIFT_DATE_DAYS, days=7)), TODAY)
    follow_ups = [t for t in proposal.new_tasks if t.source is TaskSource.CHANGE]
    assert [t.title for t in follow_ups] == [
        f"Confirm the new date {after_of(proposal).event_date:%d %b} with Venue"
    ]
    assert follow_ups[0].due_date == TODAY


def test_guest_change_tells_booked_per_guest_vendors() -> None:
    plan = birthday()
    caterer = next(v for v in plan.vendors if v.vendor_type == "caterer")
    plan = set_vendor_status(plan, caterer.id, VendorStatus.BOOKED)
    proposal = propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=45)), TODAY)
    titles = [t.title for t in proposal.new_tasks]
    assert titles == ["Update Caterer or restaurant: new guest count 45"]


def test_guest_decrease_only_notifies_vendors_of_scaled_categories() -> None:
    """Regression: categories held still so savings reach contingency are not "scaled"."""
    plan = birthday(guests=40)
    for vendor_type in ("caterer", "decorator"):
        vendor = next(v for v in plan.vendors if v.vendor_type == vendor_type)
        plan = set_vendor_status(plan, vendor.id, VendorStatus.BOOKED)
    proposal = propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=25)), TODAY)
    assert [t.title for t in proposal.new_tasks] == [
        "Update Caterer or restaurant: new guest count 25"
    ]


def test_task_without_a_category() -> None:
    proposal = propose(
        birthday(), changes(op(ChangeKind.ADD_TASK, label="Call grandparents", days=5)), TODAY
    )
    assert proposal.new_tasks[0].category_id is None


def test_unbooked_vendors_get_no_follow_ups() -> None:
    proposal = propose(birthday(), changes(op(ChangeKind.SHIFT_DATE_DAYS, days=7)), TODAY)
    assert proposal.new_tasks == ()


# Expenses, protection, tasks and custom categories


def test_repeated_expense_is_skipped_not_duplicated() -> None:
    """Scenario 10: the same request twice never adds the expense twice."""
    plan = make_plan(expenses=[("decor", "Photographer", 2_000, False)])
    add = op(ChangeKind.ADD_EXPENSE, category_id="decor", label="photographer", amount_inr=2_000)
    proposal = propose(plan, changes(add), TODAY)
    assert [(s.reason, s.label) for s in proposal.skipped] == [
        (SkipReason.DUPLICATE, "photographer")
    ]
    assert not proposal.has_changes
    twice = propose(make_plan(), changes(add, add), TODAY)
    assert len(after_of(twice).expenses) == 1


def test_update_and_remove_expenses() -> None:
    plan = make_plan(expenses=[("decor", "Balloons", 400, False), ("decor", "Lights", 900, False)])
    proposal = propose(
        plan,
        changes(
            op(ChangeKind.UPDATE_EXPENSE, expense_id="exp-1", amount_inr=650),
            op(ChangeKind.REMOVE_EXPENSE, expense_id="exp-2"),
        ),
        TODAY,
    )
    assert sorted((c.action, c.before_inr, c.after_inr) for c in proposal.expense_changes) == [
        (ExpenseAction.REMOVED, 900, None),
        (ExpenseAction.UPDATED, 400, 650),
    ]


def test_protected_expenses_are_never_touched() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_000, True)])
    proposal = propose(plan, changes(op(ChangeKind.REMOVE_EXPENSE, expense_id="exp-1")), TODAY)
    assert [(s.reason, s.label) for s in proposal.skipped] == [(SkipReason.PROTECTED, "Cake")]
    assert after_of(proposal).expenses == plan.expenses


def test_protect_and_unprotect_resolve_expenses_and_categories() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_000, False)], protected=["decor"])
    after = after_of(
        propose(
            plan,
            changes(
                op(ChangeKind.PROTECT, expense_id="exp-1"),
                op(ChangeKind.UNPROTECT, category_id="decor"),
            ),
            TODAY,
        )
    )
    assert after.expenses[0].protected
    assert not next(c for c in after.categories if c.id == "decor").protected
    with pytest.raises(NotFoundError):
        propose(plan, changes(op(ChangeKind.PROTECT, category_id="fireworks")), TODAY)


def test_add_task_with_deadline_and_duplicate_check() -> None:
    plan = birthday()
    task = op(ChangeKind.ADD_TASK, label="Book a magician", days=10, category_id="entertainment")
    proposal = propose(plan, changes(task), TODAY)
    new = proposal.new_tasks[0]
    assert (new.title, new.source, new.due_date) == (
        "Book a magician",
        TaskSource.USER,
        plan.event_date - timedelta(days=10),
    )
    again = propose(after_of(proposal), changes(task), TODAY)
    assert [s.reason for s in again.skipped] == [SkipReason.DUPLICATE]
    with pytest.raises(NotFoundError):
        propose(
            plan, changes(op(ChangeKind.ADD_TASK, label="x", days=1, category_id="nope")), TODAY
        )


def test_custom_budget_line_is_added_and_funded() -> None:
    proposal = propose(
        make_plan(),
        changes(op(ChangeKind.ADD_CATEGORY, label="Fireworks", amount_inr=3_000)),
        TODAY,
    )
    after = after_of(proposal)
    fireworks = next(c for c in after.categories if c.name == "Fireworks")
    assert (fireworks.id, fireworks.custom, fireworks.allocated_inr) == ("custom-1", True, 3_000)
    assert after.total_budget_inr == 15_000
    reasons: dict[str, ChangeReason] = {c.category_id: c.reason for c in proposal.budget_changes}
    assert reasons["custom-1"] is ChangeReason.ADDED


def test_custom_budget_lines_are_limited_and_unique() -> None:
    lines = [op(ChangeKind.ADD_CATEGORY, label=f"Extra {i}", amount_inr=500) for i in range(4)]
    with pytest.raises(InvalidInputError) as limit:
        propose(make_plan(), changes(*lines), TODAY)
    assert limit.value.code == "too_many_custom_categories"
    with pytest.raises(InvalidInputError) as duplicate:
        propose(
            make_plan(), changes(op(ChangeKind.ADD_CATEGORY, label="cake", amount_inr=500)), TODAY
        )
    assert duplicate.value.code == "duplicate_category"


# Approve and reject


def test_reject_leaves_the_saved_plan_unchanged() -> None:
    """Scenario 6: proposing never modifies the plan, so rejecting needs no undo."""
    plan = birthday()
    snapshot = plan.model_dump()
    propose(
        plan,
        changes(
            op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=10_000),
            op(ChangeKind.SHIFT_DATE_DAYS, days=-2),
        ),
        TODAY,
    )
    assert plan.model_dump() == snapshot


def test_approve_saves_a_new_version() -> None:
    """Scenario 7."""
    plan = make_plan()
    proposal = propose(plan, changes(op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=12_000)), TODAY)
    saved = accept(proposal, plan)
    assert (saved.version, saved.total_budget_inr) == (2, 12_000)


def test_stale_proposal_cannot_be_approved() -> None:
    """Scenario 13, and approving twice is refused."""
    plan = make_plan()
    proposal = propose(plan, changes(op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=12_000)), TODAY)
    saved = accept(proposal, plan)
    with pytest.raises(StaleProposalError):
        accept(proposal, saved)


# Relative changes: the AI copies the number, code does the arithmetic


def test_ten_more_guests_is_computed_by_code() -> None:
    plan = make_plan(scales_with_guests=["catering"])
    relative = propose(plan, changes(op(ChangeKind.ADJUST_GUEST_COUNT, delta=10)), TODAY)
    absolute = propose(plan, changes(op(ChangeKind.SET_GUEST_COUNT, guest_count=40)), TODAY)
    assert after_of(relative).guest_count == 40
    assert relative.budget_changes == absolute.budget_changes


def test_budget_cut_by_an_amount_is_computed_by_code() -> None:
    plan = make_plan(protected=["cake"])
    after = after_of(
        propose(plan, changes(op(ChangeKind.ADJUST_TOTAL_BUDGET, delta=-3_000)), TODAY)
    )
    assert after.total_budget_inr == 12_000
    assert allocation(after, "cake") == 2_000


def test_relative_changes_are_validated() -> None:
    with pytest.raises(ValidationError, match="non-zero delta"):
        op(ChangeKind.ADJUST_GUEST_COUNT, delta=0)
    with pytest.raises(InvalidInputError) as guests:
        propose(
            make_plan(guest_count=5), changes(op(ChangeKind.ADJUST_GUEST_COUNT, delta=-5)), TODAY
        )
    assert guests.value.code == "guest_count_out_of_range"
    with pytest.raises(InvalidInputError) as budget:
        propose(make_plan(), changes(op(ChangeKind.ADJUST_TOTAL_BUDGET, delta=-14_500)), TODAY)
    assert budget.value.code == "budget_out_of_range"
    with pytest.raises(ValidationError, match="guest count is set more than once"):
        changes(
            op(ChangeKind.SET_GUEST_COUNT, guest_count=40),
            op(ChangeKind.ADJUST_GUEST_COUNT, delta=5),
        )


# Setting one budget line (review, 23 Oct: moving money between lines had no operation)


def _line(plan: EventPlan, category_id: str) -> int:
    return plan.category(CategoryId(category_id)).allocated_inr


def test_setting_a_line_moves_money_and_keeps_the_total_exact() -> None:
    plan = make_plan()
    proposal = propose(
        plan,
        changes(op(ChangeKind.SET_CATEGORY_BUDGET, category_id="decor", amount_inr=6_000)),
        TODAY,
    )
    assert proposal.after is not None
    assert _line(proposal.after, "decor") == 6_000
    assert sum(c.allocated_inr for c in proposal.after.categories) == plan.total_budget_inr
    reasons = {c.category_id: c.reason for c in proposal.budget_changes}
    assert reasons[CategoryId("decor")] is ChangeReason.SET
    assert _line(proposal.after, "catering") < _line(plan, "catering"), "others give way"


def test_set_and_keep_in_one_request_protects_the_new_amount() -> None:
    proposal = propose(
        make_plan(),
        changes(
            op(ChangeKind.SET_CATEGORY_BUDGET, category_id="decor", amount_inr=6_000),
            op(ChangeKind.PROTECT, category_id="decor"),
        ),
        TODAY,
    )
    assert proposal.after is not None
    decor = proposal.after.category(CategoryId("decor"))
    assert (decor.allocated_inr, decor.protected) == (6_000, True)


def test_setting_an_already_protected_line_is_skipped_and_reported() -> None:
    plan = make_plan(protected=["decor"])
    proposal = propose(
        plan,
        changes(op(ChangeKind.SET_CATEGORY_BUDGET, category_id="decor", amount_inr=6_000)),
        TODAY,
    )
    assert proposal.after is not None
    assert _line(proposal.after, "decor") == _line(plan, "decor")
    assert [(s.label, s.reason) for s in proposal.skipped] == [("Decor", SkipReason.PROTECTED)]


def test_setting_a_line_beyond_what_fits_shows_the_exact_shortfall() -> None:
    plan = make_plan(protected=["catering", "cake", "other"])
    proposal = propose(
        plan,
        changes(op(ChangeKind.SET_CATEGORY_BUDGET, category_id="decor", amount_inr=5_000)),
        TODAY,
    )
    assert proposal.after is None
    assert proposal.shortfall_inr == 2_000, "decor can only have the ₹3,000 nobody protected"


def test_setting_an_unknown_line_is_refused() -> None:
    with pytest.raises(NotFoundError):
        propose(
            make_plan(),
            changes(op(ChangeKind.SET_CATEGORY_BUDGET, category_id="fireworks", amount_inr=500)),
            TODAY,
        )


def test_a_plan_has_a_bounded_number_of_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.domain.changes.MAX_TASKS", 0)
    with pytest.raises(InvalidInputError) as error:
        propose(make_plan(), changes(op(ChangeKind.ADD_TASK, label="Buy candles", days=3)), TODAY)
    assert error.value.code == "too_many_tasks"


# Freed money and alternatives (F13)

CATERING_OVER = make_plan(expenses=(("catering", "Caterer advance", 7_000, False),))
"""Catering is planned at ₹5,000 but ₹7,000 is spent: ₹2,000 short."""


def test_extra_money_covers_overspent_lines_first() -> None:
    assert summarize(CATERING_OVER).shortfall_inr == 2_000
    after = after_of(
        propose(CATERING_OVER, changes(op(ChangeKind.ADJUST_TOTAL_BUDGET, delta=1_000)), TODAY)
    )
    assert [allocation(after, c) for c in ("catering", "decor", "cake", "other")] == [
        6_000,
        3_000,
        2_000,
        5_000,
    ]
    assert summarize(after).shortfall_inr == 1_000


def test_a_saving_moves_money_to_the_overspent_line() -> None:
    change_set = saving_change(
        CATERING_OVER, CategoryId("decor"), SavingAction.REDUCE, SavingLevel.LARGE
    )
    assert change_set is not None
    after = after_of(propose(CATERING_OVER, change_set, TODAY))
    assert (allocation(after, "decor"), allocation(after, "catering")) == (1_500, 6_500)
    assert summarize(after).shortfall_inr == 500


def test_a_saving_larger_than_the_gap_shares_the_rest() -> None:
    """₹3,000 freed: ₹2,000 covers catering, ₹1,000 is shared in proportion to the plan."""
    change_set = saving_change(
        CATERING_OVER, CategoryId("decor"), SavingAction.REMOVE, SavingLevel.SMALL
    )
    assert change_set is not None
    after = after_of(propose(CATERING_OVER, change_set, TODAY))
    assert [allocation(after, c) for c in ("catering", "decor", "cake", "other")] == [
        7_417,
        0,
        2_167,
        5_416,
    ]
    assert summarize(after).shortfall_inr == 0
    assert after.total_budget_inr == CATERING_OVER.total_budget_inr


@pytest.mark.parametrize(
    ("spent_on_decor", "expected"),
    [(0, 1_500), (2_500, 2_500), (3_000, None)],
)
def test_a_saving_never_cuts_below_what_is_spent(spent_on_decor: int, expected: int | None) -> None:
    expenses = [("catering", "Caterer advance", 7_000, False)]
    if spent_on_decor:
        expenses.append(("decor", "Flowers", spent_on_decor, False))
    plan = make_plan(expenses=expenses)
    change_set = saving_change(plan, CategoryId("decor"), SavingAction.REDUCE, SavingLevel.LARGE)
    amount = change_set.operations[0].amount_inr if change_set else None
    assert amount == expected


def test_protected_lines_offer_no_saving() -> None:
    plan = make_plan(protected=("decor",))
    assert saving_change(plan, CategoryId("decor"), SavingAction.REMOVE, SavingLevel.SMALL) is None
    with pytest.raises(NotFoundError):
        saving_change(plan, CategoryId("nope"), SavingAction.REMOVE, SavingLevel.SMALL)


def test_only_a_budget_line_may_be_set_to_zero() -> None:
    assert op(ChangeKind.SET_CATEGORY_BUDGET, category_id="decor", amount_inr=0).amount_inr == 0
    with pytest.raises(ValidationError, match="at least ₹1"):
        op(ChangeKind.SET_TOTAL_BUDGET, amount_inr=0)


def test_removing_an_essential_line_cuts_it_deeply_instead() -> None:
    """A live option titled "Trim catering" removed the food line entirely (28 Oct)."""
    plan = make_plan()
    removed = saving_change(plan, CategoryId("catering"), SavingAction.REMOVE, SavingLevel.SMALL)
    deep_cut = saving_change(plan, CategoryId("catering"), SavingAction.REDUCE, SavingLevel.LARGE)
    assert removed == deep_cut
    assert removed is not None and (removed.operations[0].amount_inr or 0) > 0
    decor = saving_change(plan, CategoryId("decor"), SavingAction.REMOVE, SavingLevel.SMALL)
    assert decor is not None and decor.operations[0].amount_inr == 0, "a non-essential line can go"

"""Property tests: the change engine's guarantees hold for random plans and requests."""

from datetime import date, timedelta

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from app.domain.budget import NewExpense, add_expense
from app.domain.catalog import spec_for
from app.domain.changes import ChangeKind, ChangeOperation, ChangeSet, accept, propose
from app.domain.errors import InfeasibleChangeError, StaleProposalError
from app.domain.models import (
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    EventPlan,
    EventType,
    VendorStatus,
)
from app.domain.planning import EventDetails, draft_plan
from app.domain.vendors import set_vendor_status

TODAY = date(2026, 10, 22)


@st.composite
def plans(draw: st.DrawFn) -> EventPlan:
    event_type = draw(st.sampled_from(list(EventType)))
    sub_ids = [s.id for s in spec_for(event_type).sub_events]
    plan = draft_plan(
        EventDetails(
            event_type=event_type,
            title="Generated event",
            city="Chennai",
            event_date=TODAY + timedelta(days=draw(st.integers(0, 400))),
            guest_count=draw(st.integers(1, 800)),
            total_budget_inr=draw(st.integers(MIN_BUDGET_INR, 5_000_000)),
            sub_events=tuple(draw(st.lists(st.sampled_from(sub_ids), max_size=3))),
        ),
        TODAY,
    )
    for category in draw(st.lists(st.sampled_from(plan.categories), max_size=3)):
        amount = draw(st.integers(1, max(1, category.allocated_inr)))
        plan, _ = add_expense(
            plan,
            NewExpense(
                category_id=category.id,
                label="Advance",
                amount_inr=amount,
                protected=draw(st.booleans()),
            ),
        )
    for vendor in draw(
        st.lists(st.sampled_from(plan.vendors), max_size=2, unique_by=lambda v: v.id)
    ):
        plan = set_vendor_status(plan, vendor.id, VendorStatus.BOOKED)
    return plan


@st.composite
def requests(draw: st.DrawFn, plan: EventPlan) -> ChangeSet:
    category_ids = [c.id for c in plan.categories]
    candidates: list[ChangeOperation] = []
    if draw(st.booleans()):
        candidates.append(
            ChangeOperation(
                kind=ChangeKind.PROTECT, category_id=draw(st.sampled_from(category_ids))
            )
        )
    if draw(st.booleans()):
        total = draw(st.integers(MIN_BUDGET_INR, 6_000_000))
        candidates.append(ChangeOperation(kind=ChangeKind.SET_TOTAL_BUDGET, amount_inr=total))
    if draw(st.booleans()):
        candidates.append(
            ChangeOperation(
                kind=ChangeKind.SET_GUEST_COUNT, guest_count=draw(st.integers(1, MAX_GUESTS))
            )
        )
    days_left = (plan.event_date - TODAY).days
    shift = draw(st.integers(-days_left, MAX_DAYS_AHEAD - days_left))
    if shift and draw(st.booleans()):
        candidates.append(ChangeOperation(kind=ChangeKind.SHIFT_DATE_DAYS, days=shift))
    if draw(st.booleans()):
        candidates.append(
            ChangeOperation(
                kind=ChangeKind.ADD_EXPENSE,
                category_id=draw(st.sampled_from(category_ids)),
                label="Extra",
                amount_inr=draw(st.integers(1, plan.total_budget_inr * 2)),
            )
        )
    if not candidates:
        candidates.append(
            ChangeOperation(kind=ChangeKind.ADD_TASK, label="Check the weather", days=1)
        )
    priority = tuple(draw(st.lists(st.sampled_from(category_ids), max_size=2)))
    return ChangeSet(operations=tuple(candidates), cut_priority=priority)


@given(st.data())
def test_change_engine_guarantees(data: st.DataObject) -> None:
    plan = data.draw(plans())
    request = data.draw(requests(plan))
    snapshot = plan.model_dump()

    try:
        proposal = propose(plan, request, TODAY)
    except InfeasibleChangeError as error:
        event("nothing can absorb the increase")
        assert error.code == "no_adjustable_category"
        return

    assert plan.model_dump() == snapshot, "proposing must never modify the saved plan"
    if not proposal.feasible:
        event("shortfall")
        assert proposal.shortfall_inr > 0
        assert proposal.summary_after is None
        return

    event("feasible")
    after = proposal.after
    assert after is not None
    newly_protected = {
        op.protection_target for op in request.operations if op.kind is ChangeKind.PROTECT
    }
    for category in plan.categories:
        if category.protected or category.id in newly_protected:
            assert after.category(category.id).allocated_inr == category.allocated_inr
    for expense in plan.expenses:
        if expense.protected:
            assert after.expense(expense.id) == expense
    total_ops = [
        op.amount_inr for op in request.operations if op.kind is ChangeKind.SET_TOTAL_BUDGET
    ]
    assert after.total_budget_inr == (total_ops[0] if total_ops else plan.total_budget_inr)
    assert all(t.due_date >= TODAY for t in after.tasks if not t.done)

    saved = accept(proposal, plan)
    assert saved.version == plan.version + 1
    with pytest.raises(StaleProposalError):
        accept(proposal, saved)

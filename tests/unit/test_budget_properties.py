"""Property tests: rebalance rules hold for thousands of generated plans."""

from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.errors import InfeasibleChangeError
from app.domain.models import MIN_BUDGET_INR, CategoryId, EventPlan
from app.domain.rebalance import ChangeReason, Pin, rebalance
from tests.factories import make_plan

TOTALS = st.integers(min_value=MIN_BUDGET_INR, max_value=20_000_000)


@st.composite
def plans(draw: st.DrawFn) -> EventPlan:
    count = draw(st.integers(min_value=1, max_value=8))
    names = [f"cat{i}" for i in range(count)]
    allocations = draw(st.lists(st.integers(0, 3_000_000), min_size=count, max_size=count))
    allocations[0] += MIN_BUDGET_INR
    protected = [n for n in names if draw(st.booleans()) and draw(st.booleans())]
    expenses = []
    for name, amount in zip(names, allocations, strict=True):
        if draw(st.booleans()):
            spend = draw(st.integers(1, max(1, amount + 50_000)))
            expenses.append((name, f"item {name}", spend, draw(st.booleans())))
    return make_plan(
        allocations=dict(zip(names, allocations, strict=True)),
        protected=protected,
        expenses=expenses,
    )


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[EventPlan, int, dict[CategoryId, Pin], list[CategoryId]]:
    plan = draw(plans())
    ids = [c.id for c in plan.categories]
    pinned = draw(st.lists(st.sampled_from(ids), unique=True, max_size=2))
    pins = {
        cid: Pin(amount_inr=draw(st.integers(0, 4_000_000)), reason=ChangeReason.GUEST_SCALED)
        for cid in pinned
    }
    priority = draw(st.lists(st.sampled_from(ids), max_size=3))
    return plan, draw(TOTALS), pins, priority


@given(scenarios())
def test_rebalance_invariants(
    scenario: tuple[EventPlan, int, dict[CategoryId, Pin], list[CategoryId]],
) -> None:
    plan, total, pins, priority = scenario
    snapshot = plan.model_dump()
    floors = {c.id: plan.protected_floor_inr(c.id) for c in plan.categories}
    fixed = {
        c.id: c.allocated_inr if c.protected else max(pins[c.id].amount_inr, floors[c.id])
        for c in plan.categories
        if c.protected or c.id in pins
    }
    required = sum(fixed.values()) + sum(floors[c.id] for c in plan.categories if c.id not in fixed)

    try:
        result = rebalance(plan, total_inr=total, pins=pins, cut_priority=priority)
    except InfeasibleChangeError:
        assert len(fixed) == len(plan.categories)
        assert total > sum(fixed.values())
        return

    assert plan.model_dump() == snapshot, "rebalance must not modify its input"
    if not result.feasible:
        assert result.shortfall_inr == required - total > 0
        return

    after = {change.category_id: change.after_inr for change in result.changes}
    assert sum(after.values()) == total
    assert required <= total
    for category in plan.categories:
        assert after[category.id] >= 0
        if category.id in fixed:
            assert after[category.id] == fixed[category.id]
        else:
            assert after[category.id] >= floors[category.id]
    assert result.apply(plan).total_budget_inr == total
    assert plan.model_dump() == snapshot, "apply must not modify its input"


@given(plans(), TOTALS)
def test_cuts_never_raise_unpinned_categories_above_their_base(plan: EventPlan, total: int) -> None:
    assume(total < plan.total_budget_inr)
    result = rebalance(plan, total_inr=total)
    for change in result.changes:
        floor = plan.protected_floor_inr(change.category_id)
        assert change.after_inr <= max(change.before_inr, floor)

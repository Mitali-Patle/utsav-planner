"""Budget engine: allocation, summaries, expenses and rebalancing (SPEC §7 scenarios 1-3, 9, 10)."""

from collections.abc import Iterable

import pytest
from pydantic import ValidationError

from app.domain.budget import (
    CategoryChange,
    ChangeReason,
    Emphasis,
    EmphasisDirection,
    EmphasisStrength,
    NewExpense,
    Pin,
    add_expense,
    allocate,
    apply_emphasis,
    find_duplicate_expense,
    rebalance,
    remove_expense,
    set_category_protection,
    set_expense_protection,
    summarize,
    update_expense,
)
from app.domain.errors import InfeasibleChangeError, InvalidInputError, NotFoundError
from app.domain.models import (
    MAX_BUDGET_INR,
    MIN_BUDGET_INR,
    CategoryId,
    ExpenseId,
    evolve,
)
from tests.factories import make_plan

CATERING, DECOR, CAKE, OTHER = (CategoryId(c) for c in ("catering", "decor", "cake", "other"))


def amounts(changes: Iterable[CategoryChange]) -> dict[str, int]:
    return {c.category_id: c.after_inr for c in changes}


def reasons(changes: Iterable[CategoryChange]) -> dict[str, ChangeReason]:
    return {c.category_id: c.reason for c in changes}


# Allocation and emphasis


def test_allocate_splits_by_weight_exactly() -> None:
    plan = make_plan()
    weighted = tuple(
        evolve(c, weight=w) for c, w in zip(plan.categories, [5, 3, 2, 0], strict=True)
    )
    allocated = allocate(weighted, 10_001)
    assert [c.allocated_inr for c in allocated] == [5_001, 3_000, 2_000, 0]


def test_allocate_with_all_zero_weights_splits_equally() -> None:
    plan = make_plan()
    unweighted = tuple(evolve(c, weight=0) for c in plan.categories)
    assert [c.allocated_inr for c in allocate(unweighted, 1_000)] == [250, 250, 250, 250]


def test_emphasis_is_bounded_and_scales_other_weights_neutrally() -> None:
    plan = make_plan(allocations={"food": 1_000, "music": 1_000})
    boosted = apply_emphasis(
        plan.categories,
        {
            CategoryId("music"): Emphasis(
                direction=EmphasisDirection.INCREASE, strength=EmphasisStrength.MEDIUM
            )
        },
    )
    assert [c.weight for c in boosted] == [100_000, 120_000]
    assert [c.allocated_inr for c in allocate(boosted, 2_200)] == [1_000, 1_200]


@pytest.mark.parametrize(
    ("direction", "strength", "percent"),
    [
        (EmphasisDirection.INCREASE, EmphasisStrength.SMALL, 110),
        (EmphasisDirection.INCREASE, EmphasisStrength.MEDIUM, 120),
        (EmphasisDirection.DECREASE, EmphasisStrength.SMALL, 90),
        (EmphasisDirection.DECREASE, EmphasisStrength.MEDIUM, 80),
    ],
)
def test_emphasis_percentages(
    direction: EmphasisDirection, strength: EmphasisStrength, percent: int
) -> None:
    assert Emphasis(direction=direction, strength=strength).percent == percent


def test_emphasis_on_unknown_category_is_rejected() -> None:
    emphasis = Emphasis(direction=EmphasisDirection.INCREASE, strength=EmphasisStrength.SMALL)
    with pytest.raises(NotFoundError):
        apply_emphasis(make_plan().categories, {CategoryId("fireworks"): emphasis})


# Summaries and expenses


def test_scenario_1_expense_within_budget_updates_totals() -> None:
    plan, expense = add_expense(
        make_plan(), NewExpense(category_id=CATERING, label="Snacks", amount_inr=1_500)
    )
    summary = summarize(plan)
    assert expense.id == "exp-1"
    assert (summary.spent_inr, summary.remaining_inr, summary.shortfall_inr) == (1_500, 13_500, 0)
    assert not summary.is_over_budget
    assert summary.per_guest_inr == 500
    catering = next(c for c in summary.categories if c.category_id == CATERING)
    assert (catering.spent_inr, catering.remaining_inr, catering.overspent_by_inr) == (
        1_500,
        3_500,
        0,
    )


def test_scenario_2_expense_over_budget_shows_shortfall() -> None:
    plan, _ = add_expense(
        make_plan(), NewExpense(category_id=DECOR, label="Flower arch", amount_inr=16_000)
    )
    summary = summarize(plan)
    assert summary.is_over_budget
    assert (summary.remaining_inr, summary.shortfall_inr) == (0, 1_000)
    decor = next(c for c in summary.categories if c.category_id == DECOR)
    assert (decor.remaining_inr, decor.overspent_by_inr) == (0, 13_000)


def test_expense_up_to_twice_the_budget_is_allowed() -> None:
    plan, expense = add_expense(
        make_plan(), NewExpense(category_id=DECOR, label="Big", amount_inr=30_000)
    )
    assert expense.amount_inr == 30_000
    assert summarize(plan).shortfall_inr == 15_000


@pytest.mark.parametrize("amount", [0, -5])
def test_scenario_9_non_positive_amounts_are_rejected(amount: int) -> None:
    with pytest.raises(ValidationError):
        NewExpense(category_id=DECOR, label="Lights", amount_inr=amount)


def test_scenario_9_amount_above_twice_budget_is_rejected() -> None:
    with pytest.raises(InvalidInputError) as error:
        add_expense(make_plan(), NewExpense(category_id=DECOR, label="Lights", amount_inr=30_001))
    assert error.value.code == "expense_amount_out_of_range"


def test_expense_for_unknown_category_is_rejected() -> None:
    with pytest.raises(NotFoundError):
        add_expense(
            make_plan(),
            NewExpense(category_id=CategoryId("fireworks"), label="Rockets", amount_inr=500),
        )


def test_adding_an_expense_leaves_the_original_plan_unchanged() -> None:
    plan = make_plan()
    before = plan.model_dump()
    add_expense(plan, NewExpense(category_id=CAKE, label="Cake", amount_inr=2_000))
    assert plan.model_dump() == before


def test_update_and_remove_unprotected_expense() -> None:
    plan = make_plan(expenses=[("decor", "Balloons", 400, False)])
    updated = update_expense(plan, ExpenseId("exp-1"), amount_inr=650, label="  Balloon   arch ")
    assert (updated.expenses[0].amount_inr, updated.expenses[0].label) == (650, "Balloon arch")
    assert update_expense(plan, ExpenseId("exp-1")).expenses == plan.expenses
    assert remove_expense(updated, ExpenseId("exp-1")).expenses == ()


def test_update_rejects_out_of_range_amount() -> None:
    plan = make_plan(expenses=[("decor", "Balloons", 400, False)])
    with pytest.raises(InvalidInputError):
        update_expense(plan, ExpenseId("exp-1"), amount_inr=30_001)


def test_protected_expense_cannot_be_changed_or_removed() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_000, True)])
    with pytest.raises(InvalidInputError) as update_error:
        update_expense(plan, ExpenseId("exp-1"), amount_inr=1_000)
    with pytest.raises(InvalidInputError) as remove_error:
        remove_expense(plan, ExpenseId("exp-1"))
    assert update_error.value.code == remove_error.value.code == "item_protected"


def test_protection_can_be_toggled() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_000, False)])
    protected = set_expense_protection(plan, ExpenseId("exp-1"), protected=True)
    assert protected.expenses[0].protected
    assert set_category_protection(plan, CAKE, protected=True).category(CAKE).protected
    with pytest.raises(NotFoundError):
        set_category_protection(plan, CategoryId("fireworks"), protected=True)
    with pytest.raises(NotFoundError):
        set_expense_protection(plan, ExpenseId("exp-9"), protected=True)


def test_scenario_10_duplicate_expense_is_detected() -> None:
    plan = make_plan(expenses=[("decor", "Photographer", 2_000, False)])
    duplicate = find_duplicate_expense(
        plan, category_id=DECOR, label="  photographer ", amount_inr=2_000
    )
    assert duplicate is not None
    assert duplicate.id == "exp-1"
    assert (
        find_duplicate_expense(plan, category_id=DECOR, label="Photographer", amount_inr=2_500)
        is None
    )
    assert (
        find_duplicate_expense(plan, category_id=CAKE, label="Photographer", amount_inr=2_000)
        is None
    )


# Rebalancing


def test_scenario_3_budget_cut_keeps_protected_cake() -> None:
    """Worked example from planning: ₹15,000 → ₹12,000, "don't change the cake"."""
    plan = make_plan(protected=["cake"])
    result = rebalance(plan, total_inr=12_000)
    assert result.feasible
    assert amounts(result.changes) == {
        "catering": 3_846,
        "decor": 2_308,
        "cake": 2_000,
        "other": 3_846,
    }
    assert reasons(result.changes)["cake"] is ChangeReason.PROTECTED
    assert reasons(result.changes)["decor"] is ChangeReason.PROPORTIONAL
    new_plan = result.apply(plan)
    assert new_plan.total_budget_inr == 12_000
    assert new_plan.category(CAKE).allocated_inr == 2_000


def test_cut_priority_is_used_first() -> None:
    result = rebalance(make_plan(), total_inr=12_000, cut_priority=[DECOR])
    assert amounts(result.changes) == {"catering": 5_000, "decor": 0, "cake": 2_000, "other": 5_000}
    assert reasons(result.changes)["decor"] is ChangeReason.CUT_FIRST
    assert reasons(result.changes)["catering"] is ChangeReason.UNCHANGED


def test_cut_priority_overflow_is_shared_proportionally() -> None:
    result = rebalance(make_plan(), total_inr=11_000, cut_priority=[DECOR, DECOR])
    after = amounts(result.changes)
    assert after["decor"] == 0
    assert after["catering"] + after["cake"] + after["other"] == 11_000
    assert reasons(result.changes)["other"] is ChangeReason.PROPORTIONAL


def test_protected_expense_sets_a_floor() -> None:
    plan = make_plan(expenses=[("catering", "Caterer advance", 4_500, True)])
    result = rebalance(plan, total_inr=4_500)
    after = amounts(result.changes)
    assert after == {"catering": 4_500, "decor": 0, "cake": 0, "other": 0}
    assert reasons(result.changes)["catering"] is ChangeReason.FLOOR_REACHED


def test_category_held_at_its_floor_is_explained() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_000, True)])
    result = rebalance(plan, total_inr=12_000)
    assert amounts(result.changes)["cake"] == 2_000
    assert reasons(result.changes)["cake"] is ChangeReason.FLOOR_REACHED


def test_infeasible_cut_reports_exact_shortfall_and_cannot_be_applied() -> None:
    plan = make_plan(protected=["cake"], expenses=[("catering", "Caterer", 5_000, True)])
    result = rebalance(plan, total_inr=6_000)
    assert not result.feasible
    assert result.shortfall_inr == 1_000
    assert result.changes == ()
    with pytest.raises(InfeasibleChangeError) as error:
        result.apply(plan)
    assert (error.value.code, error.value.shortfall_inr) == ("budget_shortfall", 1_000)


def test_increase_follows_current_proportions() -> None:
    result = rebalance(make_plan(), total_inr=18_000)
    assert amounts(result.changes) == {
        "catering": 6_000,
        "decor": 3_600,
        "cake": 2_400,
        "other": 6_000,
    }
    assert set(reasons(result.changes).values()) == {ChangeReason.PROPORTIONAL}


def test_increase_falls_back_to_weights_when_adjustable_amounts_are_zero() -> None:
    plan = make_plan(allocations={"venue": 1_000, "music": 0}, protected=["venue"])
    result = rebalance(plan, total_inr=3_000)
    assert amounts(result.changes) == {"venue": 1_000, "music": 2_000}


def test_increase_falls_back_to_equal_split_when_weights_are_zero() -> None:
    plan = make_plan(allocations={"venue": 1_000, "music": 0, "decor": 0}, protected=["venue"])
    plan = evolve(plan, categories=tuple(evolve(c, weight=0) for c in plan.categories))
    assert amounts(rebalance(plan, total_inr=2_000).changes) == {
        "venue": 1_000,
        "music": 500,
        "decor": 500,
    }


def test_category_below_its_floor_is_raised_to_it() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 2_500, True)])
    result = rebalance(plan, total_inr=15_000)
    assert amounts(result.changes)["cake"] == 2_500
    assert reasons(result.changes)["cake"] is ChangeReason.FLOOR_REACHED
    assert sum(amounts(result.changes).values()) == 15_000


def test_guest_scaled_pin_is_absorbed_by_other_categories() -> None:
    pins = {CATERING: Pin(amount_inr=7_000, reason=ChangeReason.GUEST_SCALED)}
    result = rebalance(make_plan(), total_inr=15_000, pins=pins)
    assert amounts(result.changes) == {
        "catering": 7_000,
        "decor": 2_400,
        "cake": 1_600,
        "other": 4_000,
    }
    assert reasons(result.changes)["catering"] is ChangeReason.GUEST_SCALED


def test_pin_below_protected_floor_is_raised_to_floor() -> None:
    plan = make_plan(expenses=[("catering", "Caterer", 4_000, True)])
    pins = {CATERING: Pin(amount_inr=3_000, reason=ChangeReason.GUEST_SCALED)}
    result = rebalance(plan, total_inr=15_000, pins=pins)
    assert amounts(result.changes)["catering"] == 4_000
    assert reasons(result.changes)["catering"] is ChangeReason.FLOOR_REACHED


def test_protection_overrides_a_pin() -> None:
    plan = make_plan(protected=["catering"])
    pins = {CATERING: Pin(amount_inr=9_000, reason=ChangeReason.GUEST_SCALED)}
    result = rebalance(plan, total_inr=15_000, pins=pins)
    assert amounts(result.changes)["catering"] == 5_000
    assert reasons(result.changes)["catering"] is ChangeReason.PROTECTED


def test_overcommitted_categories_are_flagged() -> None:
    plan = make_plan(expenses=[("catering", "Caterer quote", 4_800, False)])
    catering = next(
        c for c in rebalance(plan, total_inr=12_000).changes if c.category_id == CATERING
    )
    assert catering.after_inr < 4_800
    assert catering.overcommitted_by_inr == 4_800 - catering.after_inr
    assert catering.delta_inr == catering.after_inr - 5_000


def test_all_categories_fixed() -> None:
    plan = make_plan(protected=["catering", "decor", "cake", "other"])
    assert rebalance(plan, total_inr=15_000).feasible
    assert rebalance(plan, total_inr=14_000).shortfall_inr == 1_000
    with pytest.raises(InfeasibleChangeError) as error:
        rebalance(plan, total_inr=16_000)
    assert error.value.code == "no_adjustable_category"


@pytest.mark.parametrize("total", [MIN_BUDGET_INR - 1, MAX_BUDGET_INR + 1])
def test_rebalance_rejects_out_of_range_total(total: int) -> None:
    with pytest.raises(InvalidInputError) as error:
        rebalance(make_plan(), total_inr=total)
    assert error.value.code == "budget_out_of_range"


def test_rebalance_rejects_unknown_categories() -> None:
    with pytest.raises(NotFoundError):
        rebalance(make_plan(), total_inr=12_000, cut_priority=[CategoryId("fireworks")])
    with pytest.raises(NotFoundError):
        rebalance(
            make_plan(),
            total_inr=12_000,
            pins={CategoryId("fireworks"): Pin(amount_inr=1, reason=ChangeReason.GUEST_SCALED)},
        )


def test_rebalance_cannot_be_applied_to_a_different_plan() -> None:
    result = rebalance(make_plan(), total_inr=12_000)
    with pytest.raises(ValueError, match="different plan"):
        result.apply(make_plan(allocations={"venue": 15_000}))

"""Plan models: limits from ARCHITECTURE §9, invariants and immutability."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    BudgetCategory,
    CategoryId,
    Expense,
    ExpenseId,
    Task,
    TaskId,
    evolve,
    next_id,
    validate_event_date,
)
from tests.factories import make_plan


def test_factory_plan_is_valid() -> None:
    plan = make_plan()
    assert plan.total_budget_inr == 15_000
    assert sum(c.allocated_inr for c in plan.categories) == plan.total_budget_inr


def test_plan_is_immutable() -> None:
    plan = make_plan()
    with pytest.raises(ValidationError):
        plan.guest_count = 99  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        evolve(make_plan(), surprise="field")


@pytest.mark.parametrize(
    ("total", "valid"),
    [
        (MIN_BUDGET_INR - 1, False),
        (MIN_BUDGET_INR, True),
        (MAX_BUDGET_INR, True),
        (MAX_BUDGET_INR + 1, False),
    ],
)
def test_total_budget_limits(total: int, *, valid: bool) -> None:
    if valid:
        assert make_plan(allocations={"all": total}).total_budget_inr == total
    else:
        with pytest.raises(ValidationError):
            make_plan(allocations={"all": total})


@pytest.mark.parametrize(("guests", "valid"), [(0, False), (1, True), (MAX_GUESTS, True)])
def test_guest_count_limits(guests: int, *, valid: bool) -> None:
    if valid:
        assert make_plan(guest_count=guests).guest_count == guests
    else:
        with pytest.raises(ValidationError):
            make_plan(guest_count=guests)


def test_guest_count_above_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_plan(guest_count=MAX_GUESTS + 1)


def test_text_is_normalized_and_length_checked() -> None:
    plan = make_plan()
    assert evolve(plan, title="  Priya's    Haldi ").title == "Priya's Haldi"
    assert evolve(plan, title="प्रिया की हल्दी").title == "प्रिया की हल्दी"
    assert len(evolve(plan, title="x" * 80).title) == 80
    for bad in ("x" * 81, "   ", ""):
        with pytest.raises(ValidationError):
            evolve(plan, title=bad)


def test_category_ids_must_be_slugs() -> None:
    with pytest.raises(ValidationError):
        BudgetCategory(id=CategoryId("Bad Id"), name="Bad", weight=1, allocated_inr=0)


def test_duplicate_ids_are_rejected() -> None:
    plan = make_plan(allocations={"food": 1_000})
    duplicated = (plan.categories[0], plan.categories[0])
    with pytest.raises(ValidationError, match="duplicate category id: food"):
        evolve(plan, categories=duplicated, total_budget_inr=2_000)


def test_expense_must_reference_known_category() -> None:
    orphan = Expense(
        id=ExpenseId("exp-1"), category_id=CategoryId("nope"), label="Lights", amount_inr=500
    )
    with pytest.raises(ValidationError, match="unknown category id: nope"):
        evolve(make_plan(), expenses=(orphan,))


def test_task_must_reference_known_category() -> None:
    task = Task(
        id=TaskId("task-1"),
        title="Book hall",
        category_id=CategoryId("hall"),
        offset_days=30,
        due_date=date(2026, 11, 12),
    )
    with pytest.raises(ValidationError, match="unknown category id: hall"):
        evolve(make_plan(), tasks=(task,))


def test_allocations_must_sum_to_total() -> None:
    plan = make_plan()
    with pytest.raises(ValidationError, match="must equal the total budget"):
        evolve(plan, total_budget_inr=plan.total_budget_inr + 1)


def test_lookups_raise_not_found() -> None:
    plan = make_plan()
    assert plan.category(CategoryId("cake")).allocated_inr == 2_000
    with pytest.raises(NotFoundError):
        plan.category(CategoryId("fireworks"))
    with pytest.raises(NotFoundError):
        plan.expense(ExpenseId("exp-99"))


def test_committed_and_protected_floor() -> None:
    plan = make_plan(expenses=[("cake", "Cake", 1_800, True), ("cake", "Candles", 150, False)])
    assert plan.committed_inr(CategoryId("cake")) == 1_950
    assert plan.protected_floor_inr(CategoryId("cake")) == 1_800
    assert plan.protected_floor_inr(CategoryId("decor")) == 0


def test_next_id_ignores_other_prefixes_and_non_numbers() -> None:
    assert next_id("exp", ["exp-2", "exp-x", "expense-9", "task-5"]) == "exp-3"


TODAY = date(2026, 10, 22)


@pytest.mark.parametrize(
    ("event_date", "code"),
    [
        (TODAY - timedelta(days=1), "event_date_in_past"),
        (TODAY + timedelta(days=MAX_DAYS_AHEAD + 1), "event_date_too_far"),
    ],
)
def test_event_date_window_rejects(event_date: date, code: str) -> None:
    with pytest.raises(InvalidInputError) as error:
        validate_event_date(event_date, TODAY)
    assert error.value.code == code


@pytest.mark.parametrize("offset", [0, 1, MAX_DAYS_AHEAD])
def test_event_date_window_accepts(offset: int) -> None:
    validate_event_date(TODAY + timedelta(days=offset), TODAY)

"""Opening plan (F1): complete, valid and adapted to how close the event is."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.domain.budget import Emphasis, EmphasisDirection, EmphasisStrength, NewExpense, add_expense
from app.domain.catalog import IdeaSpec, TaskSpec, spec_for
from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    BudgetImpact,
    CategoryId,
    EventType,
    TaskSource,
)
from app.domain.planning import EventDetails, Personalization, draft_plan, personalize_plan

TODAY = date(2026, 10, 22)


def details(**overrides: object) -> EventDetails:
    values: dict[str, object] = {
        "event_type": EventType.WEDDING,
        "title": "Ananya and Rohan's wedding",
        "city": "Jaipur",
        "event_date": date(2027, 2, 14),
        "guest_count": 250,
        "total_budget_inr": 15_00_000,
        "preferences": "Rajasthani theme, lots of marigold",
        "sub_events": ("sangeet", "haldi", "ceremony"),
    }
    return EventDetails.model_validate(values | overrides)


def test_draft_plan_is_complete() -> None:
    plan = draft_plan(details(), TODAY)
    assert plan.version == 1
    assert plan.sub_events == ("haldi", "sangeet", "ceremony")
    assert sum(c.allocated_inr for c in plan.categories) == 15_00_000
    titles = {t.title for t in plan.tasks}
    assert "Book the venue" in titles
    assert "Book a choreographer and plan performances" in titles, "sangeet task"
    assert "Book the mehendi artist" not in titles, "mehendi was not chosen"
    assert any(v.vendor_type == "mandap" for v in plan.vendors)
    assert sorted(v.priority for v in plan.vendors) == list(range(1, len(plan.vendors) + 1))


def test_last_minute_event_compresses_tasks_and_flags_vendors() -> None:
    plan = draft_plan(details(event_date=TODAY + timedelta(days=10)), TODAY)
    assert any(t.compressed for t in plan.tasks)
    assert all(t.due_date >= TODAY for t in plan.tasks)
    assert any(v.urgent for v in plan.vendors)


def test_draft_plan_rejects_past_dates_and_unknown_sub_events() -> None:
    with pytest.raises(InvalidInputError):
        draft_plan(details(event_date=TODAY - timedelta(days=1)), TODAY)
    with pytest.raises(InvalidInputError):
        draft_plan(details(sub_events=("kids_games",)), TODAY)


def test_event_details_validate_input() -> None:
    with pytest.raises(ValidationError):
        details(guest_count=0)
    with pytest.raises(ValidationError):
        details(total_budget_inr=500)
    with pytest.raises(ValidationError):
        details(title="   ")


@given(
    event_type=st.sampled_from(list(EventType)),
    data=st.data(),
    days_ahead=st.integers(0, MAX_DAYS_AHEAD),
    guests=st.integers(1, MAX_GUESTS),
    total=st.integers(MIN_BUDGET_INR, MAX_BUDGET_INR),
)
def test_any_valid_details_give_a_valid_plan(
    event_type: EventType, data: st.DataObject, days_ahead: int, guests: int, total: int
) -> None:
    sub_ids = [s.id for s in spec_for(event_type).sub_events]
    chosen = tuple(data.draw(st.lists(st.sampled_from(sub_ids), max_size=5)))
    plan = draft_plan(
        details(
            event_type=event_type,
            event_date=TODAY + timedelta(days=days_ahead),
            guest_count=guests,
            total_budget_inr=total,
            sub_events=chosen,
        ),
        TODAY,
    )
    assert plan.total_budget_inr == total
    assert all(t.due_date >= TODAY for t in plan.tasks)
    assert all(v.book_by >= TODAY for v in plan.vendors)
    assert sorted(v.priority for v in plan.vendors) == list(range(1, len(plan.vendors) + 1))


# Personalization (applied by code after the AI's suggestions are validated)


def personalization(**overrides: object) -> Personalization:
    values: dict[str, object] = {
        "emphasis": {
            CategoryId("music"): Emphasis(
                direction=EmphasisDirection.INCREASE, strength=EmphasisStrength.MEDIUM
            )
        },
        "tasks": (
            TaskSpec(
                key="ai",
                title="Book a folk dance troupe",
                category_id=CategoryId("music"),
                offset_days=45,
                why="Rajasthani theme.",
            ),
        ),
        "ideas": (
            IdeaSpec(
                title="Puppet show at the sangeet",
                description="A kathputli show between dance performances.",
                category_id=CategoryId("music"),
                budget_impact=BudgetImpact.ADDS,
                why="Local Rajasthani art that guests of all ages enjoy.",
            ),
        ),
        "vendor_notes": {"music": "Ask for Rajasthani folk musicians for the sangeet."},
    }
    return Personalization.model_validate(values | overrides)


def test_personalization_is_applied_by_code() -> None:
    draft = draft_plan(details(), TODAY)
    snapshot = draft.model_dump()
    plan = personalize_plan(draft, personalization(), TODAY)
    music_before = next(c.allocated_inr for c in draft.categories if c.id == "music")
    music_after = next(c.allocated_inr for c in plan.categories if c.id == "music")
    assert music_after > music_before
    assert sum(c.allocated_inr for c in plan.categories) == draft.total_budget_inr
    new_task = plan.tasks[-1]
    assert (new_task.source, new_task.why, new_task.due_date) == (
        TaskSource.AI,
        "Rajasthani theme.",
        draft.event_date - timedelta(days=45),
    )
    assert [(i.id, i.why) for i in plan.ideas] == [
        ("idea-1", "Local Rajasthani art that guests of all ages enjoy.")
    ]
    music_vendor = next(v for v in plan.vendors if v.vendor_type == "music")
    assert music_vendor.why == "Ask for Rajasthani folk musicians for the sangeet."
    assert draft.model_dump() == snapshot


def test_empty_personalization_keeps_the_draft_budget() -> None:
    draft = draft_plan(details(), TODAY)
    plan = personalize_plan(draft, Personalization(), TODAY)
    assert [c.allocated_inr for c in plan.categories] == [c.allocated_inr for c in draft.categories]
    assert plan.tasks == draft.tasks


def test_personalization_needs_a_fresh_plan() -> None:
    draft = draft_plan(details(), TODAY)
    used, _ = add_expense(
        draft, NewExpense(category_id=CategoryId("decor"), label="Flowers", amount_inr=5_000)
    )
    with pytest.raises(ValueError, match="freshly drafted"):
        personalize_plan(used, Personalization(), TODAY)


def test_personalization_rejects_unknown_categories() -> None:
    emphasis = {
        CategoryId("fireworks"): Emphasis(
            direction=EmphasisDirection.INCREASE, strength=EmphasisStrength.SMALL
        )
    }
    with pytest.raises(NotFoundError):
        personalize_plan(draft_plan(details(), TODAY), personalization(emphasis=emphasis), TODAY)

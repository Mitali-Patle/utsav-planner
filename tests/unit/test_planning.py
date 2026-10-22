"""Opening plan (F1): complete, valid and adapted to how close the event is."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.domain.catalog import spec_for
from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_BUDGET_INR,
    MAX_DAYS_AHEAD,
    MAX_GUESTS,
    MIN_BUDGET_INR,
    EventType,
)
from app.domain.planning import EventDetails, draft_plan

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

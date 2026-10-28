"""The user's own timeline tasks and vendors: dates, limits, removal and moving with the event."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_DAYS_AFTER,
    MAX_PRIORITY,
    MAX_TASKS,
    EventPlan,
    EventType,
    TaskSource,
    VendorStatus,
    evolve,
)
from app.domain.planning import EventDetails, draft_plan
from app.domain.timeline import NewTask, add_own_task, remove_own_task, reschedule_tasks
from app.domain.vendors import (
    OWN_VENDOR_TYPE,
    NewVendor,
    add_own_vendor,
    remove_own_vendor,
    reschedule_vendors,
    set_vendor_status,
)

TODAY = date(2026, 10, 28)
EVENT = date(2026, 12, 13)


def birthday() -> EventPlan:
    details = EventDetails(
        event_type=EventType.BIRTHDAY,
        title="Aarav turns 7",
        city="Pune",
        event_date=EVENT,
        guest_count=30,
        total_budget_inr=40_000,
    )
    return draft_plan(details, TODAY)


def test_own_task_keeps_its_distance_from_the_event_and_moves_with_it() -> None:
    plan, task = add_own_task(
        birthday(),
        NewTask(title="Collect the cake stand", due_date=EVENT - timedelta(days=2)),
        TODAY,
    )
    assert (task.offset_days, task.source, task.due_date) == (
        2,
        TaskSource.USER,
        date(2026, 12, 11),
    )
    assert plan.task(task.id) == task
    moved = reschedule_tasks(plan.tasks, EVENT + timedelta(days=7), TODAY)
    assert next(t for t in moved if t.id == task.id).due_date == date(2026, 12, 18)


def test_own_task_ids_never_collide() -> None:
    plan, first = add_own_task(birthday(), NewTask(title="One", due_date=TODAY), TODAY)
    plan, second = add_own_task(plan, NewTask(title="Two", due_date=TODAY), TODAY)
    assert first.id != second.id
    assert len({t.id for t in plan.tasks}) == len(plan.tasks)


@pytest.mark.parametrize(
    ("due", "code"),
    [
        (TODAY - timedelta(days=1), "date_in_past"),
        (EVENT + timedelta(days=MAX_DAYS_AFTER + 1), "date_too_late"),
    ],
)
def test_own_task_dates_must_be_reachable(due: date, code: str) -> None:
    with pytest.raises(InvalidInputError) as error:
        add_own_task(birthday(), NewTask(title="Something", due_date=due), TODAY)
    assert error.value.code == code


def test_own_task_on_the_last_allowed_day_after_the_event_is_fine() -> None:
    _, task = add_own_task(
        birthday(),
        NewTask(title="Return the chairs", due_date=EVENT + timedelta(days=MAX_DAYS_AFTER)),
        TODAY,
    )
    assert task.offset_days == -MAX_DAYS_AFTER


def test_a_plan_cannot_hold_more_than_the_task_limit() -> None:
    plan = birthday()
    filler = plan.tasks[0]
    full = plan.with_changes(
        tasks=tuple(evolve(filler, id=f"task-{n}") for n in range(1, MAX_TASKS + 1))
    )
    with pytest.raises(InvalidInputError) as error:
        add_own_task(full, NewTask(title="One more", due_date=TODAY), TODAY)
    assert error.value.code == "too_many_tasks"


@pytest.mark.parametrize("title", ["", "   ", "x" * 81])
def test_own_task_needs_a_short_title(title: str) -> None:
    with pytest.raises(ValidationError):
        NewTask(title=title, due_date=TODAY)


def test_only_tasks_the_user_added_can_be_removed() -> None:
    plan, task = add_own_task(birthday(), NewTask(title="Call Nani", due_date=TODAY), TODAY)
    assert task.id not in {t.id for t in remove_own_task(plan, task.id).tasks}
    catalog_task = next(t for t in plan.tasks if t.source is TaskSource.CATALOG)
    with pytest.raises(InvalidInputError) as error:
        remove_own_task(plan, catalog_task.id)
    assert error.value.code == "not_removable"


def test_own_vendor_is_ranked_with_the_others_and_moves_with_the_event() -> None:
    plan, vendor = add_own_vendor(birthday(), NewVendor(name="Magician", book_by=TODAY), TODAY)
    assert vendor.vendor_type == OWN_VENDOR_TYPE
    assert vendor.lead_days == (EVENT - TODAY).days
    assert vendor.priority == 1, "booking today makes it the most urgent"
    assert sorted(v.priority for v in plan.vendors) == list(range(1, len(plan.vendors) + 1))
    moved = reschedule_vendors(plan.vendors, EVENT + timedelta(days=10), TODAY)
    assert next(v for v in moved if v.id == vendor.id).book_by == TODAY + timedelta(days=10)


def test_own_vendor_can_be_booked_like_any_other() -> None:
    plan, vendor = add_own_vendor(birthday(), NewVendor(name="Dhol player", book_by=TODAY), TODAY)
    booked = set_vendor_status(plan, vendor.id, VendorStatus.BOOKED)
    assert booked.vendor(vendor.id).status is VendorStatus.BOOKED


@pytest.mark.parametrize(
    ("book_by", "code"),
    [
        (TODAY - timedelta(days=1), "date_in_past"),
        (EVENT + timedelta(days=1), "after_event"),
    ],
)
def test_own_vendor_dates_must_be_reachable(book_by: date, code: str) -> None:
    with pytest.raises(InvalidInputError) as error:
        add_own_vendor(birthday(), NewVendor(name="Tent house", book_by=book_by), TODAY)
    assert error.value.code == code


def test_own_vendor_cannot_be_booked_more_than_two_years_ahead() -> None:
    details = EventDetails(
        event_type=EventType.WEDDING,
        title="Wedding",
        city="Jaipur",
        event_date=TODAY + timedelta(days=1000),
        guest_count=200,
        total_budget_inr=1_500_000,
    )
    with pytest.raises(InvalidInputError) as error:
        add_own_vendor(draft_plan(details, TODAY), NewVendor(name="Elephant", book_by=TODAY), TODAY)
    assert error.value.code == "date_too_early"


def test_a_checklist_cannot_hold_more_than_the_vendor_limit() -> None:
    plan = birthday()
    filler = plan.vendors[0]
    full = plan.with_changes(
        vendors=tuple(
            evolve(filler, id=f"vendor-{n}", priority=n) for n in range(1, MAX_PRIORITY + 1)
        )
    )
    with pytest.raises(InvalidInputError) as error:
        add_own_vendor(full, NewVendor(name="One more", book_by=TODAY), TODAY)
    assert error.value.code == "too_many_vendors"


def test_only_vendors_the_user_added_can_be_removed() -> None:
    plan, vendor = add_own_vendor(birthday(), NewVendor(name="Magician", book_by=TODAY), TODAY)
    left = remove_own_vendor(plan, vendor.id)
    assert vendor.id not in {v.id for v in left.vendors}
    assert sorted(v.priority for v in left.vendors) == list(range(1, len(left.vendors) + 1))
    from_the_plan = next(v for v in plan.vendors if v.vendor_type != OWN_VENDOR_TYPE)
    with pytest.raises(InvalidInputError) as error:
        remove_own_vendor(plan, from_the_plan.id)
    assert error.value.code == "not_removable"

"""Deadlines, rescheduling (SPEC scenario 5), vendor priority and booking status."""

from datetime import date, timedelta

import pytest

from app.domain.catalog import TaskSpec, VendorSpec, tasks_for, vendors_for
from app.domain.errors import InvalidInputError, NotFoundError
from app.domain.models import CategoryId, EventType, TaskId, TaskSource, VendorId, VendorStatus
from app.domain.timeline import (
    build_tasks,
    is_overdue,
    reschedule_tasks,
    set_task_done,
    sort_tasks,
)
from app.domain.vendors import build_vendors, prioritize, reschedule_vendors, set_vendor_status
from tests.factories import make_plan

TODAY = date(2026, 10, 22)
EVENT = date(2026, 12, 12)


def spec(key: str, offset: int) -> TaskSpec:
    return TaskSpec(key=key, title=key.replace("_", " "), offset_days=offset)


def vendor_spec(kind: str, lead: int) -> VendorSpec:
    return VendorSpec(
        vendor_type=kind, name=kind.title(), category_id=CategoryId("decor"), lead_days=lead
    )


def test_tasks_get_deadlines_counted_back_from_the_event() -> None:
    tasks = build_tasks([spec("invite", 21), spec("cleanup", -1)], EVENT, TODAY)
    assert [(t.id, t.due_date, t.compressed) for t in tasks] == [
        ("task-1", date(2026, 11, 21), False),
        ("task-2", date(2026, 12, 13), False),
    ]
    assert all(t.source is TaskSource.CATALOG for t in tasks)


def test_passed_deadlines_become_due_today_and_compressed() -> None:
    task = build_tasks([spec("book_venue", 180)], EVENT, TODAY)[0]
    assert (task.due_date, task.compressed) == (TODAY, True)


def test_new_task_ids_continue_after_existing_ones() -> None:
    tasks = build_tasks([spec("a", 1), spec("b", 2)], EVENT, TODAY, existing_ids=["task-7"])
    assert [t.id for t in tasks] == ["task-8", "task-9"]


def test_scenario_5_moving_the_event_recalculates_open_deadlines() -> None:
    tasks = build_tasks([spec("invite", 21), spec("decor", 7), spec("cake", 3)], EVENT, TODAY)
    tasks = (tasks[0], tasks[1].model_copy(update={"done": True}), tasks[2])
    moved = reschedule_tasks(tasks, EVENT - timedelta(days=3), TODAY)
    assert moved[0].due_date == date(2026, 11, 18)
    assert moved[1] == tasks[1], "completed tasks keep their dates"
    assert moved[2].due_date == date(2026, 12, 6)


def test_moving_the_event_closer_compresses_and_moving_away_releases() -> None:
    task = build_tasks([spec("invite", 21)], EVENT, TODAY)
    soon = reschedule_tasks(task, TODAY + timedelta(days=5), TODAY)[0]
    assert (soon.due_date, soon.compressed) == (TODAY, True)
    later = reschedule_tasks((soon,), EVENT, TODAY)[0]
    assert (later.due_date, later.compressed) == (date(2026, 11, 21), False)


def test_task_done_toggle_and_overdue() -> None:
    plan = make_plan()
    plan = plan.with_changes(tasks=build_tasks([spec("invite", 21)], EVENT, TODAY))
    task_id = TaskId("task-1")
    assert not is_overdue(plan.task(task_id), TODAY)
    assert is_overdue(plan.task(task_id), date(2026, 11, 22))
    done = set_task_done(plan, task_id, done=True)
    assert done.task(task_id).done
    assert not is_overdue(done.task(task_id), date(2026, 11, 22))
    with pytest.raises(NotFoundError):
        set_task_done(plan, TaskId("task-99"), done=True)


def test_sort_tasks_by_deadline() -> None:
    tasks = build_tasks([spec("late", 1), spec("early", 30), spec("mid", 10)], EVENT, TODAY)
    assert [t.title for t in sort_tasks(tasks)] == ["early", "mid", "late"]


def test_vendors_are_prioritized_by_book_by_date() -> None:
    vendors = build_vendors(
        [vendor_spec("cake", 7), vendor_spec("venue", 30), vendor_spec("decor", 14)], EVENT, TODAY
    )
    assert [(v.vendor_type, v.priority) for v in vendors] == [
        ("venue", 1),
        ("decor", 2),
        ("cake", 3),
    ]
    assert vendors[0].book_by == date(2026, 11, 12)


def test_vendor_past_its_booking_window_is_urgent() -> None:
    vendor = build_vendors([vendor_spec("venue", 120)], EVENT, TODAY)[0]
    assert (vendor.book_by, vendor.urgent) == (TODAY, True)


def test_ties_go_to_longer_lead_time_then_original_order() -> None:
    vendors = build_vendors(
        [vendor_spec("a", 120), vendor_spec("b", 90), vendor_spec("c", 90)], EVENT, TODAY
    )
    assert [v.vendor_type for v in vendors] == ["a", "b", "c"]


def test_rescheduling_leaves_booked_vendors_alone() -> None:
    vendors = build_vendors([vendor_spec("venue", 30), vendor_spec("cake", 7)], EVENT, TODAY)
    booked = (vendors[0].model_copy(update={"status": VendorStatus.BOOKED}), vendors[1])
    moved = reschedule_vendors(booked, EVENT - timedelta(days=10), TODAY)
    by_type = {v.vendor_type: v for v in moved}
    assert by_type["venue"].book_by == vendors[0].book_by
    assert by_type["cake"].book_by == date(2026, 11, 25)
    assert by_type["cake"].priority == 1, "unbooked vendors come first"


@pytest.mark.parametrize(
    ("start", "target", "allowed"),
    [
        (VendorStatus.TODO, VendorStatus.CONTACTED, True),
        (VendorStatus.TODO, VendorStatus.BOOKED, True),
        (VendorStatus.CONTACTED, VendorStatus.BOOKED, True),
        (VendorStatus.CONTACTED, VendorStatus.TODO, True),
        (VendorStatus.BOOKED, VendorStatus.CONTACTED, True),
        (VendorStatus.BOOKED, VendorStatus.TODO, False),
        (VendorStatus.TODO, VendorStatus.TODO, False),
    ],
)
def test_vendor_status_changes(start: VendorStatus, target: VendorStatus, *, allowed: bool) -> None:
    vendors = build_vendors([vendor_spec("venue", 30)], EVENT, TODAY)
    plan = make_plan().with_changes(vendors=(vendors[0].model_copy(update={"status": start}),))
    vendor_id = VendorId("vendor-1")
    if allowed:
        assert set_vendor_status(plan, vendor_id, target).vendor(vendor_id).status is target
    else:
        with pytest.raises(InvalidInputError) as error:
            set_vendor_status(plan, vendor_id, target)
        assert error.value.code == "invalid_status_change"


def test_unknown_vendor_is_rejected() -> None:
    with pytest.raises(NotFoundError):
        set_vendor_status(make_plan(), VendorId("vendor-9"), VendorStatus.BOOKED)


def test_prioritize_is_stable_for_an_empty_list() -> None:
    assert prioritize([]) == ()


def test_catalog_tasks_and_vendors_build_for_every_event_type() -> None:
    for event_type in EventType:
        assert build_tasks(tasks_for(event_type, []), EVENT, TODAY)
        assert build_vendors(vendors_for(event_type, []), EVENT, TODAY)

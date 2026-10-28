"""Task deadlines: built from the catalog, kept correct when the event date moves.

A deadline that has already passed is never dropped or left in the past: the task becomes
due today and is flagged as compressed, which is what last-minute planning needs.
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from app.domain.catalog import TaskSpec
from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_DAYS_AFTER,
    MAX_TASKS,
    DomainModel,
    EventPlan,
    ShortText,
    Task,
    TaskId,
    TaskSource,
    evolve,
    id_sequence,
    next_id,
)


def deadline(event_date: date, days_before: int, today: date) -> tuple[date, bool]:
    """Compute a deadline relative to the event, never earlier than today.

    Args:
        event_date: Date of the event.
        days_before: Days before the event (negative means after it).
        today: Current date.

    Returns:
        The deadline and whether it had to be moved up to today.

    Examples:
        >>> deadline(date(2026, 12, 12), 10, date(2026, 11, 1))
        (datetime.date(2026, 12, 2), False)
        >>> deadline(date(2026, 12, 12), 60, date(2026, 11, 1))
        (datetime.date(2026, 11, 1), True)
    """
    due = event_date - timedelta(days=days_before)
    if due < today:
        return today, True
    return due, False


def build_tasks(
    specs: Sequence[TaskSpec],
    event_date: date,
    today: date,
    *,
    existing_ids: Iterable[str] = (),
    source: TaskSource = TaskSource.CATALOG,
) -> tuple[Task, ...]:
    """Turn task specs into dated tasks.

    Args:
        specs: Tasks to create.
        event_date: Date of the event.
        today: Current date.
        existing_ids: Task ids already in the plan, so new ids never collide.
        source: Where the tasks come from.

    Returns:
        New tasks in the same order as `specs`.
    """
    ids = id_sequence("task", existing_ids)
    tasks = []
    for spec in specs:
        due, compressed = deadline(event_date, spec.offset_days, today)
        tasks.append(
            Task(
                id=TaskId(next(ids)),
                title=spec.title,
                category_id=spec.category_id,
                offset_days=spec.offset_days,
                due_date=due,
                compressed=compressed,
                source=source,
                why=spec.why,
            )
        )
    return tuple(tasks)


def reschedule_tasks(tasks: Sequence[Task], new_event_date: date, today: date) -> tuple[Task, ...]:
    """Recompute deadlines after the event date changes.

    Completed tasks keep their dates; every other task moves with the event and its
    compressed flag is recomputed.
    """
    rescheduled = []
    for task in tasks:
        if task.done:
            rescheduled.append(task)
            continue
        due, compressed = deadline(new_event_date, task.offset_days, today)
        rescheduled.append(evolve(task, due_date=due, compressed=compressed))
    return tuple(rescheduled)


def set_task_done(plan: EventPlan, task_id: TaskId, *, done: bool) -> EventPlan:
    """Mark a task as done or not done.

    Raises:
        NotFoundError: If the task does not exist.
    """
    plan.task(task_id)
    return plan.with_changes(
        tasks=tuple(evolve(t, done=done) if t.id == task_id else t for t in plan.tasks)
    )


class NewTask(DomainModel):
    """Something the user wants to remember, by a date of their choosing."""

    title: ShortText
    due_date: date


def add_own_task(plan: EventPlan, draft: NewTask, today: date) -> tuple[EventPlan, Task]:
    """Add the user's own task to the timeline.

    Its offset from the event is kept, so it moves with the event like every other task.

    Raises:
        InvalidInputError: If the date is in the past or too long after the event, or the
            plan already has the most tasks allowed.

    Examples:
        >>> from app.domain.models import EventType
        >>> from app.domain.planning import EventDetails, draft_plan
        >>> plan = draft_plan(EventDetails(event_type=EventType.BIRTHDAY, title="Aarav",
        ...     city="Pune", event_date=date(2026, 12, 13), guest_count=30,
        ...     total_budget_inr=40_000), today=date(2026, 10, 28))
        >>> plan, task = add_own_task(plan, NewTask(title="Collect the cake stand",
        ...     due_date=date(2026, 12, 12)), today=date(2026, 10, 28))
        >>> task.offset_days, task.source.value
        (1, 'user')
    """
    if len(plan.tasks) >= MAX_TASKS:
        raise InvalidInputError(
            f"A plan can have at most {MAX_TASKS} tasks.", code="too_many_tasks"
        )
    if draft.due_date < today:
        raise InvalidInputError("Choose today or a later date.", code="date_in_past")
    offset = (plan.event_date - draft.due_date).days
    if offset < -MAX_DAYS_AFTER:
        raise InvalidInputError(
            f"Choose a date no more than {MAX_DAYS_AFTER} days after the event.",
            code="date_too_late",
        )
    task = Task(
        id=TaskId(next_id("task", (t.id for t in plan.tasks))),
        title=draft.title,
        offset_days=offset,
        due_date=draft.due_date,
        source=TaskSource.USER,
    )
    return plan.with_changes(tasks=(*plan.tasks, task)), task


def remove_own_task(plan: EventPlan, task_id: TaskId) -> EventPlan:
    """Remove a task the user added; the plan's own tasks stay.

    Raises:
        NotFoundError: If the task does not exist.
        InvalidInputError: If the task was not added by the user.
    """
    if plan.task(task_id).source is not TaskSource.USER:
        raise InvalidInputError("Only tasks you added can be removed.", code="not_removable")
    return plan.with_changes(tasks=tuple(t for t in plan.tasks if t.id != task_id))


def is_overdue(task: Task, today: date) -> bool:
    """Whether an unfinished task's deadline has passed."""
    return not task.done and task.due_date < today


def sort_tasks(tasks: Iterable[Task]) -> list[Task]:
    """Order tasks by deadline, then by how early they were meant to start."""
    return sorted(tasks, key=lambda t: (t.due_date, -t.offset_days, t.title))

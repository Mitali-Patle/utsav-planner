"""Task deadlines: built from the catalog, kept correct when the event date moves.

A deadline that has already passed is never dropped or left in the past: the task becomes
due today and is flagged as compressed, which is what last-minute planning needs.
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from app.domain.catalog import TaskSpec
from app.domain.models import EventPlan, Task, TaskId, TaskSource, evolve, id_sequence


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


def is_overdue(task: Task, today: date) -> bool:
    """Whether an unfinished task's deadline has passed."""
    return not task.done and task.due_date < today


def sort_tasks(tasks: Iterable[Task]) -> list[Task]:
    """Order tasks by deadline, then by how early they were meant to start."""
    return sorted(tasks, key=lambda t: (t.due_date, -t.offset_days, t.title))

"""The plan page and My Plans: budget, timeline, vendors and what to do next."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from itertools import groupby
from typing import Any

from app.domain.budget import BudgetSummary, summarize
from app.domain.calendar import google_calendar_url
from app.domain.catalog import VENDOR_QUESTIONS
from app.domain.models import (
    MAX_DAYS_AFTER,
    CategoryId,
    EventPlan,
    Task,
    TaskSource,
    VendorItem,
    VendorStatus,
)
from app.domain.money import format_inr
from app.domain.timeline import is_overdue, sort_tasks
from app.domain.vendors import ALLOWED_STATUS_CHANGES, OWN_VENDOR_TYPE
from app.persistence.events import StoredEvent
from app.persistence.proposals import StoredProposal
from app.web.views.formatting import (
    EVENT_TYPE_LABELS,
    TASK_SOURCE_LABELS,
    VENDOR_STATUS_LABELS,
    ai_panel,
    format_date,
)
from app.web.views.forms import PlanForms
from app.web.views.proposals import history_row


def plans_context(events: Sequence[StoredEvent], today: date) -> dict[str, Any]:
    """Data for the My Plans page: this visitor's events, soonest first."""
    summaries = [_event_summary(e, today) for e in events]
    return {"events": sorted(summaries, key=lambda e: (e["days_left"] < 0, abs(e["days_left"])))}


def dashboard_context(
    stored: StoredEvent,
    history: Sequence[StoredProposal],
    today: date,
    *,
    forms: PlanForms | None = None,
) -> dict[str, Any]:
    """Data for the event dashboard; `forms` carries an add form's rejected input."""
    plan = stored.plan
    forms = forms or PlanForms()
    return {
        "event": _event_summary(stored, today),
        "plan": plan_sections(plan, today),
        "ideas": [
            {
                "title": i.title,
                "description": i.description,
                "why": i.why,
                "impact": i.budget_impact.value,
            }
            for i in plan.ideas
        ],
        "ai": ai_panel(stored.ai),
        "personalization_pending": stored.ai is None,
        "history": [history_row(p) for p in history],
        "expense_form": forms.expense,
        "task_form": forms.task,
        "vendor_form": forms.vendor,
        "min_date": today.isoformat(),
        "max_date": (plan.event_date + timedelta(days=MAX_DAYS_AFTER)).isoformat(),
        "event_day": plan.event_date.isoformat(),
        "keep_typed_input": False,
        "focus_ideas": False,
        "categories_for_select": [(c.id, c.name) for c in plan.categories],
        "change_result": None,
        "alternatives": None,
    }


def percent_of(part: int, whole: int) -> int:
    """Whole-number share of `whole`, past 100 when overspent.

    The text shows the true share; a `<progress max="100">` clamps the bar itself.

    Examples:
        >>> percent_of(2_500, 10_000), percent_of(1, 3), percent_of(15_000, 10_000)
        (25, 33, 150)
        >>> percent_of(500, 0), percent_of(0, 0)
        (100, 0)
    """
    if whole <= 0:
        return 100 if part > 0 else 0
    return part * 100 // whole


def plan_sections(plan: EventPlan, today: date) -> dict[str, Any]:
    """Budget, expenses, timeline and vendors: the parts that change after each edit."""
    summary = summarize(plan)
    names = {c.id: c.name for c in plan.categories}
    return {
        "summary": _summary(summary, plan),
        "categories": [
            {
                "id": row.category_id,
                "name": row.name,
                "allocated": format_inr(row.allocated_inr),
                "spent": format_inr(row.spent_inr),
                "remaining": format_inr(row.remaining_inr),
                "overspent": format_inr(row.overspent_by_inr) if row.overspent_by_inr else None,
                "protected": row.protected,
                "spent_percent": percent_of(row.spent_inr, row.allocated_inr),
            }
            for row in summary.categories
        ],
        "expenses": [
            {
                "id": e.id,
                "label": e.label,
                "category": names[e.category_id],
                "amount": format_inr(e.amount_inr),
                "amount_inr": e.amount_inr,
                "protected": e.protected,
            }
            for e in plan.expenses
        ],
        "task_months": [
            _task_month(list(tasks), names, today)
            for _, tasks in groupby(sort_tasks(plan.tasks), key=_year_month)
        ],
        "vendors": [_vendor_row(v) for v in sorted(plan.vendors, key=lambda v: v.priority)],
        "next_up": _next_up(plan, names, today),
    }


NEXT_TASKS = 3
"""How many unfinished tasks "What to do next" shows."""


def _next_up(plan: EventPlan, names: Mapping[CategoryId, str], today: date) -> dict[str, Any]:
    """The most pressing unfinished tasks (overdue first, then by deadline) and vendor.

    A vendor already named by one of those tasks ("Book the venue...") is skipped for the
    next one, so the list never says the same thing twice.
    """
    tasks = [t for t in sort_tasks(plan.tasks) if not t.done][:NEXT_TASKS]
    titles = " ".join(t.title.casefold() for t in tasks)
    unbooked = sorted(
        (v for v in plan.vendors if v.status is not VendorStatus.BOOKED), key=lambda v: v.priority
    )
    vendor = next((v for v in unbooked if v.name.casefold() not in titles), None)
    return {
        "tasks": [_task_row(t, names, today) for t in tasks],
        "vendor": _vendor_row(vendor) if vendor else None,
    }


def _event_summary(stored: StoredEvent, today: date) -> dict[str, Any]:
    plan = stored.plan
    return {
        "id": stored.id,
        "version": stored.version,
        "title": plan.title,
        "type_label": EVENT_TYPE_LABELS[plan.event_type],
        "city": plan.city,
        "date": format_date(plan.event_date),
        "days_left": (plan.event_date - today).days,
        "guests": plan.guest_count,
        "preferences": plan.preferences,
        "google_calendar": google_calendar_url(plan),
    }


def _summary(summary: BudgetSummary, plan: EventPlan) -> dict[str, Any]:
    return {
        "total": format_inr(summary.total_inr),
        "spent": format_inr(summary.spent_inr),
        "remaining": format_inr(summary.remaining_inr),
        "shortfall": format_inr(summary.shortfall_inr) if summary.is_over_budget else None,
        "over_plan": [c.name for c in summary.categories if c.overspent_by_inr],
        "per_guest": format_inr(summary.per_guest_inr),
        "guests": plan.guest_count,
        "spent_percent": percent_of(summary.spent_inr, summary.total_inr),
    }


def _year_month(task: Task) -> tuple[int, int]:
    return task.due_date.year, task.due_date.month


def _task_month(
    tasks: Sequence[Task], names: Mapping[CategoryId, str], today: date
) -> dict[str, Any]:
    """One month of the timeline: its heading and its tasks, already in date order."""
    return {
        "label": f"{tasks[0].due_date:%B %Y}",
        "tasks": [_task_row(t, names, today) for t in tasks],
    }


def _task_row(task: Task, names: Mapping[CategoryId, str], today: date) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "due": format_date(task.due_date),
        "done": task.done,
        "overdue": is_overdue(task, today),
        "compressed": task.compressed,
        "source": TASK_SOURCE_LABELS[task.source],
        "ai": task.source is TaskSource.AI,
        "own": task.source is TaskSource.USER,
        "why": task.why,
        "category": names.get(task.category_id) if task.category_id else None,
    }


def _vendor_row(vendor: VendorItem) -> dict[str, Any]:
    return {
        "id": vendor.id,
        "name": vendor.name,
        "priority": vendor.priority,
        "book_by": format_date(vendor.book_by),
        "urgent": vendor.urgent,
        "status": vendor.status.value,
        "status_label": VENDOR_STATUS_LABELS[vendor.status],
        "next": [
            (s.value, VENDOR_STATUS_LABELS[s])
            for s in sorted(ALLOWED_STATUS_CHANGES[vendor.status])
        ],
        "why": vendor.why,
        "own": vendor.vendor_type == OWN_VENDOR_TYPE,
        "questions": VENDOR_QUESTIONS.get(vendor.vendor_type, ()),
    }

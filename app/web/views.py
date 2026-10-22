"""View models: domain objects turned into simple values for templates.

All formatting and labelling happens here, in tested Python, so templates only lay out
ready-made values.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.ai.client import Provenance
from app.domain.budget import BudgetSummary, CategoryChange, ChangeReason, summarize
from app.domain.catalog import CATALOG
from app.domain.changes import Proposal, ScheduleChange
from app.domain.models import (
    CategoryId,
    EventPlan,
    EventType,
    Task,
    TaskSource,
    VendorItem,
    VendorStatus,
)
from app.domain.money import format_inr
from app.domain.timeline import is_overdue, sort_tasks
from app.domain.vendors import ALLOWED_STATUS_CHANGES
from app.persistence.repositories import StoredEvent, StoredProposal
from app.services.alternatives import AlternativesView
from app.services.change_manager import ChangeOutcome, OutcomeKind

EVENT_TYPE_LABELS: Mapping[EventType, str] = {
    EventType.WEDDING: "Wedding",
    EventType.BIRTHDAY: "Birthday",
    EventType.COMMUNITY: "Community event",
}
REASON_LABELS: Mapping[ChangeReason, str] = {
    ChangeReason.UNCHANGED: "No change",
    ChangeReason.ADDED: "New budget line",
    ChangeReason.PROTECTED: "Protected, kept as it is",
    ChangeReason.GUEST_SCALED: "Scaled for the new guest count",
    ChangeReason.CUT_FIRST: "Cut first, as you asked",
    ChangeReason.PROPORTIONAL: "Adjusted in proportion",
    ChangeReason.FLOOR_REACHED: "Held at its protected expenses",
}
TASK_SOURCE_LABELS: Mapping[TaskSource, str] = {
    TaskSource.CATALOG: "Standard step",
    TaskSource.AI: "Suggested by AI",
    TaskSource.USER: "Added by you",
    TaskSource.CHANGE: "Follow-up from a change",
}
VENDOR_STATUS_LABELS: Mapping[VendorStatus, str] = {
    VendorStatus.TODO: "To contact",
    VendorStatus.CONTACTED: "Contacted",
    VendorStatus.BOOKED: "Booked",
}
FIELD_LABELS: Mapping[str, str] = {
    "total_budget_inr": "Total budget",
    "guest_count": "Guests",
    "event_date": "Event date",
}


def format_date(value: date) -> str:
    """Readable date, for example `12 Dec 2026`.

    Examples:
        >>> format_date(date(2026, 12, 2))
        '2 Dec 2026'
    """
    return f"{value.day} {value:%b %Y}"


@dataclass(frozen=True)
class FormState:
    """Submitted values and per-field error messages, so a form can be shown again as typed."""

    values: Mapping[str, Any] = field(default_factory=dict)
    errors: Mapping[str, str] = field(default_factory=dict)


def form_errors(error: ValidationError) -> dict[str, str]:
    """A plain-language message per field from a Pydantic error (first message wins).

    Examples:
        >>> from pydantic import BaseModel, Field
        >>> class Form(BaseModel):
        ...     guests: int = Field(ge=1)
        >>> try:
        ...     Form.model_validate({"guests": "0"})
        ... except ValidationError as e:
        ...     form_errors(e)
        {'guests': 'Must be at least 1.'}
    """
    messages: dict[str, str] = {}
    for item in error.errors():
        name = str(item["loc"][0]) if item["loc"] else "form"
        messages.setdefault(name, _friendly(item["type"], item.get("ctx") or {}, item["msg"]))
    return messages


def _friendly(error_type: str, ctx: Mapping[str, Any], fallback: str) -> str:
    """Plain wording for the validation errors users can trigger from forms."""
    if error_type in {"missing", "string_too_short", "too_short"} and ctx.get("min_length", 1) <= 1:
        return "This field is required."
    messages = {
        "string_too_short": f"Use at least {ctx.get('min_length')} characters.",
        "string_too_long": f"Use at most {ctx.get('max_length')} characters.",
        "too_long": f"Use at most {ctx.get('max_length')} characters.",
        "greater_than_equal": f"Must be at least {ctx.get('ge')}.",
        "less_than_equal": f"Must be at most {ctx.get('le')}.",
        "int_parsing": "Enter a whole number.",
        "int_from_float": "Enter a whole number.",
        "date_from_datetime_parsing": "Enter a valid date.",
        "date_parsing": "Enter a valid date.",
        "enum": "Choose one of the options.",
        "literal_error": "Choose one of the options.",
    }
    return messages.get(error_type, fallback.removeprefix("Value error, ").rstrip(".") + ".")


def landing_context(events: Sequence[StoredEvent], form: FormState, today: date) -> dict[str, Any]:
    """Data for the landing page: the create form and this visitor's events."""
    return {
        "form": form,
        "today": today.isoformat(),
        "event_types": [(t.value, EVENT_TYPE_LABELS[t]) for t in EventType],
        "sub_events": [
            {
                "event_type": t.value,
                "label": EVENT_TYPE_LABELS[t],
                "options": [(s.id, s.name) for s in CATALOG[t].sub_events],
            }
            for t in EventType
        ],
        "events": [
            {
                "id": e.id,
                "title": e.plan.title,
                "type_label": EVENT_TYPE_LABELS[e.plan.event_type],
                "date": format_date(e.plan.event_date),
            }
            for e in events
        ],
    }


def dashboard_context(
    stored: StoredEvent,
    history: Sequence[StoredProposal],
    today: date,
    *,
    expense_form: FormState | None = None,
) -> dict[str, Any]:
    """Data for the event dashboard."""
    plan = stored.plan
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
        "expense_form": expense_form or FormState(),
        "categories_for_select": [(c.id, c.name) for c in plan.categories],
        "change_result": None,
        "alternatives": None,
    }


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
        "tasks": [_task_row(t, names, today) for t in sort_tasks(plan.tasks)],
        "vendors": [_vendor_row(v) for v in sorted(plan.vendors, key=lambda v: v.priority)],
    }


def proposal_view(stored: StoredProposal) -> dict[str, Any]:
    """Data for a change preview: before and after, with a reason for every number."""
    proposal = stored.proposal
    return {
        "id": stored.id,
        "request": stored.request_text,
        "status": stored.status.value,
        "feasible": proposal.feasible,
        "shortfall": format_inr(proposal.shortfall_inr) if proposal.shortfall_inr else None,
        "shortfall_inr": proposal.shortfall_inr,
        "fields": [
            {
                "label": FIELD_LABELS[f.field],
                "before": _field_value(f.before, f.field),
                "after": _field_value(f.after, f.field),
            }
            for f in proposal.field_changes
        ],
        "budget": [
            _budget_change_row(c, proposal)
            for c in proposal.budget_changes
            if c.delta_inr or c.reason is ChangeReason.PROTECTED
        ],
        "expenses": [
            {
                "action": c.action.value,
                "label": c.label,
                "before": format_inr(c.before_inr) if c.before_inr is not None else None,
                "after": format_inr(c.after_inr) if c.after_inr is not None else None,
            }
            for c in proposal.expense_changes
        ],
        "tasks": [_schedule_row(c) for c in proposal.task_changes],
        "vendors": [_schedule_row(c) for c in proposal.vendor_changes],
        "new_tasks": [
            {
                "title": t.title,
                "due": format_date(t.due_date),
                "source": TASK_SOURCE_LABELS[t.source],
            }
            for t in proposal.new_tasks
        ],
        "skipped": [{"label": s.label, "reason": s.reason.value} for s in proposal.skipped],
        "protected_kept": list(proposal.protected_kept),
        "summary_before": _summary_short(proposal.summary_before),
        "summary_after": _summary_short(proposal.summary_after) if proposal.summary_after else None,
    }


def history_row(stored: StoredProposal) -> dict[str, Any]:
    """One line of the change history."""
    return {
        "request": stored.request_text,
        "status": stored.status.value,
        "created": format_date(stored.created_at.date()),
    }


def ai_panel(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Provenance for the "How this was generated" panel, or None before personalization."""
    if record is None:
        return None
    provenance = record["provenance"]
    return {
        "provider": provenance["provider"] or "None (offline suggestions)",
        "fallback": provenance["fallback"],
        "cached": provenance["cached"],
        "latency_ms": provenance["latency_ms"],
        "prompt_version": provenance["prompt_version"],
        "attempts": [f"{a['provider']}: {a['outcome']}" for a in provenance["attempts"]],
        "request": provenance["request_payload"],
        "response": record["result"],
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
    }


def _summary_short(summary: BudgetSummary) -> dict[str, str | None]:
    return {
        "total": format_inr(summary.total_inr),
        "spent": format_inr(summary.spent_inr),
        "remaining": format_inr(summary.remaining_inr),
        "shortfall": format_inr(summary.shortfall_inr) if summary.is_over_budget else None,
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
    }


def _budget_change_row(change: CategoryChange, proposal: Proposal) -> dict[str, Any]:
    names = {c.id: c.name for c in proposal.after.categories} if proposal.after else {}
    return {
        "name": names.get(change.category_id, change.category_id),
        "before": format_inr(change.before_inr),
        "after": format_inr(change.after_inr),
        "delta": format_inr(change.delta_inr),
        "reason": REASON_LABELS[change.reason],
        "overcommitted": format_inr(change.overcommitted_by_inr)
        if change.overcommitted_by_inr
        else None,
    }


def _schedule_row(change: ScheduleChange) -> dict[str, Any]:
    return {
        "title": change.title,
        "before": format_date(change.before),
        "after": format_date(change.after),
        "flagged": change.flagged,
    }


def _field_value(value: int | date, field_name: str) -> str:
    if isinstance(value, date):
        return format_date(value)
    return format_inr(value) if field_name == "total_budget_inr" else str(value)


OUTCOME_TITLES: Mapping[str, str] = {
    "proposal": "Here is what would change",
    "clarification": "One question first",
    "off_topic": "That's outside this plan",
    "unavailable": "Use the change form instead",
    "invalid": "That change can't be made",
    "nothing_to_change": "Nothing to change",
}


def change_result_context(
    event_id: str,
    outcome: ChangeOutcome,
    categories: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Data for the Change Manager's answer: a preview, a question or a message."""
    provenance = outcome.provenance
    return {
        "event_id": event_id,
        "kind": outcome.kind.value,
        "title": OUTCOME_TITLES[outcome.kind.value],
        "message": outcome.message,
        "proposal": proposal_view(outcome.proposal) if outcome.proposal else None,
        "show_form": outcome.kind is OutcomeKind.UNAVAILABLE,
        "categories_for_select": list(categories),
        "ai_note": _ai_note(provenance) if provenance else None,
    }


def alternatives_context(event_id: str, view: AlternativesView) -> dict[str, Any]:
    """Data for priced alternatives."""
    return {
        "event_id": event_id,
        "shortfall": format_inr(view.shortfall_inr) if view.shortfall_inr else None,
        "summary": view.summary,
        "options": [
            {
                "category": o.category_name,
                "title": o.title,
                "why": o.why,
                "action": o.action.value,
                "level": o.level.value,
                "saving": format_inr(o.saving_inr),
                "shortfall_after": format_inr(o.shortfall_after_inr)
                if o.shortfall_after_inr
                else None,
            }
            for o in view.options
        ],
        "ai_note": _ai_note(view.provenance, "Suggested") if view.provenance else None,
    }


def _ai_note(provenance: Provenance, verb: str = "Interpreted") -> str:
    """One honest line on where an answer came from."""
    if provenance.fallback:
        return "Standard suggestions (not AI-generated)."
    source = "cached answer from " if provenance.cached else ""
    return f"{verb} by AI ({source}{provenance.provider}, {provenance.latency_ms} ms)."

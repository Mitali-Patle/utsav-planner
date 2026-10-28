"""View models: domain objects turned into simple values for templates.

All formatting and labelling happens here, in tested Python, so templates only lay out
ready-made values.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import groupby
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from app.ai.client import Provenance
from app.domain.budget import BudgetSummary, CategoryChange, ChangeReason, summarize
from app.domain.calendar import google_calendar_url
from app.domain.catalog import CATALOG, VENDOR_QUESTIONS
from app.domain.changes import Proposal, ScheduleChange, describe_saving
from app.domain.clock import INDIA_TZ
from app.domain.models import (
    MAX_DAYS_AFTER,
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
from app.domain.vendors import ALLOWED_STATUS_CHANGES, OWN_VENDOR_TYPE
from app.persistence.repositories import StoredEvent, StoredProposal
from app.services.alternatives import AlternativesView
from app.services.change_manager import ChangeOutcome, OutcomeKind

EVENT_TYPE_LABELS: Mapping[EventType, str] = {
    EventType.WEDDING: "Wedding",
    EventType.BIRTHDAY: "Birthday",
    EventType.COMMUNITY: "Community event",
}
EVENT_TYPE_HINTS: Mapping[EventType, str] = {
    EventType.WEDDING: "Haldi, mehendi, sangeet, the ceremony and the reception",
    EventType.BIRTHDAY: "Cake, games and return gifts, at home or at a venue",
    EventType.COMMUNITY: "Pujas, festivals and society events on a shared fund",
}
REASON_LABELS: Mapping[ChangeReason, str] = {
    ChangeReason.UNCHANGED: "No change",
    ChangeReason.ADDED: "New budget line",
    ChangeReason.PROTECTED: "Protected, kept as it is",
    ChangeReason.GUEST_SCALED: "Scaled for the new guest count",
    ChangeReason.CUT_FIRST: "Cut first, as you asked",
    ChangeReason.PROPORTIONAL: "Adjusted in proportion",
    ChangeReason.FLOOR_REACHED: "Held at its protected expenses",
    ChangeReason.SET: "Set to the amount you asked",
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


@dataclass(frozen=True)
class PlanForms:
    """The dashboard's add forms, each empty unless it is being shown again with errors."""

    expense: FormState = field(default_factory=FormState)
    task: FormState = field(default_factory=FormState)
    vendor: FormState = field(default_factory=FormState)


@dataclass(frozen=True)
class Example:
    """A ready-made event that fills the new-event form, so a first visit takes one click."""

    label: str
    days_ahead: int
    values: Mapping[str, Any]


EXAMPLES: Mapping[str, Example] = {
    "wedding": Example(
        "Wedding",
        120,
        {
            "event_type": "wedding",
            "title": "Priya and Arjun's wedding",
            "city": "Jaipur",
            "guest_count": "250",
            "total_budget_inr": "1500000",
            "preferences": "Rajasthani royal theme, vegetarian food, marigold decor and a "
            "sangeet with live dhol.",
            "sub_events": ["haldi", "mehendi", "sangeet", "ceremony", "reception"],
        },
    ),
    "birthday": Example(
        "Birthday",
        30,
        {
            "event_type": "birthday",
            "title": "Aarav's 8th birthday",
            "city": "Pune",
            "guest_count": "30",
            "total_budget_inr": "15000",
            "preferences": "Space theme, he loves rockets. At home, no plastic decorations.",
            "sub_events": ["kids_games"],
        },
    ),
    "community": Example(
        "Community event",
        60,
        {
            "event_type": "community",
            "title": "Shanti Nagar Ganeshotsav",
            "city": "Mumbai",
            "guest_count": "400",
            "total_budget_inr": "150000",
            "preferences": "Eco-friendly clay idol, no plastic, activities for elders and "
            "children. Funded by 120 households.",
            "sub_events": ["puja", "cultural_program", "community_meal", "procession"],
        },
    ),
}


def example_form(name: str | None, today: date) -> FormState:
    """The new-event form filled with an example, or empty for an unknown name.

    Examples:
        >>> example_form("birthday", date(2026, 10, 22)).values["event_date"]
        '2026-11-21'
        >>> example_form("unknown", date(2026, 10, 22)).values
        {}
    """
    example = EXAMPLES.get(name or "")
    if example is None:
        return FormState()
    event_date = (today + timedelta(days=example.days_ahead)).isoformat()
    return FormState(values={**example.values, "event_date": event_date})


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
        >>> try:
        ...     Form.model_validate({"guests": ""})
        ... except ValidationError as e:
        ...     form_errors(e)
        {'guests': 'This field is required.'}
    """
    messages: dict[str, str] = {}
    for item in error.errors():
        name = str(item["loc"][0]) if item["loc"] else "form"
        error_type = "missing" if item.get("input") == "" else item["type"]
        messages.setdefault(name, _friendly(error_type, item.get("ctx") or {}, item["msg"]))
    return messages


def _friendly(error_type: str, ctx: Mapping[str, Any], fallback: str) -> str:
    """Plain wording for the validation errors users can trigger from forms."""
    if error_type == "missing" or (
        error_type in {"string_too_short", "too_short"} and ctx.get("min_length", 1) <= 1
    ):
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


@dataclass(frozen=True)
class Slide:
    """One photo in the home page's moving panel, described for screen readers."""

    image: str
    alt: str


CELEBRATION_SLIDES: tuple[Slide, ...] = (
    Slide("diwali-diyas.jpg", "Clay diyas burning in a row at night for Diwali."),
    Slide(
        "birthday-party.jpg",
        "A birthday cake painted in yellow and blue icing, candles lit, among yellow balloons.",
    ),
    Slide(
        "ganeshotsav-pandal.jpg",
        "A Ganesh idol in a saffron and white pandal while a devotee offers aarti.",
    ),
)
"""Photos from Pexels, in public/static/img/celebrations/ (800 x 600 JPEG, under 500 KB)."""


CREATE_FIELDS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "event_type": ("Event type", "type-wedding"),
        "title": ("Event name", "f-title"),
        "city": ("City", "f-city"),
        "event_date": ("Event date", "f-event_date"),
        "guest_count": ("Number of guests", "f-guest_count"),
        "total_budget_inr": ("Total budget in rupees", "f-total_budget_inr"),
        "preferences": ("Preferences", "f-preferences"),
        "sub_events": ("Functions to include", "create-heading"),
    }
)
"""Each new-event field's label and the id its error summary links to."""


def error_links(errors: Mapping[str, str]) -> list[tuple[str, str]]:
    """The error summary: (link target, "Label: message") per field.

    Examples:
        >>> error_links({"city": "This field is required.", "form": "Try again."})
        [('f-city', 'City: This field is required.')]
        >>> error_links({"extra": "Not allowed."})
        [('create-heading', 'extra: Not allowed.')]
    """
    links = []
    for name, message in errors.items():
        if name == "form":
            continue
        label, target = CREATE_FIELDS.get(name, (name, "create-heading"))
        links.append((target, f"{label}: {message}"))
    return links


def landing_context(form: FormState, today: date) -> dict[str, Any]:
    """Data for the landing page: the create form and the celebration panel."""
    return {
        "form": form,
        "error_links": error_links(form.errors),
        "today": today.isoformat(),
        "examples": [(name, example.label) for name, example in EXAMPLES.items()],
        "event_types": [(t.value, EVENT_TYPE_LABELS[t], EVENT_TYPE_HINTS[t]) for t in EventType],
        "sub_events": [
            {
                "event_type": t.value,
                "label": EVENT_TYPE_LABELS[t],
                "options": [(s.id, s.name) for s in CATALOG[t].sub_events],
            }
            for t in EventType
        ],
        "slides": CELEBRATION_SLIDES,
    }


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
            {"label": label, "tasks": [_task_row(t, names, today) for t in tasks]}
            for label, tasks in groupby(sort_tasks(plan.tasks), key=lambda t: f"{t.due_date:%B %Y}")
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
        "protected_lines": [c.name for c in proposal.summary_before.categories if c.protected],
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
    """One line of the change history, with a short summary of what the change did."""
    return {
        "request": stored.request_text,
        "status": stored.status.value,
        "created": format_date(stored.created_at.astimezone(INDIA_TZ).date()),
        "changes": change_summary(stored.proposal),
    }


def change_summary(proposal: Proposal) -> list[str]:
    """Plain-language points for what a proposal changes, for the history."""
    points = [
        f"{FIELD_LABELS[f.field]} {_field_value(f.before, f.field)} → "
        f"{_field_value(f.after, f.field)}"
        for f in proposal.field_changes
    ]
    moved = [c for c in proposal.budget_changes if c.before_inr != c.after_inr]
    if moved:
        points.append(f"{len(moved)} budget line{'s' if len(moved) != 1 else ''} changed")
    for change in proposal.expense_changes:
        points.append(f"Expense {change.label}: {change.action.value}")
    if proposal.new_tasks:
        points.append(
            f"{len(proposal.new_tasks)} new task{'s' if len(proposal.new_tasks) != 1 else ''}"
        )
    return points


def ai_panel(record: Mapping[str, Any] | None, panel_id: str = "plan-ai") -> dict[str, Any] | None:
    """Data for a "How this was generated" panel, or None when no AI was asked.

    `panel_id` keeps element ids unique when a page shows more than one panel.
    """
    if record is None:
        return None
    provenance = record["provenance"]
    return {
        "id": panel_id,
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


QUESTION_NOTE = "Question from the AI. Nothing in your plan has changed."


def change_result_context(
    event_id: str,
    outcome: ChangeOutcome,
    categories: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Data for the Change Manager's answer: a preview, a question or a message.

    Only a preview carries the "How this was generated" panel; a question or a refusal
    changed nothing, so it gets one plain line instead.
    """
    provenance = outcome.provenance
    asks = outcome.kind is OutcomeKind.CLARIFICATION
    note = None
    if provenance:
        note = QUESTION_NOTE if asks and not provenance.fallback else _ai_note(provenance)
    return {
        "event_id": event_id,
        "kind": outcome.kind.value,
        "title": OUTCOME_TITLES[outcome.kind.value],
        "announce": OUTCOME_TITLES[outcome.kind.value]
        + (" Nothing changes until you approve it." if outcome.proposal else "."),
        "message": outcome.message,
        "proposal": proposal_view(outcome.proposal) if outcome.proposal else None,
        "show_form": outcome.kind is OutcomeKind.UNAVAILABLE,
        "categories_for_select": list(categories),
        "asks": asks,
        "ai_note": note,
        "ai": ai_panel(outcome.ai, "change-ai") if outcome.proposal else None,
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
                "category_id": o.category_id,
                "action": o.action.value,
                "level": o.level.value,
                "how": describe_saving(o.action, o.level),
                "applicable": o.applicable,
                "saving": format_inr(o.saving_inr),
                "shortfall_after": format_inr(o.shortfall_after_inr)
                if o.shortfall_after_inr
                else None,
            }
            for o in view.options
        ],
        "ai_note": _ai_note(view.provenance, "Suggested") if view.provenance else None,
        "ai": ai_panel(view.ai, "alternatives-ai"),
    }


def _ai_note(provenance: Provenance, verb: str = "Interpreted") -> str:
    """One honest line on where an answer came from."""
    if provenance.fallback:
        return "Standard suggestions (not AI-generated)."
    source = "cached answer from " if provenance.cached else ""
    return f"{verb} by AI ({source}{provenance.provider}, {provenance.latency_ms} ms)."

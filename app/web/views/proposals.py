"""Change previews, change history and priced ways to save."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.domain.budget import BudgetSummary
from app.domain.clock import INDIA_TZ
from app.domain.models import Task
from app.domain.money import format_inr
from app.domain.proposal import ExpenseChange, FieldChange, Proposal, ScheduleChange
from app.domain.rebalance import CategoryChange, ChangeReason
from app.domain.savings import describe_saving
from app.persistence.repositories import StoredProposal
from app.services.alternatives import AlternativesView
from app.services.change_manager import ChangeOutcome, OutcomeKind
from app.web.views.formatting import (
    FIELD_LABELS,
    REASON_LABELS,
    TASK_SOURCE_LABELS,
    ai_note,
    ai_panel,
    format_date,
)


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
        "fields": [_field_row(f) for f in proposal.field_changes],
        "budget": [
            _budget_change_row(c, proposal)
            for c in proposal.budget_changes
            if c.delta_inr or c.reason is ChangeReason.PROTECTED
        ],
        "expenses": [_expense_row(c) for c in proposal.expense_changes],
        "tasks": [_schedule_row(c) for c in proposal.task_changes],
        "vendors": [_schedule_row(c) for c in proposal.vendor_changes],
        "new_tasks": [_new_task_row(t) for t in proposal.new_tasks],
        "skipped": [{"label": s.label, "reason": s.reason.value} for s in proposal.skipped],
        "protected_kept": list(proposal.protected_kept),
        "summary_before": _summary_short(proposal.summary_before),
        "summary_after": _summary_short(proposal.summary_after) if proposal.summary_after else None,
    }


def _field_row(change: FieldChange) -> dict[str, str]:
    return {
        "label": FIELD_LABELS[change.field],
        "before": _field_value(change.before, change.field),
        "after": _field_value(change.after, change.field),
    }


def _expense_row(change: ExpenseChange) -> dict[str, str | None]:
    return {
        "action": change.action.value,
        "label": change.label,
        "before": format_inr(change.before_inr) if change.before_inr is not None else None,
        "after": format_inr(change.after_inr) if change.after_inr is not None else None,
    }


def _new_task_row(task: Task) -> dict[str, str]:
    return {
        "title": task.title,
        "due": format_date(task.due_date),
        "source": TASK_SOURCE_LABELS[task.source],
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


def _summary_short(summary: BudgetSummary) -> dict[str, str | None]:
    return {
        "total": format_inr(summary.total_inr),
        "spent": format_inr(summary.spent_inr),
        "remaining": format_inr(summary.remaining_inr),
        "shortfall": format_inr(summary.shortfall_inr) if summary.is_over_budget else None,
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
        note = QUESTION_NOTE if asks and not provenance.fallback else ai_note(provenance)
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
        "ai_note": ai_note(view.provenance, "Suggested") if view.provenance else None,
        "ai": ai_panel(view.ai, "alternatives-ai"),
    }

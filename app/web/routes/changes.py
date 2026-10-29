"""The Smart Change Manager routes.

Plain-language requests, the fallback form, approve, reject and priced alternatives.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Response, status
from pydantic import BaseModel, ValidationError

from app.domain.change_set import ChangeKind, ChangeOperation, ChangeSet
from app.domain.models import CategoryId
from app.domain.savings import SavingAction, SavingLevel
from app.services.change_manager import ApprovalStatus, ChangeOutcome, OutcomeKind
from app.web.deps import AILimitDep, Visit, VisitDep
from app.web.errors import is_htmx
from app.web.responses import Notice, after_post, render
from app.web.session import FlashKind
from app.web.views.dashboard import dashboard_context
from app.web.views.forms import form_errors
from app.web.views.proposals import alternatives_context, change_result_context

router = APIRouter(tags=["changes"])

CHANGE_RESULT = "partials/change_result.html"
RATE_LIMITED = (
    "You've made many AI requests in a short time. Use the change form below, "
    "or try again in a few minutes."
)
EMPTY_FORM = "Fill in at least one change."
SET_LINE_INCOMPLETE = "To set a budget line, choose the line and enter its new amount."
UNKNOWN_OPTION = "That suggestion is no longer available. Ask for suggestions again."
APPROVAL_STATUS_CODES = {
    ApprovalStatus.STALE: status.HTTP_409_CONFLICT,
    ApprovalStatus.ALREADY_DECIDED: status.HTTP_409_CONFLICT,
    ApprovalStatus.INFEASIBLE: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


class ChangeFormFields(BaseModel):
    """The structured change form as posted; empty fields mean "no change"."""

    total_budget_inr: str = ""
    guest_count: str = ""
    shift_days: str = ""
    protect_category: str = ""
    set_category: str = ""
    set_amount_inr: str = ""

    def filled(self) -> dict[str, str]:
        """Each field with surrounding spaces removed."""
        return {name: value.strip() for name, value in self.model_dump().items()}


class SavingFields(BaseModel):
    """A suggested saving, named by line, action and level (the amount is recomputed)."""

    category_id: str = ""
    action: str = ""
    level: str = ""


@router.post("/events/{event_id}/changes", include_in_schema=False)
def request_change(
    event_id: str,
    visit: VisitDep,
    ai_limit: AILimitDep,
    change_request: Annotated[str, Form(alias="request")] = "",
) -> Response:
    """Interpret a plain-language change. Nothing is saved until it is approved."""
    services, session = visit.services, visit.session
    if ai_limit.allow(visit.request):
        outcome = services.changes.request_change(session, event_id, change_request)
    else:
        stored = services.planning.get(session, event_id)
        outcome = ChangeOutcome(OutcomeKind.UNAVAILABLE, RATE_LIMITED, event=stored)
    return _show_outcome(visit, event_id, outcome)


@router.post("/events/{event_id}/changes/form", include_in_schema=False)
def change_form(
    event_id: str, visit: VisitDep, fields: Annotated[ChangeFormFields, Form()]
) -> Response:
    """The structured change form: works with no AI at all."""
    try:
        change_set, summary = _form_change_set(fields.filled())
    except ValidationError as error:
        message = " ".join(form_errors(error).values())
        outcome = ChangeOutcome(OutcomeKind.INVALID, message)
    except ValueError as error:
        outcome = ChangeOutcome(OutcomeKind.INVALID, str(error))
    else:
        outcome = visit.services.changes.propose_from_form(
            visit.session, event_id, change_set, summary
        )
    return _show_outcome(visit, event_id, outcome)


@router.post("/events/{event_id}/proposals/{proposal_id}/approve", include_in_schema=False)
def approve(event_id: str, proposal_id: str, visit: VisitDep) -> Response:
    """Apply a pending change: new plan version and history entry, together."""
    outcome = visit.services.changes.approve(visit.session, event_id, proposal_id)
    if outcome.event is not None:  # only an approval carries the saved plan
        message = f"Change approved. Your plan is now version {outcome.event.version}."
        return after_post(visit.request, f"/events/{event_id}", Notice(message))
    result = ChangeOutcome(OutcomeKind.INVALID, outcome.message)
    return _show_outcome(visit, event_id, result, APPROVAL_STATUS_CODES[outcome.status])


@router.post("/events/{event_id}/proposals/{proposal_id}/reject", include_in_schema=False)
def reject(event_id: str, proposal_id: str, visit: VisitDep) -> Response:
    """Reject a pending change; the plan stays exactly as it was."""
    decided = visit.services.changes.reject(visit.session, event_id, proposal_id)
    notice = (
        Notice("Change rejected. Your plan is unchanged.")
        if decided
        else Notice("This change was already decided.", "info")
    )
    return after_post(visit.request, f"/events/{event_id}", notice)


@router.post("/events/{event_id}/alternatives", include_in_schema=False)
def alternatives(event_id: str, visit: VisitDep, ai_limit: AILimitDep) -> Response:
    """Priced ways to close the plan's shortfall.

    Infeasible proposals are not offered alternatives: they fail because protected lines and
    committed money exceed the new total, which trimming other lines cannot fix; the preview
    explains what would make them fit instead.
    """
    view = visit.services.alternatives.suggest(
        visit.session, event_id, ai_allowed=lambda: ai_limit.allow(visit.request)
    )
    context = alternatives_context(event_id, view)
    if is_htmx(visit.request):
        return render(visit.request, "partials/alternatives.html", {"alts": context})
    return _dashboard(visit, event_id, {"alternatives": context})


@router.post("/events/{event_id}/alternatives/apply", include_in_schema=False)
def apply_alternative(
    event_id: str, visit: VisitDep, fields: Annotated[SavingFields, Form()]
) -> Response:
    """Preview a suggested saving as a change. Nothing is saved until it is approved."""
    try:
        saving_action, saving_level = SavingAction(fields.action), SavingLevel(fields.level)
    except ValueError:
        outcome = ChangeOutcome(OutcomeKind.INVALID, UNKNOWN_OPTION)
    else:
        outcome = visit.services.changes.propose_saving(
            visit.session, event_id, fields.category_id, saving_action, saving_level
        )
    return _show_outcome(visit, event_id, outcome)


def _form_change_set(fields: dict[str, str]) -> tuple[ChangeSet, str]:
    """Build operations from the filled-in fields of the structured form.

    Raises:
        ValueError: If no field was filled in or a number is not a whole number.
        ValidationError: If a value breaks an operation's limits.
    """
    operations: list[ChangeOperation] = []
    parts: list[str] = []
    if fields["protect_category"]:
        operations.append(
            ChangeOperation(
                kind=ChangeKind.PROTECT, category_id=CategoryId(fields["protect_category"])
            )
        )
        parts.append("keep a budget line as it is")
    if fields["set_category"] or fields["set_amount_inr"]:
        if not (fields["set_category"] and fields["set_amount_inr"]):
            raise ValueError(SET_LINE_INCOMPLETE)
        operations.append(
            ChangeOperation(
                kind=ChangeKind.SET_CATEGORY_BUDGET,
                category_id=CategoryId(fields["set_category"]),
                amount_inr=_whole_number(fields["set_amount_inr"]),
            )
        )
        parts.append("set a budget line's amount")
    numeric = (
        ("total_budget_inr", ChangeKind.SET_TOTAL_BUDGET, "amount_inr", "set the total budget"),
        ("guest_count", ChangeKind.SET_GUEST_COUNT, "guest_count", "change the guest count"),
        ("shift_days", ChangeKind.SHIFT_DATE_DAYS, "days", "move the event date"),
    )
    for name, kind, target, description in numeric:
        if fields[name]:
            operations.append(
                ChangeOperation.model_validate({"kind": kind, target: _whole_number(fields[name])})
            )
            parts.append(description)
    if not operations:
        raise ValueError(EMPTY_FORM)
    return ChangeSet(operations=tuple(operations)), "Form: " + ", ".join(parts)


def _whole_number(text: str) -> int:
    cleaned = text.replace(",", "").replace("₹", "").strip()
    sign = -1 if cleaned.startswith("-") else 1
    digits = cleaned.removeprefix("-")
    if not digits.isdecimal():
        raise ValueError("Use whole numbers, for example 12000 or -3.")
    return sign * int(digits)


def _show_outcome(
    visit: Visit, event_id: str, outcome: ChangeOutcome, status_code: int = status.HTTP_200_OK
) -> Response:
    stored = outcome.event or visit.services.planning.get(visit.session, event_id)
    categories = [(c.id, c.name) for c in stored.plan.categories]
    context = change_result_context(event_id, outcome, categories)
    if is_htmx(visit.request):
        kind: FlashKind = "error" if status_code >= status.HTTP_400_BAD_REQUEST else "info"
        notice = Notice(context["announce"], kind)
        return render(
            visit.request, CHANGE_RESULT, {"result": context, "notice": notice}, status_code
        )
    return _dashboard(visit, event_id, {"change_result": context}, status_code)


def _dashboard(
    visit: Visit, event_id: str, extra: dict[str, Any], status_code: int = status.HTTP_200_OK
) -> Response:
    """The full dashboard with an answer shown in place (no-JavaScript path)."""
    overview = visit.services.changes.overview(visit.session, event_id)
    context = {**dashboard_context(overview.event, overview.history, visit.today), **extra}
    return render(visit.request, "dashboard.html", context, status_code)

"""The Smart Change Manager routes.

Plain-language requests, the fallback form, approve, reject and priced alternatives.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response, status
from pydantic import ValidationError

from app.domain.changes import ChangeKind, ChangeOperation, ChangeSet
from app.domain.models import CategoryId
from app.services import Services
from app.services.change_manager import ApprovalStatus, ChangeOutcome, OutcomeKind
from app.web.deps import AILimitDep, ServicesDep, SessionDep, TodayDep
from app.web.errors import is_htmx
from app.web.responses import Notice, after_post, render
from app.web.session import FlashKind
from app.web.views import (
    alternatives_context,
    change_result_context,
    dashboard_context,
    form_errors,
)

router = APIRouter(tags=["changes"])

CHANGE_RESULT = "partials/change_result.html"
RATE_LIMITED = (
    "You've made many AI requests in a short time. Use the change form below, "
    "or try again in a few minutes."
)
EMPTY_FORM = "Fill in at least one change."
APPROVAL_STATUS_CODES = {
    ApprovalStatus.STALE: status.HTTP_409_CONFLICT,
    ApprovalStatus.ALREADY_DECIDED: status.HTTP_409_CONFLICT,
    ApprovalStatus.INFEASIBLE: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


@router.post("/events/{event_id}/changes", include_in_schema=False)
def request_change(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    ai_limit: AILimitDep,
    change_request: Annotated[str, Form(alias="request")] = "",
) -> Response:
    """Interpret a plain-language change. Nothing is saved until it is approved."""
    if ai_limit.allow(request):
        outcome = services.changes.request_change(session, event_id, change_request)
    else:
        services.planning.get(session, event_id)
        outcome = ChangeOutcome(OutcomeKind.UNAVAILABLE, RATE_LIMITED)
    return _show_outcome(request, services, session, event_id, today, outcome)


@router.post("/events/{event_id}/changes/form", include_in_schema=False)
def change_form(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    total_budget_inr: Annotated[str, Form()] = "",
    guest_count: Annotated[str, Form()] = "",
    shift_days: Annotated[str, Form()] = "",
    protect_category: Annotated[str, Form()] = "",
) -> Response:
    """The structured change form: works with no AI at all."""
    fields = {
        "total_budget_inr": total_budget_inr.strip(),
        "guest_count": guest_count.strip(),
        "shift_days": shift_days.strip(),
        "protect_category": protect_category.strip(),
    }
    try:
        change_set, summary = _form_change_set(fields)
    except ValidationError as error:
        message = " ".join(form_errors(error).values())
        outcome = ChangeOutcome(OutcomeKind.INVALID, message)
    except ValueError as error:
        outcome = ChangeOutcome(OutcomeKind.INVALID, str(error))
    else:
        outcome = services.changes.propose_from_form(session, event_id, change_set, summary)
    return _show_outcome(request, services, session, event_id, today, outcome)


@router.post("/events/{event_id}/proposals/{proposal_id}/approve", include_in_schema=False)
def approve(
    request: Request,
    event_id: str,
    proposal_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Apply a pending change: new plan version and history entry, together."""
    outcome = services.changes.approve(session, event_id, proposal_id)
    if outcome.status is ApprovalStatus.APPROVED and outcome.event is not None:
        message = f"Change approved. Your plan is now version {outcome.event.version}."
        return after_post(request, f"/events/{event_id}", Notice(message))
    result = ChangeOutcome(OutcomeKind.INVALID, outcome.message)
    return _show_outcome(
        request, services, session, event_id, today, result, APPROVAL_STATUS_CODES[outcome.status]
    )


@router.post("/events/{event_id}/proposals/{proposal_id}/reject", include_in_schema=False)
def reject(
    request: Request,
    event_id: str,
    proposal_id: str,
    services: ServicesDep,
    session: SessionDep,
) -> Response:
    """Reject a pending change; the plan stays exactly as it was."""
    decided = services.changes.reject(session, event_id, proposal_id)
    notice = (
        Notice("Change rejected. Your plan is unchanged.")
        if decided
        else Notice("This change was already decided.", "info")
    )
    return after_post(request, f"/events/{event_id}", notice)


@router.post("/events/{event_id}/alternatives", include_in_schema=False)
def alternatives(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    ai_limit: AILimitDep,
) -> Response:
    """Priced ways to close the plan's shortfall.

    Infeasible proposals are not offered alternatives: they fail because protected lines and
    committed money exceed the new total, which trimming other lines cannot fix; the preview
    explains what would make them fit instead.
    """
    view = services.alternatives.suggest(
        session, event_id, ai_allowed=lambda: ai_limit.allow(request)
    )
    context = alternatives_context(event_id, view)
    if is_htmx(request):
        return render(request, "partials/alternatives.html", {"alts": context})
    return _dashboard(request, services, session, event_id, today, {"alternatives": context})


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
    request: Request,
    services: Services,
    session: str,
    event_id: str,
    today: TodayDep,
    outcome: ChangeOutcome,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    stored = services.planning.get(session, event_id)
    categories = [(c.id, c.name) for c in stored.plan.categories]
    context = change_result_context(event_id, outcome, categories)
    if is_htmx(request):
        kind: FlashKind = "error" if status_code >= status.HTTP_400_BAD_REQUEST else "info"
        notice = Notice(context["announce"], kind)
        return render(request, CHANGE_RESULT, {"result": context, "notice": notice}, status_code)
    return _dashboard(
        request, services, session, event_id, today, {"change_result": context}, status_code
    )


def _dashboard(
    request: Request,
    services: Services,
    session: str,
    event_id: str,
    today: TodayDep,
    extra: dict[str, Any],
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """The full dashboard with an answer shown in place (no-JavaScript path)."""
    stored = services.planning.get(session, event_id)
    history = services.changes.history(session, event_id)
    context = {**dashboard_context(stored, history, today), **extra}
    return render(request, "dashboard.html", context, status_code)

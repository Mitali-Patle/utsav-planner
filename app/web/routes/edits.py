"""Direct edits from dashboard forms: expenses, protection, tasks and vendors.

Each returns the refreshed plan area to htmx, or redirects a plain form post back to the
dashboard with a status message.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from pydantic import ValidationError

from app.domain.budget import NewExpense
from app.domain.errors import InvalidInputError
from app.domain.models import CategoryId, ExpenseId, TaskId, VendorId, VendorStatus
from app.persistence.repositories import StoredEvent
from app.services import Services
from app.web.deps import ServicesDep, SessionDep, TodayDep
from app.web.errors import is_htmx
from app.web.responses import Notice, after_post, render
from app.web.views import FormState, dashboard_context, form_errors

router = APIRouter(tags=["edits"])

PLAN_AREA = "partials/plan_area.html"


@router.post("/events/{event_id}/expenses", include_in_schema=False)
def add_expense(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    category_id: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
    amount_inr: Annotated[str, Form()] = "",
    protected: Annotated[str | None, Form()] = None,
) -> Response:
    """Add an expense; invalid input is shown next to the form with the values kept."""
    values = {
        "category_id": category_id,
        "label": label,
        "amount_inr": amount_inr,
        "protected": bool(protected),
    }
    try:
        draft = NewExpense.model_validate(values)
        stored = services.edits.add_expense(session, event_id, draft)
    except ValidationError as error:
        return _invalid_expense(
            request, services, session, event_id, today, FormState(values, form_errors(error))
        )
    except InvalidInputError as error:
        return _invalid_expense(
            request,
            services,
            session,
            event_id,
            today,
            FormState(values, {"amount_inr": error.message}),
        )
    return _plan_updated(request, stored, today, Notice(f"Added {draft.label}."))


@router.post("/events/{event_id}/expenses/{expense_id}/update", include_in_schema=False)
def update_expense(
    request: Request,
    event_id: str,
    expense_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    amount_inr: Annotated[str, Form()] = "",
) -> Response:
    """Change an expense's amount."""
    if not amount_inr.strip().isdecimal():
        raise InvalidInputError("Enter the amount in whole rupees, for example 2500.")
    stored = services.edits.update_expense(
        session, event_id, ExpenseId(expense_id), int(amount_inr)
    )
    return _plan_updated(request, stored, today, Notice("Expense updated."))


@router.post("/events/{event_id}/expenses/{expense_id}/delete", include_in_schema=False)
def delete_expense(
    request: Request,
    event_id: str,
    expense_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Remove an expense."""
    stored = services.edits.remove_expense(session, event_id, ExpenseId(expense_id))
    return _plan_updated(request, stored, today, Notice("Expense removed."))


@router.post("/events/{event_id}/expenses/{expense_id}/{action}", include_in_schema=False)
def protect_expense(
    request: Request,
    event_id: str,
    expense_id: str,
    action: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Protect or unprotect an expense (`action` is `protect` or `unprotect`)."""
    protected = _protect_flag(action)
    stored = services.edits.protect_expense(
        session, event_id, ExpenseId(expense_id), protected=protected
    )
    return _plan_updated(request, stored, today, Notice(_protect_message(protected)))


@router.post("/events/{event_id}/categories/{category_id}/{action}", include_in_schema=False)
def protect_category(
    request: Request,
    event_id: str,
    category_id: str,
    action: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Protect or unprotect a budget line (`action` is `protect` or `unprotect`)."""
    protected = _protect_flag(action)
    stored = services.edits.protect_category(
        session, event_id, CategoryId(category_id), protected=protected
    )
    return _plan_updated(request, stored, today, Notice(_protect_message(protected)))


@router.post("/events/{event_id}/tasks/{task_id}/{action}", include_in_schema=False)
def set_task(
    request: Request,
    event_id: str,
    task_id: str,
    action: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Tick (`done`) or untick (`undone`) a task."""
    if action not in {"done", "undone"}:
        raise InvalidInputError("Unknown task action.")
    done = action == "done"
    stored = services.edits.set_task_done(session, event_id, TaskId(task_id), done=done)
    return _plan_updated(request, stored, today, Notice("Task done." if done else "Task reopened."))


@router.post("/events/{event_id}/vendors/{vendor_id}/status", include_in_schema=False)
def set_vendor(
    request: Request,
    event_id: str,
    vendor_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    status_value: Annotated[str, Form(alias="status")] = "",
) -> Response:
    """Move a vendor to a new booking status."""
    try:
        new_status = VendorStatus(status_value)
    except ValueError as error:
        raise InvalidInputError("Choose a booking status from the list.") from error
    stored = services.edits.set_vendor_status(session, event_id, VendorId(vendor_id), new_status)
    return _plan_updated(request, stored, today, Notice("Vendor status updated."))


def _plan_updated(
    request: Request, stored: StoredEvent, today: TodayDep, notice: Notice
) -> Response:
    return after_post(
        request, f"/events/{stored.id}", notice, PLAN_AREA, dashboard_context(stored, [], today)
    )


def _invalid_expense(
    request: Request,
    services: Services,
    session: str,
    event_id: str,
    today: TodayDep,
    form: FormState,
) -> Response:
    if is_htmx(request):
        stored = services.planning.get(session, event_id)
        context = dashboard_context(stored, [], today, expense_form=form)
        return render(request, PLAN_AREA, context, status.HTTP_422_UNPROCESSABLE_CONTENT)
    overview = services.changes.overview(session, event_id)
    context = dashboard_context(overview.event, overview.history, today, expense_form=form)
    return render(request, "dashboard.html", context, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _protect_flag(action: str) -> bool:
    if action not in {"protect", "unprotect"}:
        raise InvalidInputError("Unknown protection action.")
    return action == "protect"


def _protect_message(protected: bool) -> str:
    return "Protected: changes will keep it as it is." if protected else "Protection removed."

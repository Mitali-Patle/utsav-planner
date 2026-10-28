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
from app.domain.timeline import NewTask
from app.domain.vendors import NewVendor
from app.persistence.repositories import StoredEvent
from app.services import Services
from app.web.deps import ServicesDep, SessionDep, TodayDep
from app.web.errors import is_htmx
from app.web.responses import Notice, after_post, render
from app.web.views import FormState, PlanForms, dashboard_context, form_errors

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
        form = FormState(values, form_errors(error))
        return _invalid_form(request, services, session, event_id, today, PlanForms(expense=form))
    except InvalidInputError as error:
        form = FormState(values, {"amount_inr": error.message})
        return _invalid_form(request, services, session, event_id, today, PlanForms(expense=form))
    return _plan_updated(request, stored, today, Notice(f"Added {draft.label}."))


@router.post("/events/{event_id}/tasks", include_in_schema=False)
def add_task(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    title: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
) -> Response:
    """Add the user's own task to the timeline; invalid input is shown next to the form."""
    values = {"title": title, "due_date": due_date}
    try:
        draft = NewTask.model_validate(values)
        stored = services.edits.add_task(session, event_id, draft, today)
    except ValidationError as error:
        form = FormState(values, form_errors(error))
        return _invalid_form(request, services, session, event_id, today, PlanForms(task=form))
    except InvalidInputError as error:
        form = FormState(values, {"due_date": error.message})
        return _invalid_form(request, services, session, event_id, today, PlanForms(task=form))
    return _plan_updated(request, stored, today, Notice(f"Added {draft.title} to the timeline."))


@router.post("/events/{event_id}/vendors", include_in_schema=False)
def add_vendor(
    request: Request,
    event_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
    name: Annotated[str, Form()] = "",
    book_by: Annotated[str, Form()] = "",
) -> Response:
    """Add the user's own vendor to the checklist; invalid input is shown next to the form."""
    values = {"name": name, "book_by": book_by}
    try:
        draft = NewVendor.model_validate(values)
        stored = services.edits.add_vendor(session, event_id, draft, today)
    except ValidationError as error:
        form = FormState(values, form_errors(error))
        return _invalid_form(request, services, session, event_id, today, PlanForms(vendor=form))
    except InvalidInputError as error:
        form = FormState(values, {"book_by": error.message})
        return _invalid_form(request, services, session, event_id, today, PlanForms(vendor=form))
    return _plan_updated(request, stored, today, Notice(f"Added {draft.name} to the checklist."))


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


@router.post("/events/{event_id}/tasks/{task_id}/remove", include_in_schema=False)
def remove_task(
    request: Request,
    event_id: str,
    task_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Remove a task the user added."""
    stored = services.edits.remove_task(session, event_id, TaskId(task_id))
    return _plan_updated(request, stored, today, Notice("Task removed."))


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


@router.post("/events/{event_id}/vendors/{vendor_id}/remove", include_in_schema=False)
def remove_vendor(
    request: Request,
    event_id: str,
    vendor_id: str,
    services: ServicesDep,
    session: SessionDep,
    today: TodayDep,
) -> Response:
    """Remove a vendor the user added."""
    stored = services.edits.remove_vendor(session, event_id, VendorId(vendor_id))
    return _plan_updated(request, stored, today, Notice("Vendor removed."))


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


def _invalid_form(
    request: Request,
    services: Services,
    session: str,
    event_id: str,
    today: TodayDep,
    forms: PlanForms,
) -> Response:
    """The plan with one add form's rejected input and errors shown next to it."""
    if is_htmx(request):
        stored = services.planning.get(session, event_id)
        context = dashboard_context(stored, [], today, forms=forms)
        return render(request, PLAN_AREA, context, status.HTTP_422_UNPROCESSABLE_CONTENT)
    overview = services.changes.overview(session, event_id)
    context = dashboard_context(overview.event, overview.history, today, forms=forms)
    return render(request, "dashboard.html", context, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _protect_flag(action: str) -> bool:
    if action not in {"protect", "unprotect"}:
        raise InvalidInputError("Unknown protection action.")
    return action == "protect"


def _protect_message(protected: bool) -> str:
    return "Protected: changes will keep it as it is." if protected else "Protection removed."

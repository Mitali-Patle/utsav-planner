"""Direct edits from dashboard forms: expenses, protection, tasks and vendors.

Each returns the refreshed plan area to htmx, or redirects a plain form post back to the
dashboard with a status message.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Response, status
from pydantic import BaseModel, ValidationError

from app.domain.budget import NewExpense
from app.domain.errors import InvalidInputError
from app.domain.models import CategoryId, ExpenseId, TaskId, VendorId, VendorStatus
from app.domain.timeline import NewTask
from app.domain.vendors import NewVendor
from app.persistence.repositories import StoredEvent
from app.web.deps import Visit, VisitDep
from app.web.errors import is_htmx
from app.web.responses import Notice, after_post, render
from app.web.views.dashboard import dashboard_context
from app.web.views.forms import FormState, PlanForms, form_errors

router = APIRouter(tags=["edits"])

PLAN_AREA = "partials/plan_area.html"


class ExpenseFields(BaseModel):
    """The add-expense form as posted: raw text, validated by `NewExpense`."""

    category_id: str = ""
    label: str = ""
    amount_inr: str = ""
    protected: str | None = None


class TaskFields(BaseModel):
    """The add-to-timeline form as posted."""

    title: str = ""
    due_date: str = ""


class VendorFields(BaseModel):
    """The add-a-vendor form as posted."""

    name: str = ""
    book_by: str = ""


@router.post("/events/{event_id}/expenses", include_in_schema=False)
def add_expense(
    event_id: str, visit: VisitDep, fields: Annotated[ExpenseFields, Form()]
) -> Response:
    """Add an expense; invalid input is shown next to the form with the values kept."""
    values = {**fields.model_dump(), "protected": bool(fields.protected)}
    try:
        draft = NewExpense.model_validate(values)
        stored = visit.services.edits.add_expense(visit.session, event_id, draft)
    except ValidationError as error:
        rejected = FormState(values, form_errors(error))
        return _invalid_form(visit, event_id, PlanForms(expense=rejected))
    except InvalidInputError as error:
        rejected = FormState(values, {"amount_inr": error.message})
        return _invalid_form(visit, event_id, PlanForms(expense=rejected))
    return _plan_updated(visit, stored, Notice(f"Added {draft.label}."))


@router.post("/events/{event_id}/tasks", include_in_schema=False)
def add_task(event_id: str, visit: VisitDep, fields: Annotated[TaskFields, Form()]) -> Response:
    """Add the user's own task to the timeline; invalid input is shown next to the form."""
    values = fields.model_dump()
    try:
        draft = NewTask.model_validate(values)
        stored = visit.services.edits.add_task(visit.session, event_id, draft, visit.today)
    except ValidationError as error:
        rejected = FormState(values, form_errors(error))
        return _invalid_form(visit, event_id, PlanForms(task=rejected))
    except InvalidInputError as error:
        rejected = FormState(values, {"due_date": error.message})
        return _invalid_form(visit, event_id, PlanForms(task=rejected))
    return _plan_updated(visit, stored, Notice(f"Added {draft.title} to the timeline."))


@router.post("/events/{event_id}/vendors", include_in_schema=False)
def add_vendor(event_id: str, visit: VisitDep, fields: Annotated[VendorFields, Form()]) -> Response:
    """Add the user's own vendor to the checklist; invalid input is shown next to the form."""
    values = fields.model_dump()
    try:
        draft = NewVendor.model_validate(values)
        stored = visit.services.edits.add_vendor(visit.session, event_id, draft, visit.today)
    except ValidationError as error:
        rejected = FormState(values, form_errors(error))
        return _invalid_form(visit, event_id, PlanForms(vendor=rejected))
    except InvalidInputError as error:
        rejected = FormState(values, {"book_by": error.message})
        return _invalid_form(visit, event_id, PlanForms(vendor=rejected))
    return _plan_updated(visit, stored, Notice(f"Added {draft.name} to the checklist."))


@router.post("/events/{event_id}/expenses/{expense_id}/update", include_in_schema=False)
def update_expense(
    event_id: str, expense_id: str, visit: VisitDep, amount_inr: Annotated[str, Form()] = ""
) -> Response:
    """Change an expense's amount."""
    if not amount_inr.strip().isdecimal():
        raise InvalidInputError("Enter the amount in whole rupees, for example 2500.")
    stored = visit.services.edits.update_expense(
        visit.session, event_id, ExpenseId(expense_id), int(amount_inr)
    )
    return _plan_updated(visit, stored, Notice("Expense updated."))


@router.post("/events/{event_id}/expenses/{expense_id}/delete", include_in_schema=False)
def delete_expense(event_id: str, expense_id: str, visit: VisitDep) -> Response:
    """Remove an expense."""
    stored = visit.services.edits.remove_expense(visit.session, event_id, ExpenseId(expense_id))
    return _plan_updated(visit, stored, Notice("Expense removed."))


@router.post("/events/{event_id}/expenses/{expense_id}/{action}", include_in_schema=False)
def protect_expense(event_id: str, expense_id: str, action: str, visit: VisitDep) -> Response:
    """Protect or unprotect an expense (`action` is `protect` or `unprotect`)."""
    protected = _protect_flag(action)
    stored = visit.services.edits.protect_expense(
        visit.session, event_id, ExpenseId(expense_id), protected=protected
    )
    return _plan_updated(visit, stored, Notice(_protect_message(protected)))


@router.post("/events/{event_id}/categories/{category_id}/{action}", include_in_schema=False)
def protect_category(event_id: str, category_id: str, action: str, visit: VisitDep) -> Response:
    """Protect or unprotect a budget line (`action` is `protect` or `unprotect`)."""
    protected = _protect_flag(action)
    stored = visit.services.edits.protect_category(
        visit.session, event_id, CategoryId(category_id), protected=protected
    )
    return _plan_updated(visit, stored, Notice(_protect_message(protected)))


@router.post("/events/{event_id}/tasks/{task_id}/remove", include_in_schema=False)
def remove_task(event_id: str, task_id: str, visit: VisitDep) -> Response:
    """Remove a task the user added."""
    stored = visit.services.edits.remove_task(visit.session, event_id, TaskId(task_id))
    return _plan_updated(visit, stored, Notice("Task removed."))


@router.post("/events/{event_id}/tasks/{task_id}/{action}", include_in_schema=False)
def set_task(event_id: str, task_id: str, action: str, visit: VisitDep) -> Response:
    """Tick (`done`) or untick (`undone`) a task."""
    if action not in {"done", "undone"}:
        raise InvalidInputError("Unknown task action.")
    done = action == "done"
    stored = visit.services.edits.set_task_done(visit.session, event_id, TaskId(task_id), done=done)
    return _plan_updated(visit, stored, Notice("Task done." if done else "Task reopened."))


@router.post("/events/{event_id}/vendors/{vendor_id}/remove", include_in_schema=False)
def remove_vendor(event_id: str, vendor_id: str, visit: VisitDep) -> Response:
    """Remove a vendor the user added."""
    stored = visit.services.edits.remove_vendor(visit.session, event_id, VendorId(vendor_id))
    return _plan_updated(visit, stored, Notice("Vendor removed."))


@router.post("/events/{event_id}/vendors/{vendor_id}/status", include_in_schema=False)
def set_vendor(
    event_id: str,
    vendor_id: str,
    visit: VisitDep,
    status_value: Annotated[str, Form(alias="status")] = "",
) -> Response:
    """Move a vendor to a new booking status."""
    try:
        new_status = VendorStatus(status_value)
    except ValueError as error:
        raise InvalidInputError("Choose a booking status from the list.") from error
    stored = visit.services.edits.set_vendor_status(
        visit.session, event_id, VendorId(vendor_id), new_status
    )
    return _plan_updated(visit, stored, Notice("Vendor status updated."))


def _plan_updated(visit: Visit, stored: StoredEvent, notice: Notice) -> Response:
    context = dashboard_context(stored, [], visit.today)
    return after_post(visit.request, f"/events/{stored.id}", notice, PLAN_AREA, context)


def _invalid_form(visit: Visit, event_id: str, forms: PlanForms) -> Response:
    """The plan with one add form's rejected input and errors shown next to it."""
    services, session = visit.services, visit.session
    if is_htmx(visit.request):
        stored = services.planning.get(session, event_id)
        context = dashboard_context(stored, [], visit.today, forms=forms)
        return render(visit.request, PLAN_AREA, context, status.HTTP_422_UNPROCESSABLE_CONTENT)
    overview = services.changes.overview(session, event_id)
    context = dashboard_context(overview.event, overview.history, visit.today, forms=forms)
    return render(visit.request, "dashboard.html", context, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _protect_flag(action: str) -> bool:
    if action not in {"protect", "unprotect"}:
        raise InvalidInputError("Unknown protection action.")
    return action == "protect"


def _protect_message(protected: bool) -> str:
    return "Protected: changes will keep it as it is." if protected else "Protection removed."

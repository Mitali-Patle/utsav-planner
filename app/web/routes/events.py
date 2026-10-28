"""Create, show, personalize and export events, and ask the AI for different ideas."""

from typing import Annotated

from fastapi import APIRouter, Form, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from app.domain.calendar import plan_calendar
from app.domain.catalog import CATALOG
from app.domain.errors import InvalidInputError
from app.domain.models import EventType
from app.domain.planning import EventDetails
from app.services.planning import IdeasOutcome
from app.web.deps import AILimitDep, CreateLimitDep, ServicesDep, SessionDep, Visit, VisitDep
from app.web.responses import Notice, after_post, render
from app.web.views.dashboard import dashboard_context
from app.web.views.forms import FormState, form_errors
from app.web.views.landing import landing_context

router = APIRouter(tags=["events"])

TOO_MANY_EVENTS = (
    "You've created many plans in a short time from this connection. "
    "Please try again in an hour, or keep working on your existing plans below."
)
PERSONALIZED = Notice("Your plan has been personalized. Each AI suggestion says why.")
FALLBACK = Notice(
    "The AI assistant is unavailable right now, so these are standard ideas (not AI-generated).",
    "info",
)
LIMITED = Notice(
    "You've made many AI requests in a short time, so these are standard ideas "
    "(not AI-generated). Try again in a few minutes.",
    "info",
)

NEW_IDEAS = {
    IdeasOutcome.NEW: Notice("Here are different ideas, each with a reason."),
    IdeasOutcome.UNAVAILABLE: Notice(
        "The AI couldn't suggest different ideas right now, so your ideas are unchanged. "
        "Please try again later.",
        "info",
    ),
    IdeasOutcome.LIMITED: Notice(
        "You've made many AI requests in a short time, so your ideas are unchanged. "
        "Try again in a few minutes.",
        "info",
    ),
}


class EventFields(BaseModel):
    """The new-plan form as posted: raw text, validated by `EventDetails`."""

    event_type: str = ""
    title: str = ""
    city: str = ""
    event_date: str = ""
    guest_count: str = ""
    total_budget_inr: str = ""
    preferences: str = ""
    sub_events: list[str] = []


@router.post("/events", include_in_schema=False)
def create_event(
    visit: VisitDep, create_limit: CreateLimitDep, fields: Annotated[EventFields, Form()]
) -> Response:
    """Save the code-built draft at once and open its dashboard.

    The form lists functions for every event type; only those of the chosen type are kept.
    Invalid input re-renders the form with the user's values and a message per field.
    """
    values = fields.model_dump()
    chosen = _sub_events_for(fields.event_type, fields.sub_events)
    try:
        details = EventDetails.model_validate({**values, "sub_events": chosen})
        if not create_limit.allow(visit.request):
            too_many = FormState(values, {"form": TOO_MANY_EVENTS})
            return _invalid_form(visit, too_many, status.HTTP_429_TOO_MANY_REQUESTS)
        stored = visit.services.planning.create_draft(visit.session, details)
    except ValidationError as error:
        return _invalid_form(visit, FormState(values, form_errors(error)))
    except InvalidInputError as error:
        field = "event_date" if error.code.startswith("event_date") else "form"
        return _invalid_form(visit, FormState(values, {field: error.message}))
    return after_post(
        visit.request,
        f"/events/{stored.id}",
        Notice("Your plan is ready. The AI is personalizing it now."),
    )


@router.get("/events/{event_id}", response_class=HTMLResponse, include_in_schema=False)
def dashboard(event_id: str, visit: VisitDep) -> HTMLResponse:
    """The event dashboard."""
    overview = visit.services.changes.overview(visit.session, event_id)
    context = dashboard_context(overview.event, overview.history, visit.today)
    return render(visit.request, "dashboard.html", context)


@router.get("/events/{event_id}/calendar.ics", include_in_schema=False)
def calendar_file(event_id: str, services: ServicesDep, session: SessionDep) -> Response:
    """The event day, unfinished tasks and vendors still to book, as an `.ics` download."""
    stored = services.planning.get(session, event_id)
    return Response(
        plan_calendar(stored.plan, stored.id, stored.updated_at),
        media_type="text/calendar",
        headers={
            "Content-Disposition": 'attachment; filename="utsav-plan.ics"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/events/{event_id}/personalize", include_in_schema=False)
def personalize(event_id: str, visit: VisitDep, ai_limit: AILimitDep) -> Response:
    """Tailor the plan with AI, or with labelled standard ideas that can be retried later.

    The AI rate limit is consulted only when a model would actually be called.
    """
    answers: list[bool] = []

    def allowed() -> bool:
        answers.append(ai_limit.allow(visit.request))
        return answers[-1]

    stored = visit.services.planning.personalize(visit.session, event_id, ai_allowed=allowed)
    fallback = bool(stored.ai and stored.ai["provenance"]["fallback"])
    notice = (LIMITED if answers == [False] else FALLBACK) if fallback else PERSONALIZED
    return after_post(
        visit.request,
        f"/events/{event_id}",
        notice,
        "partials/plan_area.html",
        # The AI answer arrives seconds after the page; keep anything typed meanwhile.
        {**dashboard_context(stored, [], visit.today), "keep_typed_input": True},
    )


@router.post("/events/{event_id}/ideas", include_in_schema=False)
def new_ideas(event_id: str, visit: VisitDep, ai_limit: AILimitDep) -> Response:
    """Replace the ideas with different ones from AI; nothing else in the plan changes."""
    result = visit.services.planning.regenerate_ideas(
        visit.session, event_id, ai_allowed=lambda: ai_limit.allow(visit.request)
    )
    return after_post(
        visit.request,
        f"/events/{event_id}#ideas",
        NEW_IDEAS[result.outcome],
        "partials/plan_area.html",
        {
            **dashboard_context(result.event, [], visit.today),
            "keep_typed_input": True,
            "focus_ideas": True,
        },
    )


def _sub_events_for(event_type: str, submitted: list[str]) -> list[str]:
    valid_types = {t.value for t in EventType}
    if event_type not in valid_types:
        return []
    options = {s.id for s in CATALOG[EventType(event_type)].sub_events}
    return [s for s in submitted if s in options]


def _invalid_form(
    visit: Visit, form: FormState, status_code: int = status.HTTP_422_UNPROCESSABLE_CONTENT
) -> HTMLResponse:
    context = landing_context(form, visit.today)
    return render(visit.request, "landing.html", context, status_code)
